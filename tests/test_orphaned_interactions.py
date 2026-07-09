"""Orphaned-interaction hygiene: pending escalations whose question no longer
applies get cancelled by the heartbeat instead of waiting forever (or being
auto-accepted by the autonomy policy)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from aiteam.db.interactions import create_interaction, get_interaction
from aiteam.db.liveness import reconcile_orphaned_interactions
from aiteam.db.migration import SCHEMA_PATH


def _init_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type)"
            " VALUES ('role:lead', 'lead', 'Lead', 'lead', 'manual')"
        )
        for issue_id, status in (
            ("issue:parent", "in_progress"),
            ("issue:done", "done"),
            ("issue:child", "cancelled"),
        ):
            conn.execute(
                "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
                " VALUES (?, 'goal-1', ?, ?, 'lead', 'role:lead')",
                (issue_id, issue_id, status),
            )
        conn.commit()
    return db_path


def test_cancels_interaction_on_terminal_issue(tmp_path: Path) -> None:
    db_path = _init_db(tmp_path)
    created = create_interaction(
        db_path,
        issue_id="issue:done",
        kind="request_confirmation",
        payload={"reason": "lead_wants_file_read"},
    )

    cancelled = reconcile_orphaned_interactions(db_path)

    assert cancelled == [created["id"]]
    row = get_interaction(db_path, interaction_id=created["id"])
    assert row is not None and row["status"] == "cancelled"
    # cancel never wakes anyone
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wakeup_requests").fetchone()[0] == 0


def test_cancels_subtree_stalled_when_children_unblocked(tmp_path: Path) -> None:
    """The real capa-2 case: 'Subtree stalled' pending since a day earlier,
    pointing at a blocked child that had long been cancelled."""
    db_path = _init_db(tmp_path)
    created = create_interaction(
        db_path,
        issue_id="issue:parent",  # parent itself still in_progress
        kind="request_confirmation",
        payload={"escalation_reason": "subtree_stalled", "blocked_child_ids": ["issue:child"]},
    )

    cancelled = reconcile_orphaned_interactions(db_path)

    assert cancelled == [created["id"]]


def test_keeps_live_escalations_pending(tmp_path: Path) -> None:
    db_path = _init_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:blocked', 'goal-1', 'Blocked child', 'blocked', 'engineer', 'role:lead')"
        )
        conn.commit()
    still_stalled = create_interaction(
        db_path,
        issue_id="issue:parent",
        kind="request_confirmation",
        payload={"escalation_reason": "subtree_stalled", "blocked_child_ids": ["issue:blocked"]},
        interaction_id="int-stalled",
    )
    live_question = create_interaction(
        db_path,
        issue_id="issue:parent",
        kind="request_confirmation",
        payload={"reason": "lead_wants_file_read"},
        interaction_id="int-question",
    )

    assert reconcile_orphaned_interactions(db_path) == []
    assert get_interaction(db_path, interaction_id=still_stalled["id"])["status"] == "pending"
    assert get_interaction(db_path, interaction_id=live_question["id"])["status"] == "pending"
