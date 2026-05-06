"""Tests for idle-loop prevention in the liveness reconciler.

The core bug: when an issue has a pending user interaction (e.g. a team
proposal waiting for approval), _live_issue_ids() did NOT include it in
the live set.  This caused reconcile_unqueued_assigned_issues to re-enqueue
a wakeup every 30 s, making the Lead wake up just to skip with
no_pending_lead_work — a silent idle loop.

Fix: _live_issue_ids now also queries issue_thread_interactions WHERE
status = 'pending', treating those issues as live (waiting for user input).
"""
from __future__ import annotations

import sqlite3
import json
from pathlib import Path

import pytest

from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.liveness import reconcile_unqueued_assigned_issues, reconcile_stalled_subtrees
from aiteam.db.wakeups import enqueue_wakeup


# ── DB helpers ────────────────────────────────────────────────────────────────

def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type, budget_monthly_cents)"
            " VALUES (?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Team Lead", "lead_builtin", 0),
        )
        conn.commit()


def _add_issue(
    db_path: Path,
    *,
    issue_id: str,
    status: str = "in_progress",
    assignee_agent_id: str = "role:lead",
    parent_id: str | None = None,
) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id, parent_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (issue_id, "goal-1", issue_id, status, "lead", assignee_agent_id, parent_id),
        )
        conn.commit()


def _add_pending_interaction(db_path: Path, *, issue_id: str, kind: str = "suggest_tasks") -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO issue_thread_interactions
                (id, issue_id, kind, title, summary, status, payload_json, idempotency_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"iti:{issue_id}:{kind}",
                issue_id,
                kind,
                "Pending interaction",
                "Pending",
                "pending",
                json.dumps({}),
                f"test:{issue_id}:{kind}",
            ),
        )
        conn.commit()


def _add_resolved_interaction(db_path: Path, *, issue_id: str) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO issue_thread_interactions
                (id, issue_id, kind, title, summary, status, payload_json, idempotency_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"iti:{issue_id}:resolved",
                issue_id,
                "suggest_tasks",
                "Resolved interaction",
                "Resolved",
                "accepted",  # not pending
                json.dumps({}),
                f"test:{issue_id}:resolved",
            ),
        )
        conn.commit()


def _queued_wakeup_count(db_path: Path, *, agent_id: str) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE agent_id = ? AND status = 'queued'",
            (agent_id,),
        ).fetchone()
    return int(row[0])


# ── Tests: pending interaction blocks reconciler ──────────────────────────────

class TestPendingInteractionBlocksReconciler:
    """Issues waiting on a pending interaction must NOT be re-enqueued."""

    def test_no_wakeup_when_proposal_pending(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_progress")
        _add_pending_interaction(db_path, issue_id="issue:intake", kind="suggest_tasks")

        enqueued = reconcile_unqueued_assigned_issues(db_path)

        assert "issue:intake" not in enqueued, (
            "Reconciler should NOT re-enqueue an issue that has a pending interaction"
        )
        assert _queued_wakeup_count(db_path, agent_id="role:lead") == 0

    def test_no_wakeup_when_confirmation_pending(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_progress")
        _add_pending_interaction(db_path, issue_id="issue:intake", kind="request_confirmation")

        enqueued = reconcile_unqueued_assigned_issues(db_path)

        assert "issue:intake" not in enqueued
        assert _queued_wakeup_count(db_path, agent_id="role:lead") == 0

    def test_wakeup_created_when_interaction_resolved(self, tmp_path: Path) -> None:
        """Once the interaction is resolved (accepted/rejected), the issue is no longer
        live via interactions — if there's no active wakeup/run it should be re-enqueued."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_progress")
        _add_resolved_interaction(db_path, issue_id="issue:intake")
        # No children (so it's a leaf from the reconciler's perspective), no pending wakeup

        enqueued = reconcile_unqueued_assigned_issues(db_path)

        assert "issue:intake" in enqueued, (
            "Resolved interactions should not keep the issue live — reconciler should re-enqueue"
        )

    def test_wakeup_created_when_no_interaction_at_all(self, tmp_path: Path) -> None:
        """Fresh issue with no interaction → should be enqueued (normal case)."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_progress")

        enqueued = reconcile_unqueued_assigned_issues(db_path)

        assert "issue:intake" in enqueued

    def test_idempotent_with_existing_queued_wakeup(self, tmp_path: Path) -> None:
        """If a wakeup is already queued, reconciler should NOT create another one."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_progress")
        enqueue_wakeup(
            db_path,
            agent_id="role:lead",
            source="manual",
            reason="manual",
            payload={"issue_id": "issue:intake"},
            idempotency_key="assignment:issue:intake:role:lead",
        )

        enqueued = reconcile_unqueued_assigned_issues(db_path)

        assert "issue:intake" not in enqueued
        assert _queued_wakeup_count(db_path, agent_id="role:lead") == 1  # still just the one


class TestStallReconcilerWithPendingInteraction:
    """reconcile_stalled_subtrees should also respect pending interactions."""

    def test_stalled_subtree_not_escalated_when_parent_has_pending_interaction(
        self, tmp_path: Path
    ) -> None:
        """Parent has a pending cycle-review interaction: already waiting for user.
        The stall reconciler should not add a redundant escalation wakeup."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        # Parent issue in_progress
        _add_issue(db_path, issue_id="issue:intake", status="in_progress")
        # Child blocked
        _add_issue(db_path, issue_id="issue:child", status="blocked", parent_id="issue:intake")
        # Parent already has a pending confirmation interaction (cycle review, say)
        _add_pending_interaction(db_path, issue_id="issue:intake", kind="request_confirmation")

        escalated = reconcile_stalled_subtrees(db_path)

        assert "issue:intake" not in escalated, (
            "Stall reconciler should not escalate a subtree whose parent already has "
            "a pending user interaction"
        )
        assert _queued_wakeup_count(db_path, agent_id="role:lead") == 0

    def test_stalled_subtree_escalated_when_no_pending_interaction(
        self, tmp_path: Path
    ) -> None:
        """No pending interaction → stall reconciler should escalate normally."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_progress")
        _add_issue(db_path, issue_id="issue:child", status="blocked", parent_id="issue:intake")

        escalated = reconcile_stalled_subtrees(db_path)

        assert "issue:intake" in escalated
        assert _queued_wakeup_count(db_path, agent_id="role:lead") == 1


class TestMultiplePendingInteractions:
    """Multiple interactions in different states — only pending ones count."""

    def test_one_pending_one_resolved_still_live(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_progress")
        _add_resolved_interaction(db_path, issue_id="issue:intake")
        _add_pending_interaction(db_path, issue_id="issue:intake", kind="suggest_tasks")

        enqueued = reconcile_unqueued_assigned_issues(db_path)

        # The pending interaction makes it live even though there's also a resolved one
        assert "issue:intake" not in enqueued


class TestDurableStallEscalation:
    """reconcile_stalled_subtrees now also creates a durable request_confirmation
    interaction in addition to enqueueing a wakeup."""

    def test_stall_creates_durable_interaction(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_progress")
        _add_issue(db_path, issue_id="issue:child", status="blocked", parent_id="issue:intake")

        escalated = reconcile_stalled_subtrees(db_path)

        assert "issue:intake" in escalated
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM issue_thread_interactions WHERE issue_id = ? AND status = 'pending'",
                ("issue:intake",),
            ).fetchone()
        assert row is not None, "Durable escalation interaction must be created"
        assert row["kind"] == "request_confirmation"
        payload = json.loads(row["payload_json"])
        assert "blocked_child_ids" in payload
        assert "issue:child" in payload["blocked_child_ids"]

    def test_stall_interaction_idempotent_on_double_call(self, tmp_path: Path) -> None:
        """Two consecutive reconcile calls must not create duplicate interactions."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_progress")
        _add_issue(db_path, issue_id="issue:child", status="blocked", parent_id="issue:intake")

        reconcile_stalled_subtrees(db_path)
        reconcile_stalled_subtrees(db_path)

        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM issue_thread_interactions WHERE issue_id = ?",
                ("issue:intake",),
            ).fetchone()[0]
        assert count == 1, "Exactly one durable escalation interaction (idempotent)"

    def test_stall_interaction_makes_issue_live_on_second_call(self, tmp_path: Path) -> None:
        """After first escalation, the pending interaction makes the issue live, so
        the second reconcile call should NOT create a new wakeup."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_progress")
        _add_issue(db_path, issue_id="issue:child", status="blocked", parent_id="issue:intake")

        reconcile_stalled_subtrees(db_path)
        # Now the interaction is pending → issue is live → reconciler skips it
        escalated_second = reconcile_stalled_subtrees(db_path)

        assert "issue:intake" not in escalated_second
        # Only one wakeup ever created
        assert _queued_wakeup_count(db_path, agent_id="role:lead") == 1


class TestInReviewLiveness:
    """in_review issues without a live path must be re-enqueued by the reconciler.

    EXECUTION_SEMANTICS.md: an in_review issue with no pending interaction,
    no active run, and no queued wakeup is a silent stall.
    """

    def test_in_review_without_live_path_is_enqueued(self, tmp_path):
        """A leaf in_review issue with no pending interaction/wakeup must be woken."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_review")

        enqueued = reconcile_unqueued_assigned_issues(db_path)

        assert "issue:intake" in enqueued, (
            "in_review issue with no live path must be re-enqueued"
        )

    def test_in_review_with_pending_interaction_not_enqueued(self, tmp_path):
        """If in_review has a pending interaction, it is live — do NOT re-enqueue."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_review")
        _add_pending_interaction(db_path, issue_id="issue:intake", kind="request_confirmation")

        enqueued = reconcile_unqueued_assigned_issues(db_path)

        assert "issue:intake" not in enqueued

    def test_in_review_with_queued_wakeup_not_enqueued(self, tmp_path):
        """If in_review already has a queued wakeup, reconciler must not add another."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_review")
        enqueue_wakeup(
            db_path,
            agent_id="role:lead",
            source="manual",
            reason="manual",
            payload={"issue_id": "issue:intake"},
            idempotency_key="assignment:issue:intake:role:lead",
        )

        enqueued = reconcile_unqueued_assigned_issues(db_path)

        assert "issue:intake" not in enqueued
        assert _queued_wakeup_count(db_path, agent_id="role:lead") == 1

    def test_in_review_parent_with_all_blocked_children_escalates(self, tmp_path):
        """in_review parent + all children blocked → stall escalation fires."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_issue(db_path, issue_id="issue:intake", status="in_review")
        _add_issue(db_path, issue_id="issue:child", status="blocked", parent_id="issue:intake")

        escalated = reconcile_stalled_subtrees(db_path)

        assert "issue:intake" in escalated
