from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.db.wakeups import enqueue_wakeup


TERMINAL_STATUSES = {"accepted", "rejected", "answered", "cancelled", "expired"}

# Which actions each kind supports
_KIND_ACTIONS: dict[str, set[str]] = {
    "request_confirmation": {"accept", "reject", "cancel"},
    "ask_user_questions": {"answer", "cancel"},
    "suggest_tasks": {"accept", "reject", "cancel"},
}

_ACTION_STATUS: dict[str, str] = {
    "accept": "accepted",
    "reject": "rejected",
    "answer": "answered",
    "cancel": "cancelled",
}

_IDEMPOTENCY_CONSTRAINT = "idx_interaction_idempotency"


def create_interaction(
    db_path: Path,
    *,
    issue_id: str,
    kind: str,
    payload: dict[str, Any],
    continuation_policy: str = "wake_assignee",
    idempotency_key: str | None = None,
    source_run_id: str | None = None,
    source_comment_id: str | None = None,
    created_by_agent_id: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    interaction_id: str | None = None,
) -> dict[str, Any]:
    if kind not in _KIND_ACTIONS:
        raise ValueError(f"unknown interaction kind: {kind!r}")

    if idempotency_key:
        existing = _get_by_idempotency(db_path, issue_id=issue_id, key=idempotency_key)
        if existing is not None:
            return existing

    row_id = interaction_id or str(uuid.uuid4())
    try:
        with contextlib.closing(_connect(db_path)) as conn:
            row = conn.execute(
                """
                INSERT INTO issue_thread_interactions (
                    id, issue_id, kind, status, continuation_policy,
                    payload_json, source_run_id, source_comment_id,
                    idempotency_key, created_by_agent_id, title, summary
                )
                VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING *
                """,
                (
                    row_id, issue_id, kind, continuation_policy,
                    _json(payload), source_run_id, source_comment_id,
                    idempotency_key, created_by_agent_id, title, summary,
                ),
            ).fetchone()
            # Una decisión que espera al humano no debe esperar en silencio:
            # avisar por el canal configurado (AITEAM_NOTIFY_COMMAND). Nunca
            # bloquea ni falla la creación de la interacción.
            if kind == "request_confirmation":
                with contextlib.suppress(Exception):
                    from aiteam.notifications import notify_escalation  # noqa: PLC0415
                    notify_escalation({
                        "kind": kind,
                        "title": title or "",
                        "summary": summary or "",
                        "issue_id": issue_id,
                        "project": str(Path(db_path).resolve().parent.parent.name),
                    })
            return dict(row)
    except sqlite3.IntegrityError as exc:
        # Race on unique idempotency index
        if idempotency_key and _is_idempotency_conflict(exc):
            existing = _get_by_idempotency(db_path, issue_id=issue_id, key=idempotency_key)
            if existing is not None:
                return existing
        raise


def list_interactions(db_path: Path, *, issue_id: str) -> list[dict[str, Any]]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT * FROM issue_thread_interactions
            WHERE issue_id = ?
            ORDER BY created_at ASC, rowid ASC
            """,
            (issue_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_interaction(db_path: Path, *, interaction_id: str) -> dict[str, Any] | None:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE id = ?",
            (interaction_id,),
        ).fetchone()
        return dict(row) if row is not None else None


def resolve_interaction(
    db_path: Path,
    *,
    interaction_id: str,
    action: str,
    result: dict[str, Any] | None = None,
    resolution_data: dict[str, Any] | None = None,
    resolved_by_user_id: str | None = None,
    resolved_by_agent_id: str | None = None,
) -> dict[str, Any]:
    """Resolve a pending interaction.

    Returns the updated interaction row.
    Raises ValueError for invalid transitions, 409-style ConflictError if already resolved.
    """
    with contextlib.closing(_connect(db_path)) as conn:
        current = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE id = ?",
            (interaction_id,),
        ).fetchone()

    if current is None:
        raise LookupError(f"interaction {interaction_id!r} not found")

    current = dict(current)
    kind: str = current["kind"]
    status: str = current["status"]

    if status != "pending":
        raise ConflictError(f"interaction is already {status!r}")

    allowed = _KIND_ACTIONS.get(kind, set())
    if action not in allowed:
        raise ValueError(f"action {action!r} is not valid for kind {kind!r}; allowed: {sorted(allowed)}")

    new_status = _ACTION_STATUS[action]
    now = _now()
    base_result = result or _default_result(action)
    # Merge any user-supplied resolution_data (e.g. modified team proposal)
    if resolution_data:
        built_result = {**base_result, "resolution_data": resolution_data}
    else:
        built_result = base_result

    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            UPDATE issue_thread_interactions
            SET status = ?,
                result_json = ?,
                resolved_by_user_id = ?,
                resolved_by_agent_id = ?,
                resolved_at = ?,
                updated_at = ?
            WHERE id = ?
              AND status = 'pending'
            RETURNING *
            """,
            (
                new_status, _json(built_result),
                resolved_by_user_id, resolved_by_agent_id,
                now, now, interaction_id,
            ),
        ).fetchone()

    if row is None:
        raise ConflictError("interaction was resolved concurrently")

    updated = dict(row)

    # Enqueue wakeup based on continuation_policy
    policy = current.get("continuation_policy") or "wake_assignee"
    if policy == "wake_assignee" and new_status not in {"cancelled"}:
        _maybe_enqueue_wakeup(db_path, interaction=updated, action=action)
    elif policy == "wake_assignee_on_accept" and action == "accept":
        _maybe_enqueue_wakeup(db_path, interaction=updated, action=action)

    return updated


class ConflictError(Exception):
    """Raised when an interaction has already been resolved (409 semantics)."""


# ── helpers ──────────────────────────────────────────────────────────────────

def decision_latency_stats(db_path: Path) -> dict[str, Any]:
    """Latencia de decisión humana sobre las escalaciones (request_confirmation).

    En modo supervisado este número ES el cuello de botella del lead time del
    proyecto — sin medirlo, nadie sabía cuánto esperaban los equipos. Segundos:
    - resueltas: media y máximo de (resolved_at - created_at)
    - pendientes: cuántas hay y la edad de la más vieja
    """
    with contextlib.closing(_connect(db_path)) as conn:
        resolved = conn.execute(
            """
            SELECT COUNT(*) AS n,
                   AVG((julianday(resolved_at) - julianday(created_at)) * 86400.0) AS avg_s,
                   MAX((julianday(resolved_at) - julianday(created_at)) * 86400.0) AS max_s
            FROM issue_thread_interactions
            WHERE kind = 'request_confirmation' AND resolved_at IS NOT NULL
            """
        ).fetchone()
        pending = conn.execute(
            """
            SELECT COUNT(*) AS n,
                   MAX((julianday('now') - julianday(created_at)) * 86400.0) AS oldest_s
            FROM issue_thread_interactions
            WHERE kind = 'request_confirmation' AND status = 'pending'
            """
        ).fetchone()
    return {
        "resolved_count": int(resolved["n"] or 0),
        "avg_resolution_seconds": round(float(resolved["avg_s"]), 1) if resolved["avg_s"] else None,
        "max_resolution_seconds": round(float(resolved["max_s"]), 1) if resolved["max_s"] else None,
        "pending_count": int(pending["n"] or 0),
        "oldest_pending_seconds": round(float(pending["oldest_s"]), 1) if pending["oldest_s"] else None,
    }


def _maybe_enqueue_wakeup(
    db_path: Path,
    *,
    interaction: dict[str, Any],
    action: str,
) -> None:
    issue_id: str = interaction["issue_id"]
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT assignee_agent_id FROM issues WHERE id = ?",
            (issue_id,),
        ).fetchone()

    if row is None:
        return
    assignee_agent_id: str | None = row[0]
    if not assignee_agent_id:
        return

    enqueue_wakeup(
        db_path,
        agent_id=assignee_agent_id,
        source="interaction",
        reason="interaction_resolved",
        trigger_detail=f"{interaction['kind']}:{action}",
        payload={
            "interaction_id": interaction["id"],
            "issue_id": issue_id,
            "kind": interaction["kind"],
            "action": action,
            "wake_reason": "interaction_resolved",
        },
        idempotency_key=f"interaction:{interaction['id']}:resolved",
    )


def _get_by_idempotency(
    db_path: Path,
    *,
    issue_id: str,
    key: str,
) -> dict[str, Any] | None:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT * FROM issue_thread_interactions
            WHERE issue_id = ? AND idempotency_key = ?
            """,
            (issue_id, key),
        ).fetchone()
        return dict(row) if row is not None else None


def _default_result(action: str) -> dict[str, Any]:
    return {"version": 1, "outcome": action}


def _is_idempotency_conflict(exc: sqlite3.IntegrityError) -> bool:
    return "idx_interaction_idempotency" in str(exc) or "UNIQUE constraint" in str(exc)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
