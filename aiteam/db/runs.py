from __future__ import annotations

import json
import contextlib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "lost", "skipped"}


def create_run(
    db_path: Path,
    *,
    run_id: str,
    agent_id: str,
    issue_id: str | None = None,
    wakeup_request_id: str | None = None,
    profile: str | None = None,
    invocation_source: str = "manual",
    trigger_detail: str | None = None,
    adapter_type: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    channel: str | None = None,
    context_snapshot: dict[str, Any] | None = None,
    cost_policy: dict[str, Any] | None = None,
    delegation_reason: str | None = None,
    complexity: str | None = None,
    supervisor_run_id: str | None = None,
    estimated_cost_cents: int = 0,
    estimated_savings_cents: int = 0,
) -> dict[str, Any]:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            INSERT INTO runs (
                id, agent_id, issue_id, wakeup_request_id, profile,
                invocation_source, trigger_detail, status, adapter_type,
                provider, model, channel, context_snapshot_json,
                cost_policy_json, delegation_reason, complexity,
                supervisor_run_id, estimated_cost_cents, estimated_savings_cents
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            RETURNING *
            """,
            (
                run_id,
                agent_id,
                issue_id,
                wakeup_request_id,
                profile,
                invocation_source,
                trigger_detail,
                adapter_type,
                provider,
                model,
                channel,
                _json(context_snapshot),
                _json(cost_policy),
                delegation_reason,
                complexity,
                supervisor_run_id,
                int(estimated_cost_cents or 0),
                int(estimated_savings_cents or 0),
            ),
        ).fetchone()
        if row is not None:
            return dict(row)
        existing = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(existing)


def mark_run_running(
    db_path: Path,
    *,
    run_id: str,
    process_pid: int | None = None,
    started_at: str | None = None,
) -> dict[str, Any] | None:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            UPDATE runs
            SET status = 'running',
                started_at = COALESCE(started_at, ?),
                process_pid = COALESCE(?, process_pid),
                liveness_state = 'running',
                last_output_at = COALESCE(last_output_at, ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status IN ('queued', 'running')
            RETURNING *
            """,
            (started_at or _now(), process_pid, started_at or _now(), run_id),
        ).fetchone()
        return dict(row) if row is not None else None


def append_run_event(
    db_path: Path,
    *,
    run_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    stream: str | None = None,
    seq: int | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    with contextlib.closing(_connect(db_path)) as conn:
        next_seq = seq
        if next_seq is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            next_seq = int(row[0])
        inserted = conn.execute(
            """
            INSERT INTO run_events (id, run_id, event_type, seq, stream, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                event_id or str(uuid.uuid4()),
                run_id,
                event_type,
                next_seq,
                stream,
                _json(payload),
            ),
        ).fetchone()
        conn.execute(
            """
            UPDATE runs
            SET last_output_at = CASE
                    WHEN ? IN ('stdout', 'stderr', 'output') THEN CURRENT_TIMESTAMP
                    ELSE last_output_at
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (stream, run_id),
        )
        return dict(inserted)


def finish_run(
    db_path: Path,
    *,
    run_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    exit_code: int | None = None,
    error: str | None = None,
    error_code: str | None = None,
    actual_cost_cents: int = 0,
    finished_at: str | None = None,
) -> dict[str, Any] | None:
    if status not in TERMINAL_RUN_STATUSES:
        raise ValueError(f"status must be terminal, got {status!r}")
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            UPDATE runs
            SET status = ?,
                finished_at = ?,
                exit_code = ?,
                error = ?,
                error_code = ?,
                usage_json = ?,
                result_json = ?,
                actual_cost_cents = ?,
                liveness_state = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status IN ('queued', 'running')
            RETURNING *
            """,
            (
                status,
                finished_at or _now(),
                exit_code,
                error,
                error_code,
                _json(usage),
                _json(result),
                int(actual_cost_cents or 0),
                status,
                run_id,
            ),
        ).fetchone()
        return dict(row) if row is not None else None


def reconcile_stale_runs(
    db_path: Path,
    *,
    max_age_sec: int = 300,
) -> list[str]:
    """Mark runs stuck in 'running' longer than max_age_sec as failed.

    Returns the IDs of reconciled runs. Safe to call on startup.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_sec)).isoformat()
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            UPDATE runs
            SET status = 'failed',
                error = 'reconciled: liveness window exceeded',
                error_code = 'liveness_timeout',
                liveness_state = 'failed',
                finished_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
              AND started_at < ?
            RETURNING id
            """,
            (cutoff,),
        ).fetchall()
        return [row[0] for row in rows]


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
