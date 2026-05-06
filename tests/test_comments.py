from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aiteam.db.comments import create_comment, get_comment, list_comments


def _setup_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    schema = (Path(__file__).parent.parent / "aiteam" / "db" / "schema.sql").read_text()
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.executescript(schema)
    # insert a goal and issue required by FK
    conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'Goal')")
    conn.execute(
        "INSERT INTO issues (id, goal_id, title, status) VALUES ('i1', 'g1', 'Issue', 'todo')"
    )
    conn.close()
    return db


def test_create_and_get_comment(tmp_path):
    db = _setup_db(tmp_path)
    row = create_comment(db, issue_id="i1", body="Hello world")
    assert row["body"] == "Hello world"
    assert row["issue_id"] == "i1"
    assert row["id"] is not None

    fetched = get_comment(db, comment_id=row["id"])
    assert fetched is not None
    assert fetched["body"] == "Hello world"


def test_create_comment_with_author(tmp_path):
    db = _setup_db(tmp_path)
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.execute(
        "INSERT INTO agents (id, role, name, seniority) VALUES ('agent-x', 'engineer', 'Bot', 'standard')"
    )
    conn.close()
    row = create_comment(
        db,
        issue_id="i1",
        body="By agent",
        author_agent_id="agent-x",
        author_user_id=None,
    )
    assert row["author_agent_id"] == "agent-x"
    assert row["author_user_id"] is None


def test_create_comment_with_metadata(tmp_path):
    db = _setup_db(tmp_path)
    row = create_comment(db, issue_id="i1", body="Meta", metadata={"verdict": "approved"})
    import json
    meta = json.loads(row["metadata_json"])
    assert meta["verdict"] == "approved"


def test_list_comments_ordered(tmp_path):
    db = _setup_db(tmp_path)
    create_comment(db, issue_id="i1", body="First")
    create_comment(db, issue_id="i1", body="Second")
    create_comment(db, issue_id="i1", body="Third")

    rows = list_comments(db, issue_id="i1")
    assert [r["body"] for r in rows] == ["First", "Second", "Third"]


def test_list_comments_empty(tmp_path):
    db = _setup_db(tmp_path)
    rows = list_comments(db, issue_id="i1")
    assert rows == []


def test_list_comments_limit(tmp_path):
    db = _setup_db(tmp_path)
    for i in range(5):
        create_comment(db, issue_id="i1", body=f"msg {i}")
    rows = list_comments(db, issue_id="i1", limit=3)
    assert len(rows) == 3


def test_get_comment_not_found(tmp_path):
    db = _setup_db(tmp_path)
    assert get_comment(db, comment_id="nonexistent") is None


def test_create_comment_requires_body(tmp_path):
    db = _setup_db(tmp_path)
    with pytest.raises(ValueError):
        create_comment(db, issue_id="i1", body="")
