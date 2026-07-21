"""Detección durable y de bajo ruido de posibles necesidades MCP."""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.db.activity_log import log_activity
from aiteam.db.comments import create_comment
from aiteam.db.wakeups import enqueue_wakeup

_EXPLICIT_CAPABILITY = re.compile(r"\b(?:capability[_ -]?gap|needs[_ -]?capability)\b", re.I)
_UNVERIFIABLE = re.compile(
    r"\b(?:unverifiable|not verifiable|cannot verify|can't verify|no verificable|no se puede verificar)\b",
    re.I,
)
_CAPABILITY_LIMIT = re.compile(
    r"\b(?:no tool|tool unavailable|sin herramienta|no access|without access|sin acceso|"
    r"unsupported|not supported|cannot run|cannot execute|can't run|can't execute|"
    r"hardware unavailable|servicio no disponible)\b",
    re.I,
)


def reconcile_mcp_needs(db_path: Path, *, repeated_threshold: int = 2) -> list[str]:
    """Wake each configured Lead once when durable reports show a capability gap.

    A single explicit ``capability_gap`` or blocked+unverifiable report is enough.
    Lower-confidence capability blockers need distinct runs before they count.
    The reconciler only suggests investigation; it cannot propose or activate MCP.
    """
    try:
        candidates = _candidate_reports(db_path)
    except sqlite3.OperationalError:
        return []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in candidates:
        text = " ".join(
            part for part in (str(row.get("blocker") or ""), str(row.get("evidence") or "")) if part
        ).strip()
        if not text or text.lower() == "none":
            continue
        explicit = bool(_EXPLICIT_CAPABILITY.search(text))
        unverifiable = (
            str(row.get("result") or "").lower() == "blocked"
            and str(row.get("next_owner") or "").lower() in {"lead", "team_lead"}
            and bool(_UNVERIFIABLE.search(text))
        )
        capability_limit = bool(_CAPABILITY_LIMIT.search(text))
        if not (explicit or unverifiable or capability_limit):
            continue
        item = {**row, "text": text[:500], "explicit": explicit, "unverifiable": unverifiable}
        signal_key = _signal_key(text)
        grouped.setdefault((str(row["root_issue_id"]), signal_key), []).append(item)

    suggested: list[str] = []
    threshold = max(2, int(repeated_threshold))
    for (root_issue_id, signal_key), rows in grouped.items():
        distinct = {
            (str(row.get("issue_id") or ""), str(row.get("run_id") or ""), _normalize(row["text"]))
            for row in rows
        }
        qualifies = any(row["explicit"] or row["unverifiable"] for row in rows) or len(distinct) >= threshold
        if not qualifies or _already_suggested(db_path, root_issue_id, signal_key):
            continue
        lead_id = str(rows[0].get("lead_agent_id") or "").strip()
        if not lead_id:
            continue
        evidence = [
            {
                "issue_id": row["issue_id"],
                "run_id": row.get("run_id"),
                "role": row.get("agent_role"),
                "blocker": row.get("blocker"),
                "evidence": row.get("evidence"),
            }
            for row in rows[-5:]
        ]
        payload = {
            "issue_id": root_issue_id,
            "wake_reason": "mcp_need_suggested",
            "suggestion_only": True,
            "evidence": evidence,
            "instruction": (
                "Evalúa si existe un hueco real de capacidad. Reutiliza herramientas existentes; "
                "solo si siguen siendo insuficientes investiga un ejecutable MCP ya instalado y "
                "crea la propuesta owner-gated. No instales ni actives nada automáticamente."
            ),
        }
        summary_lines = [
            "⚙ Sistema: posible necesidad de capacidad externa detectada.",
            "Esto es una sugerencia de investigación, no permiso para instalar o activar MCP.",
        ] + [
            f"- {row['issue_id']} / {row.get('run_id') or 'sin run'}: {row['text']}"
            for row in rows[-5:]
        ]
        create_comment(
            db_path,
            issue_id=root_issue_id,
            body="\n".join(summary_lines),
            author_user_id="system",
            metadata={"source": "mcp_need_detector", "suggestion_only": True},
        )
        if not _lead_has_live_path(db_path, lead_id=lead_id, issue_id=root_issue_id):
            enqueue_wakeup(
                db_path,
                agent_id=lead_id,
                source="mcp_need_detector",
                reason="mcp_need_suggested",
                payload=payload,
                idempotency_key=(
                    f"mcp_need_suggested:{root_issue_id}:"
                    f"{hashlib.sha256(signal_key.encode('utf-8')).hexdigest()[:12]}"
                ),
                trigger_detail=f"{len(distinct)} durable capability signal(s)",
            )
        log_activity(
            db_path,
            action="extension.need_suggested",
            target_type="issue",
            target_id=root_issue_id,
            actor_agent_id=lead_id,
            payload={"signal_key": signal_key, "signal_count": len(distinct), "evidence": evidence},
        )
        suggested.append(root_issue_id)
    return suggested


def _candidate_reports(db_path: Path) -> list[dict[str, Any]]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            WITH RECURSIVE ancestry(issue_id, ancestor_id, parent_id) AS (
                SELECT id, id, parent_id FROM issues
                UNION ALL
                SELECT ancestry.issue_id, parent.id, parent.parent_id
                FROM ancestry JOIN issues parent ON parent.id = ancestry.parent_id
            ), roots AS (
                SELECT issue_id, ancestor_id AS root_issue_id
                FROM ancestry WHERE parent_id IS NULL
            )
            SELECT ar.issue_id, ar.run_id, ar.agent_role, ar.result, ar.next_owner,
                   ar.blocker, ar.evidence, roots.root_issue_id,
                   root.assignee_agent_id AS lead_agent_id
            FROM agent_reports ar
            JOIN roots ON roots.issue_id = ar.issue_id
            JOIN issues root ON root.id = roots.root_issue_id
            JOIN agents lead ON lead.id = root.assignee_agent_id
            WHERE ar.valid = 1 AND ar.is_assignee = 1
              AND LOWER(lead.role) IN ('lead', 'team_lead')
            ORDER BY ar.created_at, ar.rowid
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _already_suggested(db_path: Path, root_issue_id: str, signal_key: str) -> bool:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT 1 FROM activity_log WHERE action='extension.need_suggested' "
            "AND target_type='issue' AND target_id=? "
            "AND json_extract(payload_json, '$.signal_key')=? LIMIT 1",
            (root_issue_id, signal_key),
        ).fetchone()
    return row is not None


def _lead_has_live_path(db_path: Path, *, lead_id: str, issue_id: str) -> bool:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM wakeup_requests
            WHERE agent_id=? AND status IN ('queued','claimed','running')
              AND COALESCE(json_extract(payload_json, '$.issue_id'), json_extract(payload_json, '$.task_id'))=?
            UNION ALL
            SELECT 1 FROM runs
            WHERE agent_id=? AND issue_id=? AND status IN ('queued','running')
            LIMIT 1
            """,
            (lead_id, issue_id, lead_id, issue_id),
        ).fetchone()
    return row is not None


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())[:240]


def _signal_key(text: str) -> str:
    normalized = _normalize(_EXPLICIT_CAPABILITY.sub("", text))
    return " ".join(normalized.split()[:16]) or "capability-gap"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0)
    conn.row_factory = sqlite3.Row
    return conn
