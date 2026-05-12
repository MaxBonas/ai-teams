"""Tests for the automatic reviewer changes_requested fix cycle.

Covers:
- _handle_reviewer_changes_requested: detect changes_requested, create fix engineer,
  reset reviewer to todo, skip if fix engineer already open
- Integration: child_report wake triggers fix cycle end-to-end
- _all_children_done: returns False when reviewer result == changes_requested
- No duplicate fix issues when called twice (open engineer gate)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aiteam.adapters.registry import AdapterRegistry, ExecutionResult, build_default_registry
from aiteam.adapters.registry import AdapterDescriptor
from aiteam.db.interactions import create_interaction, resolve_interaction
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


# ── Helpers ───────────────────────────────────────────────────────────────────

_CHANGES_REQUESTED_COMMENT = """\
Revisión completada. Hay problemas críticos que deben corregirse.

El archivo src/main.py tiene errores de lógica en la función `process`.

---AGENT-REPORT---
role: reviewer
result: changes_requested
issue_status: done
next_owner: lead
tech_match: yes
blocker: process() returns wrong values for negative inputs
evidence: src/main.py:42-67
"""

_REVIEWER_DONE_OK_COMMENT = """\
Revisión completada. El código cumple con los requisitos.

---AGENT-REPORT---
role: reviewer
result: done
issue_status: done
next_owner: none
tech_match: yes
blocker: none
evidence: src/main.py:1-100
"""

_ENGINEER_DONE_COMMENT = """\
Implementación completada.

---AGENT-REPORT---
role: engineer
result: done
issue_status: done
next_owner: reviewer
tech_match: yes
blocker: none
evidence: src/main.py:1-100
"""


def _init_db(db_path: Path) -> None:
    """Create schema + lead agent + parent issue."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Build app"))
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Team Lead", "lead", "lead_builtin"),
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
            """
            INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("issue:intake", "goal-1", "Build the application", "in_progress", "lead", "role:lead"),
        )
        conn.commit()


def _add_child_issue(
    db_path: Path,
    *,
    issue_id: str,
    role: str,
    status: str,
    comment: str | None = None,
    agent_id: str | None = None,
) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (issue_id, "goal-1", "issue:intake", f"{role.title()} task", status, role,
             agent_id or f"role:{role}"),
        )
        if comment:
            conn.execute(
                "INSERT INTO issue_comments (issue_id, author_agent_id, body) VALUES (?, ?, ?)",
                (issue_id, agent_id or f"role:{role}", comment),
            )
        conn.commit()


def _enqueue_child_report(db_path: Path, *, child_issue_id: str) -> Any:
    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="delegation",
        reason="child_report",
        payload={
            "issue_id": "issue:intake",
            "child_issue_id": child_issue_id,
            "wake_reason": "child_report",
        },
    )
    return HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")


# ── _all_children_done with changes_requested ─────────────────────────────────

class TestAllChildrenDoneGate:
    """_all_children_done must return False when reviewer.result == changes_requested."""

    def test_returns_false_when_reviewer_has_changes_requested(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child_issue(
            db_path, issue_id="issue:intake:eng", role="engineer",
            status="done", comment=_ENGINEER_DONE_COMMENT,
        )
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_CHANGES_REQUESTED_COMMENT,
        )
        executor = RunExecutor(db_path, build_default_registry())
        # Access the protected method directly for unit testing
        assert executor._all_children_done("issue:intake") is False, (
            "_all_children_done should be False when reviewer result is changes_requested"
        )

    def test_returns_true_when_reviewer_approves(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child_issue(
            db_path, issue_id="issue:intake:eng", role="engineer",
            status="done", comment=_ENGINEER_DONE_COMMENT,
        )
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_REVIEWER_DONE_OK_COMMENT,
        )
        executor = RunExecutor(db_path, build_default_registry())
        assert executor._all_children_done("issue:intake") is True, (
            "_all_children_done should be True when reviewer result is done"
        )


# ── _handle_reviewer_changes_requested unit tests ─────────────────────────────

class TestHandleReviewerChangesRequested:
    """Unit tests for _handle_reviewer_changes_requested."""

    def _make_executor(self, db_path: Path) -> RunExecutor:
        return RunExecutor(db_path, build_default_registry())

    def _fake_run(self) -> dict[str, Any]:
        return {"id": "run-test-001", "issue_id": "issue:intake"}

    def test_returns_none_when_no_changes_requested(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child_issue(
            db_path, issue_id="issue:intake:eng", role="engineer",
            status="done", comment=_ENGINEER_DONE_COMMENT,
        )
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_REVIEWER_DONE_OK_COMMENT,
        )
        executor = self._make_executor(db_path)
        result = executor._handle_reviewer_changes_requested(
            "issue:intake", "role:lead", self._fake_run()
        )
        assert result is None, "Should return None when reviewer result is 'done'"

    def test_returns_none_when_open_fix_engineer_exists(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        # Reviewer done with changes_requested
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_CHANGES_REQUESTED_COMMENT,
        )
        # An open engineer fix already exists — should wait
        _add_child_issue(
            db_path, issue_id="issue:intake:fix", role="engineer",
            status="in_progress", comment=None,
        )
        executor = self._make_executor(db_path)
        result = executor._handle_reviewer_changes_requested(
            "issue:intake", "role:lead", self._fake_run()
        )
        assert result is None, "Should return None (wait) when a fix engineer is already open"

    def test_creates_fix_engineer_and_resets_reviewer(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child_issue(
            db_path, issue_id="issue:intake:eng", role="engineer",
            status="done", comment=_ENGINEER_DONE_COMMENT,
        )
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_CHANGES_REQUESTED_COMMENT,
        )
        executor = self._make_executor(db_path)
        result = executor._handle_reviewer_changes_requested(
            "issue:intake", "role:lead", self._fake_run()
        )
        assert result is not None, "Should return an ExecutionResult when fix cycle starts"
        assert result.status == "completed"
        assert "corrección" in result.output.lower() or "fix" in result.output.lower()

        # Verify reviewer was reset to todo
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rev = conn.execute(
                "SELECT status FROM issues WHERE id = ?", ("issue:intake:rev",)
            ).fetchone()
            assert rev is not None
            assert rev["status"] == "todo", (
                f"Reviewer should be reset to todo, got {rev['status']!r}"
            )

        # Verify a new engineer fix issue was created
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            children = conn.execute(
                """
                SELECT id, title, role, status FROM issues
                WHERE parent_id = 'issue:intake'
                  AND role = 'engineer'
                  AND id != 'issue:intake:eng'
                ORDER BY created_at DESC LIMIT 1
                """,
            ).fetchone()
        assert children is not None, "A new engineer fix issue should have been created"
        assert "fix" in children["title"].lower() or "correc" in children["title"].lower()
        assert children["status"] == "todo"

    def test_fix_issue_description_contains_reviewer_findings(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_CHANGES_REQUESTED_COMMENT,
        )
        executor = self._make_executor(db_path)
        executor._handle_reviewer_changes_requested(
            "issue:intake", "role:lead", self._fake_run()
        )
        # Verify the fix engineer issue carries the reviewer's evidence and blocker
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            fix_issue = conn.execute(
                """
                SELECT description FROM issues
                WHERE parent_id = 'issue:intake' AND role = 'engineer'
                ORDER BY created_at DESC LIMIT 1
                """,
            ).fetchone()
        assert fix_issue is not None
        desc = fix_issue["description"] or ""
        assert "process()" in desc or "negative inputs" in desc, (
            "Fix issue description should include reviewer's blocker: got " + desc[:200]
        )
        assert "src/main.py" in desc, (
            "Fix issue description should include reviewer's evidence: got " + desc[:200]
        )

    def test_idempotent_when_called_twice(self, tmp_path: Path) -> None:
        """Second call while the fix engineer is open must not create a duplicate."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_CHANGES_REQUESTED_COMMENT,
        )
        executor = self._make_executor(db_path)
        # First call creates the fix issue and resets reviewer
        result1 = executor._handle_reviewer_changes_requested(
            "issue:intake", "role:lead", self._fake_run()
        )
        assert result1 is not None

        # Second call with same state — reviewer is now todo (open) AND fix engineer exists (open)
        result2 = executor._handle_reviewer_changes_requested(
            "issue:intake", "role:lead", self._fake_run()
        )
        # Reviewer is now todo again (not done) so changes_requested condition is not met
        assert result2 is None, "Second call should return None (reviewer is back to todo)"

        # Confirm there is only ONE new engineer fix issue (no duplicate)
        with sqlite3.connect(str(db_path)) as conn:
            fix_count = conn.execute(
                "SELECT COUNT(*) FROM issues WHERE parent_id = 'issue:intake' AND role = 'engineer'",
            ).fetchone()[0]
        assert fix_count == 1, f"Expected 1 fix engineer issue, found {fix_count}"

    def test_fix_title_is_numbered(self, tmp_path: Path) -> None:
        """Fix issue title should include the cycle number."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child_issue(
            db_path, issue_id="issue:intake:eng", role="engineer",
            status="done", comment=_ENGINEER_DONE_COMMENT,
        )
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_CHANGES_REQUESTED_COMMENT,
        )
        executor = self._make_executor(db_path)
        executor._handle_reviewer_changes_requested(
            "issue:intake", "role:lead", self._fake_run()
        )
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            fix_issue = conn.execute(
                """
                SELECT title FROM issues
                WHERE parent_id = 'issue:intake' AND role = 'engineer'
                  AND id != 'issue:intake:eng'
                ORDER BY created_at DESC LIMIT 1
                """,
            ).fetchone()
        assert fix_issue is not None
        assert "#1" in fix_issue["title"], (
            f"First fix issue title should contain '#1', got: {fix_issue['title']!r}"
        )

    def test_stale_initial_cycle_ready_is_cancelled_on_fix_start(self, tmp_path: Path) -> None:
        """A pending initial_cycle_ready interaction must be cancelled when a fix cycle starts."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child_issue(
            db_path, issue_id="issue:intake:eng", role="engineer",
            status="done", comment=_ENGINEER_DONE_COMMENT,
        )
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_CHANGES_REQUESTED_COMMENT,
        )
        # Inject a stale initial_cycle_ready interaction
        from aiteam.db.interactions import create_interaction
        create_interaction(
            db_path,
            issue_id="issue:intake",
            kind="request_confirmation",
            payload={"version": 1, "reason": "initial_cycle_ready", "parent_issue_id": "issue:intake"},
            idempotency_key="lead:cycle-review:issue:intake",
        )
        executor = self._make_executor(db_path)
        executor._handle_reviewer_changes_requested(
            "issue:intake", "role:lead", self._fake_run()
        )
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            stale = conn.execute(
                "SELECT status FROM issue_thread_interactions"
                " WHERE idempotency_key = 'lead:cycle-review:issue:intake'",
            ).fetchone()
        assert stale is not None
        assert stale["status"] == "cancelled", (
            f"Stale initial_cycle_ready should be cancelled, got {stale['status']!r}"
        )

    def test_fix_cycle_limit_escalates_to_user(self, tmp_path: Path) -> None:
        """After MAX_FIX_CYCLES done engineers, escalate instead of creating another fix."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        # Create MAX_FIX_CYCLES + 1 done engineers (original + 3 fixes = 4 total)
        max_cycles = RunExecutor._MAX_FIX_CYCLES
        for i in range(max_cycles + 1):
            _add_child_issue(
                db_path, issue_id=f"issue:intake:eng{i}", role="engineer",
                status="done", comment=_ENGINEER_DONE_COMMENT,
            )
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_CHANGES_REQUESTED_COMMENT,
        )
        executor = self._make_executor(db_path)
        result = executor._handle_reviewer_changes_requested(
            "issue:intake", "role:lead", self._fake_run()
        )
        assert result is not None, "Should return an ExecutionResult for escalation"
        assert result.status == "completed"
        # Should NOT have created a new engineer fix issue
        with sqlite3.connect(str(db_path)) as conn:
            new_eng_count = conn.execute(
                f"SELECT COUNT(*) FROM issues WHERE parent_id = 'issue:intake' AND role = 'engineer'",
            ).fetchone()[0]
        assert new_eng_count == max_cycles + 1, (
            f"No new engineer should be created at limit; expected {max_cycles + 1}, got {new_eng_count}"
        )
        # Should have created an escalation interaction
        assert result.actions is not None
        interactions = result.actions.get("interactions") or []
        assert len(interactions) == 1
        assert interactions[0]["payload"]["reason"] == "reviewer_fix_cycle_limit"

    def test_second_cycle_has_cycle_number_in_title_and_description(self, tmp_path: Path) -> None:
        """After original + fix #1 done, fix #2 title/description should include '#2'."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        # Original engineer done + fix #1 engineer done = 2 done engineers
        _add_child_issue(
            db_path, issue_id="issue:intake:eng", role="engineer",
            status="done", comment=_ENGINEER_DONE_COMMENT,
        )
        _add_child_issue(
            db_path, issue_id="issue:intake:fix1", role="engineer",
            status="done", comment=_ENGINEER_DONE_COMMENT,
        )
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_CHANGES_REQUESTED_COMMENT,
        )
        executor = self._make_executor(db_path)
        result = executor._handle_reviewer_changes_requested(
            "issue:intake", "role:lead", self._fake_run()
        )
        assert result is not None
        assert "#2" in result.output, (
            f"Output should reference cycle #2; got: {result.output[:300]}"
        )
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            new_fix = conn.execute(
                """
                SELECT title, description FROM issues
                WHERE parent_id = 'issue:intake' AND role = 'engineer'
                  AND id NOT IN ('issue:intake:eng', 'issue:intake:fix1')
                ORDER BY created_at DESC LIMIT 1
                """,
            ).fetchone()
        assert new_fix is not None
        assert "#2" in new_fix["title"], f"Fix #2 title should contain '#2': {new_fix['title']!r}"
        assert "2" in (new_fix["description"] or ""), (
            "Fix #2 description should mention prior attempt count"
        )


# ── Integration: child_report wake end-to-end ─────────────────────────────────

class TestChangesRequestedIntegration:
    """End-to-end: child_report wake triggers the changes_requested fix cycle."""

    def test_child_report_wake_starts_fix_cycle(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child_issue(
            db_path, issue_id="issue:intake:eng", role="engineer",
            status="done", comment=_ENGINEER_DONE_COMMENT,
        )
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_CHANGES_REQUESTED_COMMENT,
        )
        executor = RunExecutor(db_path, build_default_registry())
        dispatch = _enqueue_child_report(db_path, child_issue_id="issue:intake:rev")
        executor.execute(dispatch)  # returns None — check DB state instead

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            # Reviewer should be reset to todo
            rev = conn.execute(
                "SELECT status FROM issues WHERE id = ?", ("issue:intake:rev",)
            ).fetchone()
            assert rev is not None
            assert rev["status"] == "todo", (
                f"Reviewer should be reset to todo after fix cycle, got {rev['status']!r}"
            )
            # A new engineer fix issue should exist
            fix_engineer = conn.execute(
                """
                SELECT id, status FROM issues
                WHERE parent_id = 'issue:intake'
                  AND role = 'engineer'
                  AND id != 'issue:intake:eng'
                """,
            ).fetchone()
            assert fix_engineer is not None, "Expected a fix engineer child issue"
            assert fix_engineer["status"] == "todo"
            # No cycle-close interaction should have been created
            cycle_close = conn.execute(
                "SELECT id FROM issue_thread_interactions WHERE idempotency_key = ?",
                ("lead:cycle-review:issue:intake",),
            ).fetchone()
            assert cycle_close is None, (
                "Cycle-close interaction should NOT be created when reviewer has changes_requested"
            )
            # Lead should have posted a comment about the fix cycle
            lead_comment = conn.execute(
                """
                SELECT body FROM issue_comments
                WHERE issue_id = 'issue:intake' AND author_agent_id = 'role:lead'
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
            ).fetchone()
            assert lead_comment is not None
            assert "corrección" in lead_comment["body"].lower() or "fix" in lead_comment["body"].lower()

    def test_fix_engineer_done_wakes_reviewer(self, tmp_path: Path) -> None:
        """After fix engineer completes, resolve_blocker_wakeups should enqueue a reviewer wakeup."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child_issue(
            db_path, issue_id="issue:intake:eng", role="engineer",
            status="done", comment=_ENGINEER_DONE_COMMENT,
        )
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_CHANGES_REQUESTED_COMMENT,
        )
        executor = RunExecutor(db_path, build_default_registry())
        dispatch = _enqueue_child_report(db_path, child_issue_id="issue:intake:rev")
        executor.execute(dispatch)

        # Find the fix engineer issue that was created
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            fix_eng = conn.execute(
                """
                SELECT id FROM issues
                WHERE parent_id = 'issue:intake'
                  AND role = 'engineer'
                  AND id != 'issue:intake:eng'
                LIMIT 1
                """,
            ).fetchone()
        assert fix_eng is not None
        fix_eng_id = fix_eng["id"]

        # Simulate fix engineer completing: set to done and enqueue wakeup
        # (in real execution, _apply_result_actions does this via resolve_blocker_wakeups)
        from aiteam.db.issues import update_issue
        from aiteam.db.dependencies import resolve_blocker_wakeups

        update_issue(db_path, issue_id=fix_eng_id, status="done")
        resolve_blocker_wakeups(db_path, resolved_issue_id=fix_eng_id, source_run_id="run-fix")

        # Now the reviewer should have a pending/queued wakeup
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            reviewer_wakeup = conn.execute(
                """
                SELECT * FROM wakeup_requests
                WHERE agent_id = 'role:reviewer'
                  AND status NOT IN ('finished', 'cancelled')
                ORDER BY created_at DESC LIMIT 1
                """,
            ).fetchone()
        assert reviewer_wakeup is not None, (
            "Reviewer should have a queued wakeup after fix engineer completes"
        )
        assert reviewer_wakeup["reason"] == "blockers_resolved"

    def test_no_fix_cycle_when_reviewer_is_pending(self, tmp_path: Path) -> None:
        """If reviewer is not done (still todo/in_progress), no fix cycle is triggered."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child_issue(
            db_path, issue_id="issue:intake:eng", role="engineer",
            status="done", comment=_ENGINEER_DONE_COMMENT,
        )
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="in_progress", comment=None,
        )
        executor = RunExecutor(db_path, build_default_registry())
        result = executor._handle_reviewer_changes_requested(
            "issue:intake", "role:lead", {"id": "run-x", "issue_id": "issue:intake"}
        )
        assert result is None, "No fix cycle when reviewer is still in_progress"

    def test_cycle_completes_normally_after_fix_approved(self, tmp_path: Path) -> None:
        """After a fix cycle, if reviewer approves, _all_children_done returns True."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child_issue(
            db_path, issue_id="issue:intake:eng", role="engineer",
            status="done", comment=_ENGINEER_DONE_COMMENT,
        )
        _add_child_issue(
            db_path, issue_id="issue:intake:fix-eng", role="engineer",
            status="done", comment=_ENGINEER_DONE_COMMENT,
        )
        # Reviewer is now done with approved result (after second pass)
        _add_child_issue(
            db_path, issue_id="issue:intake:rev", role="reviewer",
            status="done", comment=_REVIEWER_DONE_OK_COMMENT,
        )
        executor = RunExecutor(db_path, build_default_registry())
        assert executor._all_children_done("issue:intake") is True, (
            "After fix is approved, _all_children_done should return True"
        )
