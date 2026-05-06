from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


def create_goal(
    db_path: Path,
    *,
    title: str,
    description: str | None = None,
    status: str = "active",
    source: str = "api",
    goal_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            INSERT INTO goals (id, title, description, status, source, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                goal_id or str(uuid.uuid4()),
                title.strip(),
                description,
                status,
                source,
                _json(metadata),
            ),
        ).fetchone()
        return dict(row)


def list_goals(
    db_path: Path,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if status:
        sql = "SELECT * FROM goals WHERE status = ? ORDER BY created_at DESC LIMIT ?"
        params: tuple = (status, limit)
    else:
        sql = "SELECT * FROM goals ORDER BY created_at DESC LIMIT ?"
        params = (limit,)
    with contextlib.closing(_connect(db_path)) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_goal(db_path: Path, *, goal_id: str) -> dict[str, Any] | None:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return dict(row) if row else None


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _json(v: Any) -> str:
    return json.dumps(v or {}, ensure_ascii=False, sort_keys=True)
