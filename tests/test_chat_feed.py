from __future__ import annotations

import sqlite3
from pathlib import Path

from api.routers.chat import _load_chat
from aiteam.db.migration import SCHEMA_PATH


def _init(db: Path) -> None:
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('role:lead', 'lead', 'Lead')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) "
            "VALUES ('issue:intake', 'g1', 'T', 'in_progress', 'lead', 'role:lead')"
        )
        conn.commit()


def _add_comment(db: Path, cid: str, body: str, *, created_at: str, user: bool = False) -> None:
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO issue_comments (id, issue_id, author_agent_id, author_user_id, body, created_at) "
            "VALUES (?, 'issue:intake', ?, ?, ?, ?)",
            (cid, None if user else "role:lead", "user" if user else None, body, created_at),
        )
        conn.commit()


def test_chat_returns_newest_when_thread_exceeds_limit(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init(db)
    # 150 messages, chronological ids so created_at ordering is deterministic.
    for i in range(150):
        _add_comment(db, f"c{i:03d}", f"msg {i}", created_at=f"2026-07-07 00:{i//60:02d}:{i%60:02d}")

    items = _load_chat(db, limit=120)

    assert len(items) == 120
    # Presented chronologically ascending…
    assert [it["body"] for it in items] == [f"msg {i}" for i in range(30, 150)]
    # …and crucially the NEWEST message is included (the bug froze it out).
    assert items[-1]["body"] == "msg 149"


def test_chat_short_thread_unaffected(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init(db)
    _add_comment(db, "c1", "hola", created_at="2026-07-07 00:00:01", user=True)
    _add_comment(db, "c2", "respuesta del lead", created_at="2026-07-07 00:00:02")

    items = _load_chat(db, limit=120)

    assert [it["body"] for it in items] == ["hola", "respuesta del lead"]
    assert items[0]["sender"] == "user"
    assert items[1]["sender"] == "agent"
