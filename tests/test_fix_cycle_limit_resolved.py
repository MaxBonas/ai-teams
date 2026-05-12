"""Tests for _handle_fix_cycle_limit_resolved (accept / reject paths).

Covers:
- accept: creates final engineer (complexity=high), resets reviewer to todo,
  description includes full rejection history
- reject: cancels non-terminal children, sets parent to cancelled, no new engineer
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aiteam.adapters.registry import build_default_registry
from aiteam.db.interactions import create_interaction, resolve_interaction
from aiteam.db.migration import SCHEMA_PATH
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


# ── Shared comment bodies ────────────────────────────────────────────────────

_ENGINEER_DONE = """\
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

_REVIEWER_CHANGES_REQUESTED = """\
Revisión completada. Hay problemas críticos.

---AGENT-REPORT---
role: reviewer
result: changes_requested
issue_status: done
next_owner: lead
tech_match: yes
blocker: process() returns wrong values for negative inputs
evidence: src/main.py:42-67
"""


# ── DB helpers ───────────────────────────────────────────────────────────────

def _init_db(db_path: Path) -> None:
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
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("issue:intake", "goal-1", "Build the application", "in_progress", "lead", "role:lead"),
        )
        conn.commit()


def _add_child(
    db_path: Path,
    *,
    issue_id: str,
    role: str,
    status: str,
    comment: str | None = None,
) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (issue_id, "goal-1", "issue:intake", f"{role.title()} task",
             status, role, f"role:{role}"),
        )
        if comment:
            conn.execute(
                "INSERT INTO issue_comments (issue_id, author_agent_id, body) VALUES (?, ?, ?)",
                (issue_id, f"role:{role}", comment),
            )
        conn.commit()


def _setup_limit_db(db_path: Path) -> str:
    """Create DB with MAX_FIX_CYCLES+1 done engineers + done reviewer (changes_requested).
    Creates the reviewer_fix_cycle_limit interaction and returns its ID.
    """
    _init_db(db_path)
    max_cycles = RunExecutor._MAX_FIX_CYCLES
    for i in range(max_cycles + 1):
        _add_child(
            db_path, issue_id=f"issue:intake:eng{i}", role="engineer",
            status="done", comment=_ENGINEER_DONE,
        )
    _add_child(
        db_path, issue_id="issue:intake:rev", role="reviewer",
        status="done", comment=_REVIEWER_CHANGES_REQUESTED,
    )
    interaction = create_interaction(
        db_path,
        issue_id="issue:intake",
        kind="request_confirmation",
        payload={
            "version": 1,
            "reason": "reviewer_fix_cycle_limit",
            "parent_issue_id": "issue:intake",
            "fix_cycle_count": max_cycles,
            "last_blocker": "process() returns wrong values for negative inputs",
            "last_evidence": "src/main.py:42-67",
        },
        continuation_policy="wake_assignee",
        idempotency_key="lead:fix-cycle-limit:issue:intake",
        created_by_agent_id="role:lead",
    )
    return str(interaction["id"])


def _dispatch_after_resolve(
    db_path: Path, *, interaction_id: str, action: str
) -> Any:
    resolve_interaction(
        db_path,
        interaction_id=interaction_id,
        action=action,
        resolved_by_user_id="user",
    )
    return HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")


# ── Accept path ──────────────────────────────────────────────────────────────

class TestFixCycleLimitAccept:
    def test_creates_final_engineer_issue(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        int_id = _setup_limit_db(db_path)
        dispatch = _dispatch_after_resolve(db_path, interaction_id=int_id, action="accept")
        assert dispatch is not None, "Lead should receive interaction_resolved wakeup"

        RunExecutor(db_path, build_default_registry()).execute(dispatch)

        max_cycles = RunExecutor._MAX_FIX_CYCLES
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            engineers = conn.execute(
                "SELECT id, title, status, complexity FROM issues"
                " WHERE parent_id = 'issue:intake' AND role = 'engineer'"
                " ORDER BY rowid ASC",
            ).fetchall()

        # Should have one more engineer than the MAX_FIX_CYCLES+1 set up
        assert len(engineers) == max_cycles + 2, (
            f"Expected {max_cycles + 2} engineers total, got {len(engineers)}"
        )
        final = engineers[-1]
        assert "final" in final["title"].lower(), (
            f"Final engineer title should contain 'final': {final['title']!r}"
        )
        assert final["status"] == "todo"

    def test_final_engineer_has_high_complexity(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        int_id = _setup_limit_db(db_path)
        dispatch = _dispatch_after_resolve(db_path, interaction_id=int_id, action="accept")
        RunExecutor(db_path, build_default_registry()).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            engineers = conn.execute(
                "SELECT complexity FROM issues WHERE parent_id = 'issue:intake'"
                " AND role = 'engineer' ORDER BY rowid ASC",
            ).fetchall()
        final = engineers[-1]
        assert str(final["complexity"] or "").lower() == "high", (
            f"Final engineer should have complexity=high, got {final['complexity']!r}"
        )

    def test_final_engineer_description_includes_rejection_history(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        int_id = _setup_limit_db(db_path)
        dispatch = _dispatch_after_resolve(db_path, interaction_id=int_id, action="accept")
        RunExecutor(db_path, build_default_registry()).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            engineers = conn.execute(
                "SELECT description FROM issues WHERE parent_id = 'issue:intake'"
                " AND role = 'engineer' ORDER BY rowid ASC",
            ).fetchall()
        desc = engineers[-1]["description"] or ""
        assert "process()" in desc or "negative inputs" in desc, (
            "Final description should include last_blocker from interaction payload"
        )
        assert "src/main.py" in desc, (
            "Final description should include last_evidence from interaction payload"
        )
        assert "final" in desc.lower(), (
            "Final description should warn this is the final attempt"
        )

    def test_reviewer_reset_to_todo_on_accept(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        int_id = _setup_limit_db(db_path)
        dispatch = _dispatch_after_resolve(db_path, interaction_id=int_id, action="accept")
        RunExecutor(db_path, build_default_registry()).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rev = conn.execute(
                "SELECT status FROM issues WHERE id = ?", ("issue:intake:rev",)
            ).fetchone()
        assert rev is not None
        assert rev["status"] == "todo", (
            f"Reviewer should be reset to todo on accept, got {rev['status']!r}"
        )

    def test_parent_issue_status_unchanged_on_accept(self, tmp_path: Path) -> None:
        """accept only creates the fix engineer; the parent stays in_progress."""
        db_path = tmp_path / "aiteam.db"
        int_id = _setup_limit_db(db_path)
        dispatch = _dispatch_after_resolve(db_path, interaction_id=int_id, action="accept")
        RunExecutor(db_path, build_default_registry()).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            parent = conn.execute(
                "SELECT status FROM issues WHERE id = ?", ("issue:intake",)
            ).fetchone()
        assert parent["status"] == "in_progress", (
            f"Parent should stay in_progress on accept, got {parent['status']!r}"
        )


# ── Reject path ──────────────────────────────────────────────────────────────

class TestFixCycleLimitReject:
    def test_parent_cancelled_on_reject(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        int_id = _setup_limit_db(db_path)
        dispatch = _dispatch_after_resolve(db_path, interaction_id=int_id, action="reject")
        assert dispatch is not None

        RunExecutor(db_path, build_default_registry()).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            parent = conn.execute(
                "SELECT status FROM issues WHERE id = ?", ("issue:intake",)
            ).fetchone()
        assert parent["status"] == "cancelled", (
            f"Parent should be cancelled on reject, got {parent['status']!r}"
        )

    def test_done_reviewer_not_modified_on_reject(self, tmp_path: Path) -> None:
        """A reviewer that is already 'done' (changes_requested) should stay done.
        Only non-terminal (todo/in_progress/blocked) children are cancelled.
        """
        db_path = tmp_path / "aiteam.db"
        int_id = _setup_limit_db(db_path)
        dispatch = _dispatch_after_resolve(db_path, interaction_id=int_id, action="reject")
        RunExecutor(db_path, build_default_registry()).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rev = conn.execute(
                "SELECT status FROM issues WHERE id = ?", ("issue:intake:rev",)
            ).fetchone()
        # Done work stays done — we don't retroactively cancel completed reviews
        assert rev["status"] == "done", (
            f"Done reviewer should remain 'done' on project cancel, got {rev['status']!r}"
        )

    def test_done_engineers_not_touched_on_reject(self, tmp_path: Path) -> None:
        """Already-done engineers should remain done — we only cancel non-terminal children."""
        db_path = tmp_path / "aiteam.db"
        int_id = _setup_limit_db(db_path)
        dispatch = _dispatch_after_resolve(db_path, interaction_id=int_id, action="reject")
        RunExecutor(db_path, build_default_registry()).execute(dispatch)

        max_cycles = RunExecutor._MAX_FIX_CYCLES
        with sqlite3.connect(str(db_path)) as conn:
            done_engs = conn.execute(
                "SELECT COUNT(*) FROM issues WHERE parent_id = 'issue:intake'"
                " AND role = 'engineer' AND status = 'done'",
            ).fetchone()[0]
        assert done_engs == max_cycles + 1, (
            f"All {max_cycles + 1} done engineers should stay done, got {done_engs}"
        )

    def test_no_new_engineer_on_reject(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        int_id = _setup_limit_db(db_path)
        dispatch = _dispatch_after_resolve(db_path, interaction_id=int_id, action="reject")
        RunExecutor(db_path, build_default_registry()).execute(dispatch)

        max_cycles = RunExecutor._MAX_FIX_CYCLES
        with sqlite3.connect(str(db_path)) as conn:
            eng_count = conn.execute(
                "SELECT COUNT(*) FROM issues WHERE parent_id = 'issue:intake'"
                " AND role = 'engineer'",
            ).fetchone()[0]
        assert eng_count == max_cycles + 1, (
            f"Reject should not create a new engineer; expected {max_cycles + 1}, got {eng_count}"
        )

    def test_lead_comment_mentions_cancellation_on_reject(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        int_id = _setup_limit_db(db_path)
        dispatch = _dispatch_after_resolve(db_path, interaction_id=int_id, action="reject")
        RunExecutor(db_path, build_default_registry()).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            comment = conn.execute(
                """
                SELECT body FROM issue_comments
                WHERE issue_id = 'issue:intake' AND author_agent_id = 'role:lead'
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
            ).fetchone()
        assert comment is not None
        body = comment["body"].lower()
        assert "cancel" in body, (
            f"Lead comment should mention cancellation, got: {comment['body'][:200]}"
        )
