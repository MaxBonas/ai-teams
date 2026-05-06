from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


def log_activity(
    db_path: Path,
    *,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    actor_agent_id: str | None = None,
    actor_user_id: str | None = None,
    run_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append an immutable activity entry. Fire-and-forget safe — never raises on duplicate."""
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            INSERT INTO activity_log (
                id, run_id, actor_agent_id, actor_user_id,
                action, target_type, target_id, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                str(uuid.uuid4()),
                run_id,
                actor_agent_id,
                actor_user_id,
                action,
                target_type,
                target_id,
                json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
            ),
        ).fetchone()
        return dict(row)


def list_activity(
    db_path: Path,
    *,
    run_id: str | None = None,
    actor_agent_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if run_id:
        filters.append("run_id = ?")
        params.append(run_id)
    if actor_agent_id:
        filters.append("actor_agent_id = ?")
        params.append(actor_agent_id)
    if target_type:
        filters.append("target_type = ?")
        params.append(target_type)
    if target_id:
        filters.append("target_id = ?")
        params.append(target_id)

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(max(1, int(limit)))

    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            f"SELECT * FROM activity_log {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
