"""Memoria operativa entre proyectos: learning_facts deja de ser tabla muerta.

El sistema re-descubría las mismas fricciones en cada proyecto (CLI Notas
quemó un ciclo entero y una escalación para descubrir que el entorno no podía
ejecutar pytest; CLI Gastos habría tropezado igual). Este módulo destila
HECHOS OPERATIVOS estructurados al cierre del proyecto — nada de narración de
agentes, solo agregados de la DB — y los espeja en un almacén global para que
el Lead del siguiente proyecto arranque sabiéndolos.

Qué se destila (cada uno con su porqué):
- Fallos de infra recurrentes por proveedor: el router del próximo proyecto
  puede evitarlos o al menos no diagnosticarlos como fallo del equipo.
- Waivers de verificación runtime aceptados: si el entorno no pudo ejecutar
  tests una vez, probablemente tampoco pueda mañana — delega un test_runner
  builtin desde el principio.
- Escalaciones de modelo y recoveries de adapter: qué peldaño de la cascada
  hizo falta de verdad para cada rol.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from aiteam.policies import INFRA_ERROR_CODES, RUNTIME_VERIFICATION_WAIVER_REASON
from aiteam.user_config import user_config_dir

logger = logging.getLogger(__name__)

_GLOBAL_STORE = "learning_facts.json"
_GLOBAL_CAP = 50
_MIN_INFRA_FAILURES = 3


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0)
    conn.row_factory = sqlite3.Row
    return conn


def distill_learning_facts(db_path: Path) -> list[dict[str, Any]]:
    """Destila hechos del proyecto a ``learning_facts`` + espejo global.

    Idempotente por contenido: el id del hecho es un hash de su texto, así que
    cierres repetidos del mismo root no duplican filas.
    """
    facts: list[dict[str, Any]] = []
    try:
        with contextlib.closing(_connect(db_path)) as conn:
            goal_row = conn.execute("SELECT id FROM goals ORDER BY created_at ASC LIMIT 1").fetchone()
            goal_id = str(goal_row["id"]) if goal_row else None

            placeholders = ",".join("?" for _ in INFRA_ERROR_CODES)
            for row in conn.execute(
                f"""
                SELECT COALESCE(provider, adapter_type, '?') AS provider, error_code, COUNT(*) AS n
                FROM runs
                WHERE status = 'failed' AND error_code IN ({placeholders})
                GROUP BY COALESCE(provider, adapter_type, '?'), error_code
                HAVING n >= ?
                """,
                (*INFRA_ERROR_CODES, _MIN_INFRA_FAILURES),
            ):
                facts.append({
                    "kind": "infra",
                    "fact": (
                        f"El proveedor {row['provider']} falló {row['n']}x con "
                        f"{row['error_code']} en este proyecto"
                    ),
                    "confidence": min(0.9, 0.5 + 0.1 * int(row["n"])),
                })

            waiver = conn.execute(
                """
                SELECT COUNT(*) FROM issue_thread_interactions
                WHERE kind = 'request_confirmation' AND status = 'accepted'
                  AND json_extract(payload_json, '$.reason') = ?
                """,
                (RUNTIME_VERIFICATION_WAIVER_REASON,),
            ).fetchone()[0]
            if waiver:
                facts.append({
                    "kind": "environment",
                    "fact": (
                        "El entorno no pudo ejecutar la suite de tests y el usuario dispensó la "
                        "verificación runtime: delega un test_runner (builtin determinista) desde el inicio"
                    ),
                    "confidence": 0.8,
                })

            for row in conn.execute(
                """
                SELECT json_extract(payload_json, '$.from_model') AS from_model,
                       json_extract(payload_json, '$.to_model') AS to_model,
                       COUNT(*) AS n
                FROM activity_log
                WHERE action = 'issue.model_escalation'
                GROUP BY from_model, to_model
                """
            ):
                facts.append({
                    "kind": "routing",
                    "fact": (
                        f"El modelo {row['from_model'] or '?'} agotó intentos y requirió escalar a "
                        f"{row['to_model']} ({row['n']} issue(s))"
                    ),
                    "confidence": 0.7,
                })

            for row in conn.execute(
                """
                SELECT json_extract(payload_json, '$.failed_adapter_type') AS failed,
                       json_extract(payload_json, '$.new_adapter_type') AS new_type,
                       COUNT(*) AS n
                FROM activity_log
                WHERE action = 'issue.adapter_recovery'
                GROUP BY failed, new_type
                """
            ):
                facts.append({
                    "kind": "routing",
                    "fact": (
                        f"El adapter {row['failed']} agotó intentos sin evidencia y se recuperó "
                        f"cambiando a {row['new_type']} ({row['n']} issue(s))"
                    ),
                    "confidence": 0.7,
                })

            # Persistencia idempotente en la tabla del proyecto.
            for fact in facts:
                fact_id = "fact:" + hashlib.sha256(fact["fact"].encode("utf-8")).hexdigest()[:16]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO learning_facts (id, goal_id, fact, confidence, metadata_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        fact_id, goal_id, fact["fact"], float(fact["confidence"]),
                        json.dumps({"kind": fact["kind"]}, ensure_ascii=False),
                    ),
                )
            conn.commit()
    except sqlite3.Error:
        logger.warning("learning distillation failed for %s", db_path, exc_info=True)
        return []

    if facts:
        _mirror_globally(facts)
    return facts


def _mirror_globally(facts: list[dict[str, Any]]) -> None:
    """Merge (dedupe por texto) en el almacén global del usuario."""
    store = user_config_dir() / _GLOBAL_STORE
    try:
        existing = json.loads(store.read_text(encoding="utf-8")) if store.exists() else []
        if not isinstance(existing, list):
            existing = []
    except (OSError, json.JSONDecodeError):
        existing = []
    known = {str(item.get("fact")) for item in existing if isinstance(item, dict)}
    for fact in facts:
        if fact["fact"] not in known:
            existing.insert(0, {"fact": fact["fact"], "kind": fact["kind"], "confidence": fact["confidence"]})
            known.add(fact["fact"])
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(
            json.dumps(existing[:_GLOBAL_CAP], ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError:
        logger.warning("failed to write global learning store %s", store, exc_info=True)


def global_learning_facts(limit: int = 5) -> list[str]:
    """Top-N lecciones globales para inyectar en el intake del próximo proyecto."""
    store = user_config_dir() / _GLOBAL_STORE
    try:
        data = json.loads(store.read_text(encoding="utf-8")) if store.exists() else []
    except (OSError, json.JSONDecodeError):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, dict) and str(item.get("fact") or "").strip():
            out.append(str(item["fact"]))
        if len(out) >= limit:
            break
    return out
