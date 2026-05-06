from __future__ import annotations

import json
import contextlib
import sqlite3
import uuid
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path
from typing import Any


def enqueue_wakeup(
    db_path: Path,
    *,
    agent_id: str,
    source: str,
    reason: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    trigger_detail: str | None = None,
    wakeup_id: str | None = None,
) -> dict[str, Any]:
    with contextlib.closing(_connect(db_path)) as conn:
        try:
            row = conn.execute(
                """
                INSERT INTO wakeup_requests (
                    id, agent_id, source, reason, status, trigger_detail,
                    payload_json, idempotency_key
                )
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)
                RETURNING *
                """,
                (
                    wakeup_id or str(uuid.uuid4()),
                    agent_id,
                    source,
                    reason,
                    trigger_detail,
                    _json(payload),
                    idempotency_key,
                ),
            ).fetchone()
            return dict(row)
        except sqlite3.IntegrityError:
            if not idempotency_key:
                raise
        row = conn.execute(
            """
            UPDATE wakeup_requests
            SET status = CASE
                    WHEN status IN ('finished', 'skipped', 'failed', 'cancelled') THEN 'queued'
                    ELSE status
                END,
                claimed_at = CASE
                    WHEN status IN ('finished', 'skipped', 'failed', 'cancelled') THEN NULL
                    ELSE claimed_at
                END,
                finished_at = CASE
                    WHEN status IN ('finished', 'skipped', 'failed', 'cancelled') THEN NULL
                    ELSE finished_at
                END,
                run_id = CASE
                    WHEN status IN ('finished', 'skipped', 'failed', 'cancelled') THEN NULL
                    ELSE run_id
                END,
                error = NULL,
                coalesced_count = coalesced_count + 1,
                payload_json = ?,
                trigger_detail = COALESCE(?, trigger_detail),
                updated_at = CURRENT_TIMESTAMP
            WHERE agent_id = ?
              AND idempotency_key = ?
            RETURNING *
            """,
            (_json(payload), trigger_detail, agent_id, idempotency_key),
        ).fetchone()
        return dict(row)


def claim_next_wakeup(
    db_path: Path,
    *,
    agent_id: str | None = None,
    claimed_at: str | None = None,
    wakeup_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> dict[str, Any] | None:
    params: list[Any] = []
    if wakeup_ids is not None:
        ids = [str(item) for item in wakeup_ids if str(item).strip()]
        if not ids:
            return None
    else:
        ids = []
    agent_filter = ""
    if agent_id:
        agent_filter = "AND agent_id = ?"
        params.append(agent_id)
    id_filter = ""
    if ids:
        id_filter = f"AND id IN ({', '.join('?' for _ in ids)})"
        params.extend(ids)
    sql = f"""
        UPDATE wakeup_requests
        SET status = 'claimed',
            claimed_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = (
            SELECT id
            FROM wakeup_requests
            WHERE status = 'queued'
              {agent_filter}
              {id_filter}
            ORDER BY requested_at ASC, id ASC
            LIMIT 1
        )
        RETURNING *
    """
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(sql, (claimed_at or _now(), *params)).fetchone()
        return dict(row) if row is not None else None


def finish_wakeup(
    db_path: Path,
    *,
    wakeup_id: str,
    status: str,
    run_id: str | None = None,
    error: str | None = None,
    finished_at: str | None = None,
) -> dict[str, Any] | None:
    if status not in {"finished", "skipped", "failed", "cancelled"}:
        raise ValueError(f"invalid wakeup terminal status: {status!r}")
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            UPDATE wakeup_requests
            SET status = ?,
                run_id = COALESCE(?, run_id),
                error = ?,
                finished_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status IN ('claimed', 'running', 'queued')
            RETURNING *
            """,
            (status, run_id, error, finished_at or _now(), wakeup_id),
        ).fetchone()
        return dict(row) if row is not None else None


def reconcile_stale_wakeups(
    db_path: Path,
    *,
    max_age_sec: int = 300,
    now: datetime | str | None = None,
) -> list[str]:
    """Requeue claimed wakeups that never reached a run.

    This is the DB-queue equivalent of Paperclip's stranded-work scan: a crash
    or an old manual claim must not leave an agent permanently asleep.
    """

    now_dt = _coerce_datetime(now) or datetime.now(timezone.utc)
    cutoff = now_dt - timedelta(seconds=max(1, int(max_age_sec)))
    stale_ids: list[str] = []
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, claimed_at
            FROM wakeup_requests
            WHERE status = 'claimed'
              AND run_id IS NULL
            """
        ).fetchall()
        for row in rows:
            claimed_at = _coerce_datetime(row["claimed_at"])
            if claimed_at is not None and claimed_at > cutoff:
                continue
            stale_ids.append(row["id"])
        for wakeup_id in stale_ids:
            conn.execute(
                """
                UPDATE wakeup_requests
                SET status = 'queued',
                    claimed_at = NULL,
                    error = 'requeued_stale_claim',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = 'claimed'
                  AND run_id IS NULL
                """,
                (wakeup_id,),
            )
    return stale_ids


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


def _coerce_datetime(value: datetime | str | Any | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None
