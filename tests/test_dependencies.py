from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aiteam.db.dependencies import (
    add_dependency,
    all_blockers_resolved,
    list_blocked_issues,
    list_dependencies,
    remove_dependency,
    resolve_blocker_wakeups,
)
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup as _enqueue  # noqa: F401 (used transitively)


def _init(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority) VALUES ('a1', 'engineer', 'E', 'standard')"
        )
        for iid, status in [("A", "todo"), ("B", "todo"), ("C", "done")]:
            conn.execute(
                "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) VALUES (?, 'g1', ?, ?, 'a1')",
                (iid, f"Issue {iid}", status),
            )
    return db


def test_add_and_list_dependency(tmp_path):
    db = _init(tmp_path)
    row = add_dependency(db, issue_id="A", depends_on_issue_id="B")
    assert row["issue_id"] == "A"
    assert row["depends_on_issue_id"] == "B"
    assert row["relation_type"] == "blocks"

    deps = list_dependencies(db, issue_id="A")
    assert len(deps) == 1
    assert deps[0]["depends_on_issue_id"] == "B"
    assert deps[0]["blocker_title"] == "Issue B"


def test_list_blocked_issues(tmp_path):
    db = _init(tmp_path)
    add_dependency(db, issue_id="A", depends_on_issue_id="B")
    blocked = list_blocked_issues(db, depends_on_issue_id="B")
    assert len(blocked) == 1
    assert blocked[0]["issue_id"] == "A"


def test_self_dependency_raises(tmp_path):
    db = _init(tmp_path)
    with pytest.raises(ValueError):
        add_dependency(db, issue_id="A", depends_on_issue_id="A")


def test_duplicate_dependency_raises(tmp_path):
    db = _init(tmp_path)
    add_dependency(db, issue_id="A", depends_on_issue_id="B")
    with pytest.raises(sqlite3.IntegrityError):
        add_dependency(db, issue_id="A", depends_on_issue_id="B")


def test_remove_dependency(tmp_path):
    db = _init(tmp_path)
    add_dependency(db, issue_id="A", depends_on_issue_id="B")
    deleted = remove_dependency(db, issue_id="A", depends_on_issue_id="B")
    assert deleted is True
    assert list_dependencies(db, issue_id="A") == []


def test_remove_nonexistent_returns_false(tmp_path):
    db = _init(tmp_path)
    deleted = remove_dependency(db, issue_id="A", depends_on_issue_id="B")
    assert deleted is False


def test_all_blockers_resolved_no_deps(tmp_path):
    db = _init(tmp_path)
    assert all_blockers_resolved(db, issue_id="A") is True


def test_all_blockers_resolved_unresolved(tmp_path):
    db = _init(tmp_path)
    add_dependency(db, issue_id="A", depends_on_issue_id="B")
    assert all_blockers_resolved(db, issue_id="A") is False


def test_all_blockers_resolved_done_blocker(tmp_path):
    db = _init(tmp_path)
    add_dependency(db, issue_id="A", depends_on_issue_id="C")  # C is done
    assert all_blockers_resolved(db, issue_id="A") is True


def test_resolve_blocker_wakeups_enqueues(tmp_path):
    db = _init(tmp_path)
    add_dependency(db, issue_id="A", depends_on_issue_id="B")
    # Mark B as done
    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE issues SET status = 'done' WHERE id = 'B'")
    woken = resolve_blocker_wakeups(db, resolved_issue_id="B")
    assert "A" in woken
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        wakeups = [dict(r) for r in conn.execute(
            "SELECT * FROM wakeup_requests WHERE agent_id = 'a1'"
        ).fetchall()]
    assert any(w["reason"] == "blockers_resolved" for w in wakeups)


def test_resolve_blocker_wakeups_skips_unresolved(tmp_path):
    db = _init(tmp_path)
    # A depends on both B and C; C is done but B is still todo
    add_dependency(db, issue_id="A", depends_on_issue_id="B")
    add_dependency(db, issue_id="A", depends_on_issue_id="C")
    # Mark only C done — B still open
    woken = resolve_blocker_wakeups(db, resolved_issue_id="C")
    assert "A" not in woken  # B still blocks A


def test_resolve_blocker_wakeups_idempotent(tmp_path):
    db = _init(tmp_path)
    add_dependency(db, issue_id="A", depends_on_issue_id="B")
    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE issues SET status = 'done' WHERE id = 'B'")
    resolve_blocker_wakeups(db, resolved_issue_id="B")
    resolve_blocker_wakeups(db, resolved_issue_id="B")
    with sqlite3.connect(str(db)) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE agent_id='a1' AND reason='blockers_resolved'"
        ).fetchone()[0]
    assert count == 1  # idempotency_key prevents duplicate


def test_reconciler_skips_blocked_issues(tmp_path):
    from aiteam.db.liveness import reconcile_unqueued_assigned_issues
    db = _init(tmp_path)
    add_dependency(db, issue_id="A", depends_on_issue_id="B")  # A blocked by B (todo)
    recovered = reconcile_unqueued_assigned_issues(db)
    assert "A" not in recovered  # A should not be re-enqueued while B is todo
    assert "B" in recovered      # B itself has no blockers and should be enqueued


def test_default_child_dependencies_order_test_runner_after_engineer(tmp_path):
    """Visto en vivo (CLI Gastos, 2026-07-15): el test_runner despertaba al
    asignarse y ejecutaba la suite contra un workspace vacio porque el sync de
    dependencias por defecto solo ordenaba reviewer/qa tras engineer."""
    from aiteam.db.dependencies import sync_default_child_dependencies

    db = tmp_path / "test.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority) VALUES ('a1', 'lead', 'L', 'lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id)"
            " VALUES ('parent', 'g1', 'Parent', 'in_progress', 'a1')"
        )
        for iid, role in [("eng", "engineer"), ("rev", "reviewer"), ("runner", "test_runner")]:
            conn.execute(
                "INSERT INTO issues (id, goal_id, parent_id, title, status, role)"
                " VALUES (?, 'g1', 'parent', ?, 'todo', ?)",
                (iid, f"Issue {iid}", role),
            )

    created = sync_default_child_dependencies(db, parent_issue_id="parent")

    pairs = {(row["issue_id"], row["depends_on_issue_id"]) for row in created}
    assert ("rev", "eng") in pairs
    assert ("runner", "eng") in pairs, "test_runner debe esperar al artefacto del engineer"
    assert ("eng", "rev") not in pairs and ("eng", "runner") not in pairs


def test_builtin_test_runner_strips_ansi_from_evidence():
    from aiteam.heartbeat.executor import RunExecutor

    colored = "\x1b[32m\x1b[1m16 passed\x1b[0m\x1b[32m in 0.18s\x1b[0m"
    assert RunExecutor._ANSI_RE.sub("", colored) == "16 passed in 0.18s"
