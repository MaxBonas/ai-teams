"""reconcile_orphaned_children_of_closed_parents — a parent can close (done/
cancelled) while a child stays open. reconcile_stalled_subtrees only watches
in_progress/in_review parents, so once the parent itself closes nothing ever
escalates the leftover child again.

Live case: root issue a0539b99 closed 'done' (accepting the user's directive
B) while its reviewer child 5d50353c stayed 'blocked' with a genuine,
unresolved compile-error finding. Manually waking the Lead on a DIFFERENT
issue (issue:intake, itself correctly empty) never surfaced this — nothing
pointed the Lead at the orphaned child.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from aiteam.db.interactions import get_interaction, resolve_interaction
from aiteam.db.liveness import reconcile_orphaned_children_of_closed_parents, reconcile_orphaned_interactions
from aiteam.db.migration import SCHEMA_PATH


def _init_db(tmp_path: Path, *, parent_status: str = "done", child_status: str = "blocked") -> Path:
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES"
            " ('role:lead', 'lead', 'Lead', 'lead_builtin'),"
            " ('role:reviewer', 'reviewer', 'Reviewer', 'subscription_cli')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:parent', 'goal-1', 'Root task', ?, 'lead', 'role:lead')",
            (parent_status,),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:child', 'goal-1', 'issue:parent', 'Canonical review', ?, 'reviewer', 'role:reviewer')",
            (child_status,),
        )
        conn.commit()
    return db_path


def test_escalates_blocked_child_under_done_parent(tmp_path: Path) -> None:
    db_path = _init_db(tmp_path, parent_status="done", child_status="blocked")

    enqueued = reconcile_orphaned_children_of_closed_parents(db_path)

    assert "issue:parent" in enqueued
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        wakeup = conn.execute(
            "SELECT agent_id, reason, payload_json FROM wakeup_requests WHERE reason = 'parent_closed_child_open'"
        ).fetchone()
        interaction = conn.execute(
            "SELECT title, payload_json FROM issue_thread_interactions WHERE issue_id = 'issue:parent'"
        ).fetchone()
    assert wakeup is not None
    assert wakeup["agent_id"] == "role:lead"  # the closed parent's own assignee
    payload = json.loads(wakeup["payload_json"])
    assert payload["open_child_ids"] == ["issue:child"]
    assert interaction is not None
    assert "abierto" in interaction["title"]


def test_no_escalation_when_parent_still_open(tmp_path: Path) -> None:
    db_path = _init_db(tmp_path, parent_status="in_progress", child_status="blocked")

    assert reconcile_orphaned_children_of_closed_parents(db_path) == []


def test_no_escalation_when_child_also_terminal(tmp_path: Path) -> None:
    db_path = _init_db(tmp_path, parent_status="done", child_status="cancelled")

    assert reconcile_orphaned_children_of_closed_parents(db_path) == []


def test_idempotent_same_gap_not_re_escalated(tmp_path: Path) -> None:
    db_path = _init_db(tmp_path, parent_status="done", child_status="blocked")

    first = reconcile_orphaned_children_of_closed_parents(db_path)
    second = reconcile_orphaned_children_of_closed_parents(db_path)

    assert first == ["issue:parent"]
    assert second == []


def test_interaction_survives_the_orphan_cleanup_reconciler(tmp_path: Path) -> None:
    """The escalation is deliberately attached to a TERMINAL issue_id (the
    closed parent) — the orphan-cleanup reconciler must not treat that as
    staleness and cancel it out from under the Lead."""
    db_path = _init_db(tmp_path, parent_status="done", child_status="blocked")
    reconcile_orphaned_children_of_closed_parents(db_path)

    cancelled = reconcile_orphaned_interactions(db_path)

    assert cancelled == []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM issue_thread_interactions WHERE issue_id = 'issue:parent'"
        ).fetchone()
    assert row["status"] == "pending"


def test_wake_on_resolve_targets_the_closed_parents_assignee(tmp_path: Path) -> None:
    db_path = _init_db(tmp_path, parent_status="done", child_status="blocked")
    reconcile_orphaned_children_of_closed_parents(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        interaction_id = conn.execute(
            "SELECT id FROM issue_thread_interactions WHERE issue_id = 'issue:parent'"
        ).fetchone()["id"]

    resolve_interaction(db_path, interaction_id=interaction_id, action="accept", resolved_by_user_id="user")

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        wakeup = conn.execute(
            "SELECT agent_id FROM wakeup_requests WHERE reason = 'interaction_resolved'"
        ).fetchone()
    assert wakeup is not None
    assert wakeup["agent_id"] == "role:lead"


def test_orphan_cleanup_cancels_once_child_resolves(tmp_path: Path) -> None:
    db_path = _init_db(tmp_path, parent_status="done", child_status="blocked")
    reconcile_orphaned_children_of_closed_parents(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE issues SET status = 'done' WHERE id = 'issue:child'")
        conn.commit()

    cancelled = reconcile_orphaned_interactions(db_path)

    assert len(cancelled) == 1
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM issue_thread_interactions WHERE issue_id = 'issue:parent'"
        ).fetchone()
    assert row["status"] == "cancelled"
