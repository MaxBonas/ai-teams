from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aiteam.db.liveness import (
    diagnose_issue,
    reconcile_unassigned_role_issues,
    reconcile_unqueued_assigned_issues,
)
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup


def _init(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(schema)
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority) VALUES ('a1', 'engineer', 'E', 'standard')"
        )
    return db


def _insert_issue(db: Path, *, issue_id: str, status: str = "todo", assignee: str | None = "a1", parent_id: str | None = None):
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id, parent_id) VALUES (?, 'g1', ?, ?, ?, ?)",
            (issue_id, issue_id, status, assignee, parent_id),
        )


def _insert_run(db: Path, *, run_id: str, issue_id: str, status: str = "running"):
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status, invocation_source) VALUES (?, 'a1', ?, ?, 'test')",
            (run_id, issue_id, status),
        )


def _insert_interaction(db: Path, *, issue_id: str, kind: str = "request_confirmation", status: str = "pending"):
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO issue_thread_interactions (id, issue_id, kind, status, payload_json, continuation_policy) "
            "VALUES (?, ?, ?, ?, '{}', 'wake_assignee')",
            (f"int-{issue_id}", issue_id, kind, status),
        )


# --- diagnose_issue ---

def test_diagnose_not_found(tmp_path):
    db = _init(tmp_path)
    d = diagnose_issue(db, issue_id="missing")
    assert d["live"] is False
    assert "not found" in d["reason"]


def test_diagnose_terminal_done(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="i1", status="done")
    d = diagnose_issue(db, issue_id="i1")
    assert d["live"] is True
    assert "terminal" in d["paths"]


def test_diagnose_terminal_cancelled(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="i1", status="cancelled")
    d = diagnose_issue(db, issue_id="i1")
    assert d["live"] is True


def test_diagnose_live_via_active_run(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="i1")
    _insert_run(db, run_id="r1", issue_id="i1", status="running")
    d = diagnose_issue(db, issue_id="i1")
    assert d["live"] is True
    assert "active_run" in d["paths"]


def test_diagnose_live_via_queued_wakeup(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="i1")
    enqueue_wakeup(db, agent_id="a1", source="test", reason="test", payload={"issue_id": "i1"})
    d = diagnose_issue(db, issue_id="i1")
    assert d["live"] is True
    assert "queued_wakeup" in d["paths"]


def test_diagnose_live_via_pending_interaction(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="i1")
    _insert_interaction(db, issue_id="i1")
    d = diagnose_issue(db, issue_id="i1")
    assert d["live"] is True
    assert "pending_interaction" in d["paths"]


def test_diagnose_live_via_children(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="parent")
    _insert_issue(db, issue_id="child", parent_id="parent")
    d = diagnose_issue(db, issue_id="parent")
    assert d["live"] is True
    assert "children_live" in d["paths"]


def test_diagnose_dead_no_path(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="i1", status="in_progress")
    d = diagnose_issue(db, issue_id="i1")
    assert d["live"] is False
    assert "no_live_path" in d["blockers"]


def test_diagnose_dead_no_assignee(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="i1", assignee=None)
    d = diagnose_issue(db, issue_id="i1")
    assert d["live"] is False
    assert "no_assignee" in d["blockers"]


# --- reconcile_unqueued_assigned_issues ---

def test_reconcile_enqueues_orphaned(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="i1")
    recovered = reconcile_unqueued_assigned_issues(db)
    assert "i1" in recovered


def test_reconcile_skips_already_live(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="i1")
    enqueue_wakeup(db, agent_id="a1", source="test", reason="test", payload={"issue_id": "i1"})
    recovered = reconcile_unqueued_assigned_issues(db)
    assert "i1" not in recovered


def test_reconcile_skips_terminal(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="i1", status="done")
    recovered = reconcile_unqueued_assigned_issues(db)
    assert recovered == []


def test_reconcile_idempotent(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="i1")
    first = reconcile_unqueued_assigned_issues(db)
    second = reconcile_unqueued_assigned_issues(db)
    assert "i1" in first
    assert second == []  # idempotency_key prevents re-enqueue


def test_reconcile_unassigned_role_issue_materializes_agent_and_wakeup(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="i1", assignee=None)
    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE issues SET role = 'reviewer' WHERE id = 'i1'")

    recovered = reconcile_unassigned_role_issues(db)

    assert recovered == ["i1"]
    with sqlite3.connect(str(db)) as conn:
        issue = conn.execute("SELECT assignee_agent_id FROM issues WHERE id = 'i1'").fetchone()
        agent = conn.execute("SELECT role, adapter_type FROM agents WHERE id = 'role:reviewer'").fetchone()
        wakeup = conn.execute(
            "SELECT agent_id, payload_json FROM wakeup_requests WHERE agent_id = 'role:reviewer'"
        ).fetchone()
    assert issue[0] == "role:reviewer"
    assert agent == ("reviewer", "role_builtin")
    assert wakeup[0] == "role:reviewer"
    assert '"issue_id": "i1"' in wakeup[1]


def test_reconcile_unassigned_role_issue_idempotent(tmp_path):
    db = _init(tmp_path)
    _insert_issue(db, issue_id="i1", assignee=None)
    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE issues SET role = 'engineer' WHERE id = 'i1'")

    first = reconcile_unassigned_role_issues(db)
    second = reconcile_unassigned_role_issues(db)

    assert first == ["i1"]
    assert second == []
