from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.tool_access import list_tool_access, record_tool_access


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.execute("INSERT INTO agents (id, role, name) VALUES (?, ?, ?)", ("agent-1", "lead", "Lead"))
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) VALUES (?, ?, ?, ?, ?)",
            ("issue-1", "goal-1", "Issue", "todo", "agent-1"),
        )
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status) VALUES (?, ?, ?, ?)",
            ("run-1", "agent-1", "issue-1", "queued"),
        )
        conn.commit()


def test_record_and_list_tool_access(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    row = record_tool_access(
        db_path,
        run_id="run-1",
        agent_id="agent-1",
        issue_id="issue-1",
        tool_name="adapter:lead_builtin",
        decision="allowed",
        reason="adapter selected",
        metadata={"channel": "local"},
    )

    assert row["tool_name"] == "adapter:lead_builtin"
    assert row["decision"] == "allowed"
    assert json.loads(row["metadata_json"])["channel"] == "local"

    rows = list_tool_access(db_path, issue_id="issue-1", decision="allowed")
    assert len(rows) == 1
    assert rows[0]["id"] == row["id"]


def test_record_tool_access_requires_tool_and_decision(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    with pytest.raises(ValueError):
        record_tool_access(db_path, tool_name="", decision="allowed")
    with pytest.raises(ValueError):
        record_tool_access(db_path, tool_name="adapter:x", decision="")
