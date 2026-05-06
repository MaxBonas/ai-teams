from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def create_issue(
    db_path: Path,
    *,
    title: str,
    status: str = "backlog",
    goal_id: str | None = None,
    parent_id: str | None = None,
    description: str | None = None,
    role: str | None = None,
    complexity: str | None = None,
    priority: int = 0,
    assignee_agent_id: str | None = None,
    issue_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            INSERT INTO issues (
                id, parent_id, goal_id, title, description, status,
                priority, role, complexity, assignee_agent_id, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                issue_id or str(uuid.uuid4()),
                parent_id,
                goal_id,
                title.strip(),
                description,
                status,
                int(priority),
                role,
                complexity,
                assignee_agent_id,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            ),
        ).fetchone()
        return dict(row)


def list_issues(
    db_path: Path,
    *,
    goal_id: str | None = None,
    parent_id: str | None = None,
    status: str | None = None,
    assignee_agent_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if goal_id:
        filters.append("goal_id = ?")
        params.append(goal_id)
    if parent_id is not None:
        filters.append("parent_id = ?" if parent_id else "parent_id IS NULL")
        if parent_id:
            params.append(parent_id)
    if status:
        filters.append("status = ?")
        params.append(status)
    if assignee_agent_id:
        filters.append("assignee_agent_id = ?")
        params.append(assignee_agent_id)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit)
    with contextlib.closing(_connect(db_path)) as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM issues {where} ORDER BY priority DESC, created_at ASC LIMIT ?",
            params,
        ).fetchall()]


def get_issue(db_path: Path, *, issue_id: str) -> dict[str, Any] | None:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
        return dict(row) if row else None


def update_issue(
    db_path: Path,
    *,
    issue_id: str,
    status: str | None = None,
    title: str | None = None,
    description: str | None = None,
    assignee_agent_id: str | None = None,
    priority: int | None = None,
    complexity: str | None = None,
    criticality: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    sets: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
    params: list[Any] = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if title is not None:
        sets.append("title = ?")
        params.append(title.strip())
    if description is not None:
        sets.append("description = ?")
        params.append(description)
    if assignee_agent_id is not None:
        sets.append("assignee_agent_id = ?")
        params.append(assignee_agent_id)
    if priority is not None:
        sets.append("priority = ?")
        params.append(int(priority))
    if complexity is not None:
        sets.append("complexity = ?")
        params.append(complexity)
    if criticality is not None:
        sets.append("criticality = ?")
        params.append(criticality)
    if metadata is not None:
        sets.append("metadata_json = ?")
        params.append(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    if len(sets) == 1:
        return get_issue(db_path, issue_id=issue_id)
    params.append(issue_id)
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            f"UPDATE issues SET {', '.join(sets)} WHERE id = ? RETURNING *",
            params,
        ).fetchone()
        return dict(row) if row else None


def checkout_issue(
    db_path: Path,
    *,
    issue_id: str,
    agent_id: str,
    expected_statuses: Iterable[str],
    run_id: str,
    locked_at: str | None = None,
) -> dict[str, Any] | None:
    """Atomically claim an issue for a run.

    Returns the updated row on success and None on conflict. Callers should map
    None to HTTP 409 and must not blindly retry that issue.
    """
    statuses = tuple(str(status).strip() for status in expected_statuses if str(status).strip())
    if not statuses:
        raise ValueError("expected_statuses must not be empty")

    db_path = Path(db_path)
    locked_at_sql = locked_at or _now_sql()
    placeholders = ", ".join("?" for _ in statuses)
    sql = f"""
        UPDATE issues
        SET
            assignee_agent_id = ?,
            checkout_run_id = ?,
            execution_run_id = ?,
            status = 'in_progress',
            execution_locked_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND status IN ({placeholders})
          AND (assignee_agent_id IS NULL OR assignee_agent_id = ?)
          AND (checkout_run_id IS NULL OR checkout_run_id = ?)
        RETURNING *
    """
    params = (
        agent_id,
        run_id,
        run_id,
        locked_at_sql,
        issue_id,
        *statuses,
        agent_id,
        run_id,
    )
    with contextlib.closing(sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 20000")
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _now_sql() -> str:
    return datetime.now(timezone.utc).isoformat()
