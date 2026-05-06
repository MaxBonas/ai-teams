from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.runs import append_run_event, create_run, finish_run, mark_run_running


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name) VALUES (?, ?, ?)",
            ("role:engineer", "engineer", "Engineer"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, status, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("issue-1", "goal-1", "Implement", "todo", "role:engineer"),
        )
        conn.commit()


def test_create_run_records_context_and_economics(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    row = create_run(
        db_path,
        run_id="run-1",
        agent_id="role:engineer",
        issue_id="issue-1",
        profile="full_team",
        invocation_source="wakeup",
        trigger_detail="assignment",
        adapter_type="openai_api",
        provider="openai",
        model="gpt-5-mini",
        channel="api",
        context_snapshot={"wake_reason": "assignment"},
        cost_policy={"delegation_allowed": True},
        delegation_reason="bounded implementation",
        complexity="medium",
        estimated_cost_cents=4,
        estimated_savings_cents=12,
    )

    assert row["status"] == "queued"
    assert row["profile"] == "full_team"
    assert row["channel"] == "api"
    assert row["delegation_reason"] == "bounded implementation"
    assert row["estimated_cost_cents"] == 4
    assert row["estimated_savings_cents"] == 12
    assert json.loads(row["context_snapshot_json"]) == {"wake_reason": "assignment"}
    assert json.loads(row["cost_policy_json"]) == {"delegation_allowed": True}


def test_create_run_is_idempotent_by_run_id(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    first = create_run(db_path, run_id="run-1", agent_id="role:engineer")
    second = create_run(
        db_path,
        run_id="run-1",
        agent_id="role:engineer",
        provider="ignored",
    )

    assert first["id"] == second["id"] == "run-1"
    assert second["provider"] is None


def test_run_lifecycle_and_events_are_durable(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    create_run(db_path, run_id="run-1", agent_id="role:engineer", issue_id="issue-1")

    running = mark_run_running(db_path, run_id="run-1", process_pid=123)
    first_event = append_run_event(
        db_path,
        run_id="run-1",
        event_type="stdout",
        stream="stdout",
        payload={"text": "hello"},
    )
    second_event = append_run_event(
        db_path,
        run_id="run-1",
        event_type="usage",
        payload={"input_tokens": 10},
    )
    finished = finish_run(
        db_path,
        run_id="run-1",
        status="completed",
        result={"summary": "done"},
        usage={"input_tokens": 10, "output_tokens": 5},
        exit_code=0,
        actual_cost_cents=3,
    )

    assert running is not None
    assert running["status"] == "running"
    assert running["process_pid"] == 123
    assert first_event["seq"] == 1
    assert json.loads(first_event["payload_json"]) == {"text": "hello"}
    assert second_event["seq"] == 2
    assert finished is not None
    assert finished["status"] == "completed"
    assert finished["exit_code"] == 0
    assert finished["actual_cost_cents"] == 3
    assert json.loads(finished["usage_json"]) == {"input_tokens": 10, "output_tokens": 5}
    assert json.loads(finished["result_json"]) == {"summary": "done"}


def test_finish_run_rejects_non_terminal_status(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    create_run(db_path, run_id="run-1", agent_id="role:engineer")

    with pytest.raises(ValueError):
        finish_run(db_path, run_id="run-1", status="running")
