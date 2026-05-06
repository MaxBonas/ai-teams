from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


def record_tool_access(
    db_path: Path,
    *,
    tool_name: str,
    decision: str,
    run_id: str | None = None,
    agent_id: str | None = None,
    issue_id: str | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    normalized_tool = str(tool_name or "").strip()
    normalized_decision = str(decision or "").strip()
    if not normalized_tool:
        raise ValueError("tool_name is required")
    if not normalized_decision:
        raise ValueError("decision is required")

    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            INSERT INTO tool_access (
                id, run_id, agent_id, issue_id, tool_name, decision, reason, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                event_id or str(uuid.uuid4()),
                run_id,
                agent_id,
                issue_id,
                normalized_tool,
                normalized_decision,
                reason,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            ),
        ).fetchone()
        return dict(row)


def list_tool_access(
    db_path: Path,
    *,
    run_id: str | None = None,
    agent_id: str | None = None,
    issue_id: str | None = None,
    decision: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if run_id:
        filters.append("run_id = ?")
        params.append(run_id)
    if agent_id:
        filters.append("agent_id = ?")
        params.append(agent_id)
    if issue_id:
        filters.append("issue_id = ?")
        params.append(issue_id)
    if decision:
        filters.append("decision = ?")
        params.append(decision)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(max(1, min(int(limit), 500)))

    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            f"SELECT * FROM tool_access {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
