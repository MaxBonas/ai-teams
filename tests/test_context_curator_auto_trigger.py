"""Tests for context_curator auto-trigger logic (_maybe_spawn_context_curator).

Conditions for auto-spawn:
  C1. Parent issue comment count ≥ _CONTEXT_CURATOR_COMMENT_THRESHOLD (8)
  C2. No 'plan' document exists for the issue
  C3. No non-terminal context_curator child exists

Tests are organised as:
  - Unit: each condition in isolation (threshold below/at/above, plan doc blocks,
    active curator blocks, done/cancelled curator does NOT block)
  - Integration: trigger fires on a real child_report wakeup; full executor run
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aiteam.adapters.registry import AdapterDescriptor, AdapterRegistry, ExecutionResult
from aiteam.db.documents import put_document
from aiteam.db.interactions import list_interactions
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


# ── Shared DB helpers ────────────────────────────────────────────────────────

def _init_db(db_path: Path) -> None:
    """Create a minimal DB with a lead + parent issue + child engineer (done)."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Lead", "lead", "lead_builtin"),
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("role:engineer", "engineer", "Engineer", "standard", "openai_api", "role:lead"),
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("role:reviewer", "reviewer", "Reviewer", "standard", "openai_api", "role:lead"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("issue:intake", "goal-1", "Build the app", "in_progress", "lead", "role:lead"),
        )
        # A done engineer child (so child_report can fire) with a valid AGENT-REPORT
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("issue:intake:eng", "goal-1", "issue:intake", "Engineer task",
             "done", "engineer", "role:engineer"),
        )
        _ENGINEER_DONE = (
            "Implementación completada.\n\n"
            "---AGENT-REPORT---\n"
            "role: engineer\n"
            "result: done\n"
            "issue_status: done\n"
            "next_owner: reviewer\n"
            "tech_match: yes\n"
            "blocker: none\n"
            "evidence: src/main.py:1-50\n"
        )
        conn.execute(
            "INSERT INTO issue_comments (issue_id, author_agent_id, body) VALUES (?, ?, ?)",
            ("issue:intake:eng", "role:engineer", _ENGINEER_DONE),
        )
        # A done reviewer child with a valid "done" AGENT-REPORT so _all_children_done
        # returns True and the cycle-close path is available.
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("issue:intake:rev", "goal-1", "issue:intake", "Reviewer task",
             "done", "reviewer", "role:reviewer"),
        )
        _REVIEWER_DONE = (
            "Revisión completada.\n\n"
            "---AGENT-REPORT---\n"
            "role: reviewer\n"
            "result: done\n"
            "issue_status: done\n"
            "next_owner: lead\n"
            "tech_match: yes\n"
            "blocker: none\n"
            "evidence: src/main.py:1-50\n"
        )
        conn.execute(
            "INSERT INTO issue_comments (issue_id, author_agent_id, body) VALUES (?, ?, ?)",
            ("issue:intake:rev", "role:reviewer", _REVIEWER_DONE),
        )
        conn.commit()


def _add_parent_comments(db_path: Path, count: int) -> None:
    """Add *count* comments to the parent issue (issue:intake)."""
    with sqlite3.connect(str(db_path)) as conn:
        for i in range(count):
            conn.execute(
                "INSERT INTO issue_comments (issue_id, author_agent_id, body) VALUES (?, ?, ?)",
                ("issue:intake", "role:lead", f"Progress update #{i + 1}"),
            )
        conn.commit()


def _count_curator_children(db_path: Path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM issues"
            " WHERE parent_id = 'issue:intake' AND lower(role) = 'context_curator'",
        ).fetchone()[0]


def _curator_status(db_path: Path) -> str | None:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT status FROM issues"
            " WHERE parent_id = 'issue:intake' AND lower(role) = 'context_curator'"
            " ORDER BY rowid DESC LIMIT 1",
        ).fetchone()
    return row[0] if row else None


def _dispatch_child_report(db_path: Path) -> Any:
    """Enqueue a child_report wakeup for the lead and dispatch it."""
    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="child_report",
        reason="child_report",
        payload={"issue_id": "issue:intake", "wake_reason": "child_report"},
    )
    return HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")


def _run_lead(db_path: Path) -> None:
    from aiteam.adapters.registry import build_default_registry
    dispatch = _dispatch_child_report(db_path)
    assert dispatch is not None, "Lead wakeup should be dispatched"
    RunExecutor(db_path, build_default_registry()).execute(dispatch)


# ── Condition 1: comment threshold ───────────────────────────────────────────

class TestCommentThreshold:
    def test_no_curator_below_threshold(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        threshold = RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD
        # Add threshold-1 comments (just below)
        _add_parent_comments(db_path, threshold - 1)
        _run_lead(db_path)
        assert _count_curator_children(db_path) == 0, (
            "Curator should NOT be spawned below the comment threshold"
        )

    def test_curator_spawned_at_threshold(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        threshold = RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD
        _add_parent_comments(db_path, threshold)
        _run_lead(db_path)
        assert _count_curator_children(db_path) == 1, (
            "Curator should be spawned when comment count == threshold"
        )

    def test_curator_spawned_above_threshold(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        threshold = RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD
        _add_parent_comments(db_path, threshold + 5)
        _run_lead(db_path)
        assert _count_curator_children(db_path) == 1, (
            "Curator should be spawned when comment count > threshold"
        )

    def test_curator_is_todo_when_created(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        _run_lead(db_path)
        assert _curator_status(db_path) == "todo"


# ── Condition 2: plan document blocks auto-spawn ─────────────────────────────

class TestPlanDocumentBlocks:
    def test_no_curator_when_plan_doc_exists(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        # Insert a plan document — should prevent curator from being spawned
        put_document(
            db_path,
            issue_id="issue:intake",
            key="plan",
            title="Project Plan",
            body="## Phase 1\n- Implement feature\n",
        )
        _run_lead(db_path)
        assert _count_curator_children(db_path) == 0, (
            "Curator should NOT be spawned when a plan document already exists"
        )

    def test_curator_spawned_when_no_plan_doc(self, tmp_path: Path) -> None:
        """Complementary: no plan doc → curator spawned (confirming the guard is plan-specific)."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        # No plan document inserted
        _run_lead(db_path)
        assert _count_curator_children(db_path) == 1


# ── Condition 3: active curator child blocks duplicate spawn ─────────────────

class TestActiveCuratorIdempotency:
    def _add_curator_child(self, db_path: Path, status: str) -> None:
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("issue:intake:curator", "goal-1", "issue:intake",
                 "Context curator — sintetizar hilo del proyecto",
                 status, "context_curator", "role:lead"),
            )
            conn.commit()

    def test_no_second_curator_when_todo_exists(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        self._add_curator_child(db_path, "todo")
        _run_lead(db_path)
        assert _count_curator_children(db_path) == 1, (
            "Should not create a second curator when one is already todo"
        )

    def test_no_second_curator_when_in_progress(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        self._add_curator_child(db_path, "in_progress")
        _run_lead(db_path)
        assert _count_curator_children(db_path) == 1, (
            "Should not create a second curator when one is in_progress"
        )

    def test_new_curator_spawned_when_prior_is_done(self, tmp_path: Path) -> None:
        """A completed curator is terminal; the next long-thread wave should spawn a fresh one."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        self._add_curator_child(db_path, "done")
        _run_lead(db_path)
        # Two total: the pre-existing done one + the newly spawned todo one
        assert _count_curator_children(db_path) == 2, (
            "A done curator is terminal — a new one should be spawned for the next wave"
        )

    def test_new_curator_spawned_when_prior_is_cancelled(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        self._add_curator_child(db_path, "cancelled")
        _run_lead(db_path)
        assert _count_curator_children(db_path) == 2, (
            "A cancelled curator is terminal — a new one should be spawned"
        )


# ── Curator child properties ──────────────────────────────────────────────────

class TestCuratorChildProperties:
    def test_curator_role_is_context_curator(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        _run_lead(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT role FROM issues WHERE parent_id = 'issue:intake'"
                " AND lower(role) = 'context_curator' LIMIT 1",
            ).fetchone()
        assert row is not None
        assert row["role"].lower() == "context_curator"

    def test_curator_complexity_is_low(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        _run_lead(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT complexity FROM issues WHERE parent_id = 'issue:intake'"
                " AND lower(role) = 'context_curator' LIMIT 1",
            ).fetchone()
        assert row is not None
        assert str(row["complexity"] or "").lower() == "low"

    def test_curator_has_non_empty_title(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        _run_lead(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT title FROM issues WHERE parent_id = 'issue:intake'"
                " AND lower(role) = 'context_curator' LIMIT 1",
            ).fetchone()
        assert row is not None
        assert len(str(row["title"] or "").strip()) > 0

    def test_curator_description_mentions_hilo(self, tmp_path: Path) -> None:
        """Description should mention 'hilo' (thread) to orient the curator agent."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        _run_lead(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT description FROM issues WHERE parent_id = 'issue:intake'"
                " AND lower(role) = 'context_curator' LIMIT 1",
            ).fetchone()
        assert row is not None
        assert "hilo" in str(row["description"] or "").lower(), (
            "Curator description should mention 'hilo' (thread)"
        )

    def test_curator_parent_id_is_intake(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        _run_lead(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT parent_id FROM issues WHERE lower(role) = 'context_curator' LIMIT 1",
            ).fetchone()
        assert row is not None
        assert row["parent_id"] == "issue:intake"


# ── Does NOT interrupt child_report flow ─────────────────────────────────────

class TestDoesNotInterruptFlow:
    def test_cycle_close_deferred_while_curator_is_active(self, tmp_path: Path) -> None:
        """The auto-spawned curator becomes a non-terminal child, so _all_children_done
        returns False and the cycle-close interaction is NOT created in the same run.
        Cycle-close fires after the curator finishes — this test verifies the deferral."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        _run_lead(db_path)

        # Curator is now todo → _all_children_done is False → no cycle-close yet
        _TERMINAL = {"accepted", "rejected", "answered", "cancelled", "expired"}
        from aiteam.db.interactions import list_interactions
        interactions = list_interactions(db_path, issue_id="issue:intake")
        pending = [i for i in interactions if str(i.get("status") or "") not in _TERMINAL]
        reasons = [
            str((i.get("payload") or {}).get("reason") or "") for i in pending
        ]
        assert "initial_cycle_ready" not in reasons, (
            "cycle-close interaction should be deferred while the curator is still active"
        )
        # …and the curator itself is the pending non-terminal child
        assert _curator_status(db_path) == "todo"

    def test_parent_issue_not_cancelled_after_curator_spawn(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_parent_comments(db_path, RunExecutor._CONTEXT_CURATOR_COMMENT_THRESHOLD)
        _run_lead(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            parent = conn.execute(
                "SELECT status FROM issues WHERE id = 'issue:intake'",
            ).fetchone()
        assert parent["status"] not in {"cancelled", "done"}, (
            "Parent issue should remain in_progress after curator is spawned"
        )
