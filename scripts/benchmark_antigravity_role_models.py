r"""Screening acotado de modelos Antigravity por contrato de rol.

Ejecuta una llamada independiente por candidato mediante ``agy --new-project``.
La rubrica es determinista y permanece en este harness, fuera del prompt. Este
screening sirve para elegir candidatos para una calibracion multi-semilla; una
sola muestra nunca autoriza cambiar defaults, gates ni cascadas.

Uso (consume cuota real de la suscripcion):
    .\scripts\python_local.bat scripts\benchmark_antigravity_role_models.py \
      --output benchmarks/results/model_calibration/antigravity-1.1.5-screening-seed-1.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Candidate:
    model: str
    role: str
    tier: int
    baseline: bool


MATRIX = (
    Candidate("gemini-3.1-pro-high", "lead", 1, True),
    Candidate("claude-opus-4-6-thinking", "lead", 1, False),
    Candidate("gemini-3.6-flash-high", "lead", 1, False),
    Candidate("gemini-3.5-flash-high", "coding", 2, True),
    Candidate("claude-sonnet-4-6", "coding", 2, False),
    Candidate("gemini-3.6-flash-high", "coding", 2, False),
    Candidate("gemini-3.5-flash-high", "review", 2, True),
    Candidate("gemini-3.5-flash-medium", "review", 2, False),
    Candidate("gemini-3.6-flash-medium", "review", 2, False),
    Candidate("gemini-3.1-pro-low", "review", 2, False),
    Candidate("gemini-3.5-flash-low", "scout", 3, True),
    Candidate("gemini-3.6-flash-low", "scout", 3, False),
    Candidate("gpt-oss-120b-medium", "scout", 3, False),
)

BASELINES = {
    item.role: item.model for item in MATRIX if item.baseline
}


COMMON = """Responde EXCLUSIVAMENTE con un objeto JSON valido, sin Markdown.
No uses herramientas, no modifiques archivos y no inventes hechos que no esten
en el caso. La respuesta debe ser autocontenida y concisa."""


PROMPTS = {
    "lead": COMMON + """

ROL: Lead de un control plane multiagente.
CASO: Hay que migrar una cola de issues de memoria a SQLite sin detener las runs.
Cada issue pertenece a un tenant. Dos workers pueden competir por el checkout.
El despliegue debe poder revertirse y la evidencia debe permitir decidir go/no-go.

Devuelve estas claves:
{
  "objective": "...",
  "assumptions": ["..."],
  "work_items": [{"owner":"...","deliverable":"...","accepted_by":"...","depends_on":[]}],
  "risks": [{"risk":"...","mitigation":"..."}],
  "rollback": "...",
  "verification": [{"metric":"...","threshold":"...","window":"...","action":"..."}],
  "escalation": "..."
}
""",
    "coding": COMMON + """

ROL: software engineer. No ejecutes el codigo: propone un cambio aplicable.
CASO: Este checkout contiene cuatro defectos: mezcla tenants, interpola SQL,
permite carrera entre SELECT y UPDATE y no comprueba si gano la reserva.

def checkout(conn, tenant_id, worker_id):
    row = conn.execute(
        f"SELECT id FROM issues WHERE status='todo' ORDER BY priority LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE issues SET status='in_progress', lease_owner=? WHERE id=?",
                 (worker_id, row[0]))
    return row[0]

Devuelve:
{
  "approach": "...",
  "replacement_function": "codigo Python completo de checkout como string",
  "tests": ["..."],
  "failure_modes": ["..."]
}
La solucion debe parametrizar tenant_id, hacer reserva atomica y devolver None
si otro worker gano. No uses APIs externas ni cambies el esquema.
""",
    "review": COMMON + """

ROL: code reviewer independiente.
CASO: Revisa este flujo de cierre durable:

1 def close_issue(db, issue_id, actor):
2     issue = db.query("SELECT * FROM issues WHERE id=?", issue_id)
3     db.execute("UPDATE issues SET status='done' WHERE id=?", issue_id)
4     try:
5         db.insert("activity_log", {"issue_id": issue_id, "actor": actor})
6         parent = db.query("SELECT parent_id FROM issues WHERE id=?", issue_id)
7         enqueue_wakeup(parent, "child_done")
8     except Exception:
9         pass

Hechos: issue incluye tenant_id y assignee_agent_id; actor tambien tiene tenant y
agent_id. El cierre solo es valido para el assignee del mismo tenant. Status y
activity deben ser atomicos. El wakeup requiere parent_id no nulo y clave
idempotente. Una excepcion no puede dejar done sin continuacion durable.

Devuelve:
{
  "verdict": "approve|changes_requested|blocked",
  "findings": [{"severity":"...","line":1,"cause":"...","fix":"..."}],
  "required_evidence": ["..."]
}
No premies estilo: prioriza defectos causales y especifica la correccion.
""",
    "scout": COMMON + """

ROL: scout/context curator. Extrae solo estado causal accionable.
CONTEXTO:
- Decision D1: SQLite sera la unica fuente de verdad; owner Lead; acepta Reviewer.
- Se considero Redis, pero se descarto por duplicar autoridad durable.
- Restriccion R1: ninguna run puede perder su wakeup al reiniciar.
- Riesgo K1: checkout duplicado entre workers. Mitigacion: transaccion atomica.
- Gate G1: 100 reinicios, cero wakeups perdidos durante 24 horas; si falla, rollback.
- Dependencia P1: migracion de runs debe terminar antes de retirar JSONL.
- Bloqueo B1: falta decidir la ventana de mantenimiento; escala al owner humano.
- Evidencia E1: suite oculta y auditoria SQLite deben estar verdes.
- Ruido: el nombre temporal fue Proyecto Aurora y hubo tres saludos sin decision.
- Estimacion descartada: hacerlo en dos horas sin pruebas.

Devuelve:
{
  "decisions": [{"id":"...","fact":"...","owner":"...","accepted_by":"..."}],
  "constraints": [{"id":"...","fact":"..."}],
  "risks": [{"id":"...","fact":"...","mitigation":"..."}],
  "gates": [{"id":"...","metric":"...","threshold":"...","window":"...","action":"..."}],
  "dependencies": [{"id":"...","fact":"..."}],
  "blockers": [{"id":"...","fact":"...","escalate_to":"..."}],
  "evidence": [{"id":"...","fact":"..."}]
}
Omite nombres temporales, saludos y estimaciones descartadas.
""",
}


ROLE_RULES: dict[str, dict[str, Any]] = {
    "lead": {
        "keys": ("objective", "assumptions", "work_items", "risks", "rollback", "verification", "escalation"),
        "anchors": (
            ("tenant",), ("atomic", "transaction", "race", "concurr"),
            ("rollback", "revert"), ("owner",), ("accepted", "accept"),
            ("threshold", "zero", "0"), ("window", "hour", "minute"),
            ("wakeup", "restart", "recovery"),
        ),
    },
    "coding": {
        "keys": ("approach", "replacement_function", "tests", "failure_modes"),
        "anchors": (
            ("tenant_id",), ("?", "parameter"), ("begin immediate", "transaction", "atomic"),
            ("rowcount", "returning", "changes()", "won", "winner"),
            ("rollback",), ("commit",), ("race", "concurr", "worker"),
        ),
    },
    "review": {
        "keys": ("verdict", "findings", "required_evidence"),
        "anchors": (
            ("tenant",), ("assignee", "actor"), ("atomic", "transaction"),
            ("exception", "rollback", "swallow"), ("parent", "null", "none"),
            ("idempot",), ("wakeup", "continuation"),
        ),
    },
    "scout": {
        "keys": ("decisions", "constraints", "risks", "gates", "dependencies", "blockers", "evidence"),
        "anchors": (
            ("d1", "sqlite"), ("r1", "wakeup"), ("k1", "checkout"),
            ("g1", "100"), ("24", "hour"), ("p1", "jsonl"),
            ("b1", "maintenance", "mantenimiento"), ("e1", "hidden", "oculta"),
            ("human", "humano"),
        ),
        "forbidden": ("aurora", "two hours", "dos horas"),
    },
}


def parse_json_output(raw: str) -> dict[str, Any]:
    text = raw.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("JSON object not found")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("top-level output must be an object")
    return value


def score_response(role: str, response: dict[str, Any]) -> dict[str, Any]:
    rules = ROLE_RULES[role]
    normalized = json.dumps(response, ensure_ascii=False).lower()
    keys = list(rules["keys"])
    present_keys = [key for key in keys if key in response]
    anchor_hits = [
        any(term.lower() in normalized for term in alternatives)
        for alternatives in rules["anchors"]
    ]
    forbidden_hits = [term for term in rules.get("forbidden", ()) if term.lower() in normalized]
    raw_points = len(present_keys) + sum(anchor_hits) - len(forbidden_hits)
    max_points = len(keys) + len(anchor_hits)
    return {
        "score": max(0, raw_points),
        "max_score": max_points,
        "score_percent": round(max(0, raw_points) * 100 / max_points, 1),
        "present_keys": present_keys,
        "missing_keys": [key for key in keys if key not in response],
        "anchor_hits": sum(anchor_hits),
        "anchor_total": len(anchor_hits),
        "forbidden_hits": forbidden_hits,
        "contract_pass": len(present_keys) == len(keys) and not forbidden_hits,
    }


def _agy_executable() -> str:
    resolved = shutil.which("agy") or shutil.which("agy.exe")
    if resolved:
        return resolved
    raise RuntimeError("agy executable not found")


def _run_text(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


def inventory(agy: str) -> tuple[str, list[str]]:
    version = _run_text([agy, "--version"], timeout=20)
    models = _run_text([agy, "models"], timeout=30)
    if version.returncode or models.returncode:
        raise RuntimeError("could not read Antigravity version/model inventory")
    return version.stdout.strip(), [line.strip() for line in models.stdout.splitlines() if line.strip()]


def run_candidate(agy: str, candidate: Candidate, *, timeout: int) -> dict[str, Any]:
    command = [
        agy, "--new-project", "--print", PROMPTS[candidate.role],
        "--mode", "plan", "--sandbox", "--dangerously-skip-permissions",
        "--print-timeout", f"{timeout}s", "--model", candidate.model,
    ]
    started = time.monotonic()
    try:
        proc = _run_text(command, timeout=timeout + 20)
        raw = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        result: dict[str, Any] = {
            "model": candidate.model, "role": candidate.role, "tier": candidate.tier,
            "baseline": candidate.baseline, "exit_code": proc.returncode,
            "wall_seconds": round(time.monotonic() - started, 3), "usage": None,
            "cost_cents": 0, "raw_output": raw[:20_000], "stderr": stderr[:4_000],
        }
        if proc.returncode != 0:
            result.update({"status": "cli_failed", "evaluation": None})
            return result
        try:
            parsed = parse_json_output(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            result.update({"status": "invalid_json", "parse_error": str(exc), "evaluation": None})
            return result
        result.update({"status": "completed", "response": parsed, "evaluation": score_response(candidate.role, parsed)})
        return result
    except subprocess.TimeoutExpired:
        return {
            "model": candidate.model, "role": candidate.role, "tier": candidate.tier,
            "baseline": candidate.baseline, "status": "timeout", "exit_code": None,
            "wall_seconds": round(time.monotonic() - started, 3), "usage": None,
            "cost_cents": 0, "evaluation": None,
        }


def build_conclusion(results: list[dict[str, Any]]) -> dict[str, Any]:
    role_rankings: dict[str, list[dict[str, Any]]] = {}
    for role in PROMPTS:
        rows = [row for row in results if row["role"] == role]
        rows.sort(key=lambda row: ((row.get("evaluation") or {}).get("score_percent", -1), -row["wall_seconds"]), reverse=True)
        role_rankings[role] = [
            {
                "model": row["model"], "score_percent": (row.get("evaluation") or {}).get("score_percent"),
                "wall_seconds": row["wall_seconds"], "status": row["status"],
            }
            for row in rows
        ]
    return {
        "policy_change_allowed": False,
        "reason": "screening de una sola muestra por modelo; requiere varias semillas y baseline comparable",
        "economic_comparison_available": False,
        "economic_reason": "agy headless no expone tokens; suscripcion se mide en llamadas y segundos, no como coste API",
        "role_rankings_directional": role_rankings,
        "next_gate": "repetir al menos tres semillas para los challengers que igualen o superen al baseline de su rol",
        "goodhart_risk": "material: rubrica estructural/lexica conocida por el harness, sin juez factual independiente",
    }


def aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for report in reports for row in report.get("results", [])]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((str(row["role"]), str(row["model"])), []).append(row)
    summaries: list[dict[str, Any]] = []
    for (role, model), samples in sorted(grouped.items()):
        completed = [row for row in samples if row.get("status") == "completed" and row.get("evaluation")]
        scores = [float(row["evaluation"]["score_percent"]) for row in completed]
        seconds = [float(row["wall_seconds"]) for row in completed]
        summaries.append({
            "role": role, "model": model, "baseline": BASELINES.get(role) == model,
            "samples": len(samples), "completed": len(completed),
            "contract_passes": sum(bool(row["evaluation"].get("contract_pass")) for row in completed),
            "score_median": round(statistics.median(scores), 1) if scores else None,
            "score_range": [round(min(scores), 1), round(max(scores), 1)] if scores else None,
            "wall_seconds_median": round(statistics.median(seconds), 3) if seconds else None,
            "wall_seconds_range": [round(min(seconds), 3), round(max(seconds), 3)] if seconds else None,
        })
    decisions: dict[str, dict[str, Any]] = {}
    for role, baseline_model in BASELINES.items():
        role_rows = [row for row in summaries if row["role"] == role]
        baseline = next((row for row in role_rows if row["model"] == baseline_model), None)
        challengers: list[dict[str, Any]] = []
        for row in role_rows:
            if row["model"] == baseline_model or not baseline:
                continue
            if row["completed"] < 3 or baseline["completed"] < 3:
                disposition = "insufficient_samples"
            elif row["score_median"] > baseline["score_median"]:
                disposition = "candidate_for_behavioral_validation"
            elif (
                row["score_median"] == baseline["score_median"]
                and row["wall_seconds_median"] < baseline["wall_seconds_median"] * 0.9
            ):
                disposition = "candidate_for_economy_validation"
            else:
                disposition = "retain_baseline"
            challengers.append({
                "model": row["model"], "disposition": disposition,
                "median_score_delta": round(row["score_median"] - baseline["score_median"], 1)
                if row["score_median"] is not None and baseline["score_median"] is not None else None,
                "median_wall_seconds_delta": round(row["wall_seconds_median"] - baseline["wall_seconds_median"], 3)
                if row["wall_seconds_median"] is not None and baseline["wall_seconds_median"] is not None else None,
            })
        decisions[role] = {"baseline": baseline_model, "challengers": challengers}
    return {
        "schema_version": 1,
        "benchmark": "antigravity_role_model_calibration_aggregate",
        "provider": "google-antigravity",
        "channel": "subscription",
        "source_reports": len(reports),
        "samples": len(rows),
        "results": summaries,
        "decisions": decisions,
        "conclusion": {
            "structural_screening_complete": all(row["completed"] >= 3 for row in summaries),
            "default_change_allowed": False,
            "reason": "tres muestras permiten seleccionar follow-ups, pero falta evidencia conductual independiente",
            "economic_comparison_available": False,
            "economic_reason": "sin tokens de agy; latencia y numero de runs no equivalen a coste API ni a cuota total",
            "goodhart_risk": "material",
        },
        "quota_observation": {
            "unit": "runs_and_wall_seconds", "runs": len(rows),
            "wall_seconds": round(sum(float(row.get("wall_seconds") or 0) for row in rows), 3),
            "tokens": None, "marginal_cost_cents": 0,
        },
    }


def _write_checkpoint(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input", action="append", type=Path, dest="inputs", help="agrega recibos existentes; repetible")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--model", action="append", dest="models", help="limita el screening; repetible")
    parser.add_argument("--role", action="append", dest="roles", help="limita por rol; repetible")
    args = parser.parse_args()

    output = args.output.resolve()
    if args.inputs:
        reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.inputs]
        aggregate = aggregate_reports(reports)
        _write_checkpoint(output, aggregate)
        print(json.dumps(aggregate["conclusion"], indent=2, ensure_ascii=False))
        return 0

    agy = _agy_executable()
    cli_version, available = inventory(agy)
    selected = [
        item for item in MATRIX
        if (not args.models or item.model in set(args.models))
        and (not args.roles or item.role in set(args.roles))
    ]
    missing = [item.model for item in selected if item.model not in available]
    if missing:
        raise RuntimeError(f"models missing from executable inventory: {missing}")
    report: dict[str, Any] = {
        "schema_version": 1, "benchmark": "antigravity_role_model_screening",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seed": args.seed,
        "provider": "google-antigravity", "channel": "subscription", "cli_version": cli_version,
        "inventory": available, "matrix": [item.__dict__ for item in selected],
        "evaluation_contract": {
            "evaluators": ["required_json_shape", "hidden_causal_anchor_coverage", "forbidden_noise"],
            "independent_behavioral_evidence": False, "repetitions_per_model": 1,
        },
        "results": [],
    }
    for candidate in selected:
        report["results"].append(run_candidate(agy, candidate, timeout=max(30, args.timeout)))
        report["conclusion"] = build_conclusion(report["results"])
        _write_checkpoint(output, report)
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    report["quota_observation"] = {
        "unit": "runs_and_wall_seconds", "runs": len(report["results"]),
        "wall_seconds": round(sum(row["wall_seconds"] for row in report["results"]), 3),
        "tokens": None, "marginal_cost_cents": 0,
    }
    report["conclusion"] = build_conclusion(report["results"])
    _write_checkpoint(output, report)
    print(json.dumps(report["conclusion"], indent=2, ensure_ascii=False))
    return 0 if all(row["status"] == "completed" for row in report["results"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
