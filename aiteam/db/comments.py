from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


def create_comment(
    db_path: Path,
    *,
    issue_id: str,
    body: str,
    author_agent_id: str | None = None,
    author_user_id: str | None = None,
    source_run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    comment_id: str | None = None,
) -> dict[str, Any]:
    if not body.strip():
        raise ValueError("comment body must not be empty")
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            INSERT INTO issue_comments (
                id, issue_id, author_agent_id, author_user_id,
                source_run_id, body, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                comment_id or str(uuid.uuid4()),
                issue_id,
                author_agent_id,
                author_user_id,
                source_run_id,
                body.strip(),
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            ),
        ).fetchone()
        return dict(row)


def list_comments(
    db_path: Path,
    *,
    issue_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM issue_comments WHERE issue_id = ? ORDER BY created_at ASC, rowid ASC LIMIT ?",
            (issue_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_comment(db_path: Path, *, comment_id: str) -> dict[str, Any] | None:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM issue_comments WHERE id = ?", (comment_id,)
        ).fetchone()
        return dict(row) if row else None


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
