"""Tests for the QA quality gate in _all_children_done.

QA is optional — cycle-close does not require a QA child to exist.
But IF a QA child is present and done, its AGENT-REPORT result must NOT
be 'blocked' or 'partial'.  A missing report (no ---AGENT-REPORT--- block)
is treated as acceptable.

Covered scenarios:
- QA result: done  → does NOT block cycle-close
- QA result: partial → blocks cycle-close
- QA result: blocked → blocks cycle-close
- No QA child at all → does NOT block cycle-close (reviewer gate still required)
- QA with no AGENT-REPORT block → does NOT block cycle-close
- QA status 'blocked' (issue status, not report result) → blocks cycle-close
  via the base "all children must be done" check (existing behavior)
- role='quality_assurance' alias also enforced
- Integration: partial-QA result prevents initial_cycle_ready interaction
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aiteam.adapters.registry import build_default_registry
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


# ── Shared test fixtures ──────────────────────────────────────────────────────

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

_REVIEWER_OK = """\
Revisión aprobada.

---AGENT-REPORT---
role: reviewer
result: done
issue_status: done
next_owner: lead
tech_match: yes
blocker: none
evidence: src/main.py:1-100
"""

_QA_DONE = """\
QA completado. Todos los tests pasan.

---AGENT-REPORT---
role: qa
result: done
issue_status: done
next_owner: lead
tech_match: yes
blocker: none
evidence: tests/: 42/42 passed
"""

_QA_PARTIAL = """\
QA parcial — algunos tests fallan.

---AGENT-REPORT---
role: qa
result: partial
issue_status: done
next_owner: lead
tech_match: yes
blocker: leaderboard tests fail (3/42)
evidence: tests/test_leaderboard.py:12-24
"""

_QA_BLOCKED = """\
QA bloqueado — no se puede ejecutar el build.

---AGENT-REPORT---
role: qa
result: blocked
issue_status: done
next_owner: lead
tech_match: no
blocker: missing build artifact
evidence: none
"""

_QA_NO_REPORT = """\
QA ejecutado. Se revisaron los archivos.
No se encontraron problemas mayores.
"""


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Build app"))
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
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("role:qa", "qa", "QA", "standard", "openai_api", "role:lead"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("issue:intake", "goal-1", "Build the app", "in_progress", "lead", "role:lead"),
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


def _executor(db_path: Path) -> RunExecutor:
    return RunExecutor(db_path, build_default_registry())


def _dispatch_child_report(db_path: Path) -> Any:
    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="delegation",
        reason="child_report",
        payload={"issue_id": "issue:intake", "wake_reason": "child_report"},
    )
    return HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")


# ── Unit: _all_children_done QA gate ─────────────────────────────────────────

class TestQaGateUnit:
    """Direct calls to _all_children_done to verify the QA gate in isolation."""

    def _setup_with_qa(self, db_path: Path, *, qa_comment: str | None, qa_status: str = "done") -> RunExecutor:
        _init_db(db_path)
        _add_child(db_path, issue_id="issue:intake:eng",
                   role="engineer", status="done", comment=_ENGINEER_DONE)
        _add_child(db_path, issue_id="issue:intake:rev",
                   role="reviewer", status="done", comment=_REVIEWER_OK)
        _add_child(db_path, issue_id="issue:intake:qa",
                   role="qa", status=qa_status, comment=qa_comment)
        return _executor(db_path)

    def test_qa_done_result_allows_cycle_close(self, tmp_path: Path) -> None:
        ex = self._setup_with_qa(tmp_path / "aiteam.db", qa_comment=_QA_DONE)
        assert ex._all_children_done("issue:intake") is True

    def test_qa_partial_blocks_cycle_close(self, tmp_path: Path) -> None:
        ex = self._setup_with_qa(tmp_path / "aiteam.db", qa_comment=_QA_PARTIAL)
        assert ex._all_children_done("issue:intake") is False, (
            "_all_children_done must return False when QA result is 'partial'"
        )

    def test_qa_blocked_report_blocks_cycle_close(self, tmp_path: Path) -> None:
        """QA with issue_status=done but result=blocked in the report still blocks."""
        ex = self._setup_with_qa(tmp_path / "aiteam.db", qa_comment=_QA_BLOCKED)
        assert ex._all_children_done("issue:intake") is False, (
            "_all_children_done must return False when QA result is 'blocked'"
        )

    def test_qa_no_report_block_allows_cycle_close(self, tmp_path: Path) -> None:
        """QA with no ---AGENT-REPORT--- block is treated as acceptable (legacy path)."""
        ex = self._setup_with_qa(tmp_path / "aiteam.db", qa_comment=_QA_NO_REPORT)
        assert ex._all_children_done("issue:intake") is True

    def test_qa_no_comment_allows_cycle_close(self, tmp_path: Path) -> None:
        """QA issue with no comments at all → no report → treated as acceptable."""
        ex = self._setup_with_qa(tmp_path / "aiteam.db", qa_comment=None)
        assert ex._all_children_done("issue:intake") is True

    def test_no_qa_child_allows_cycle_close(self, tmp_path: Path) -> None:
        """QA is optional — cycle-close can proceed without any QA child."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child(db_path, issue_id="issue:intake:eng",
                   role="engineer", status="done", comment=_ENGINEER_DONE)
        _add_child(db_path, issue_id="issue:intake:rev",
                   role="reviewer", status="done", comment=_REVIEWER_OK)
        ex = _executor(db_path)
        assert ex._all_children_done("issue:intake") is True

    def test_qa_issue_status_blocked_blocks_cycle_close(self, tmp_path: Path) -> None:
        """QA with issue status 'blocked' blocks via the base all-done check (not QA gate)."""
        ex = self._setup_with_qa(tmp_path / "aiteam.db",
                                  qa_comment=_QA_BLOCKED, qa_status="blocked")
        assert ex._all_children_done("issue:intake") is False

    def test_quality_assurance_role_alias_enforced(self, tmp_path: Path) -> None:
        """role='quality_assurance' (long form) is also governed by the QA gate."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child(db_path, issue_id="issue:intake:eng",
                   role="engineer", status="done", comment=_ENGINEER_DONE)
        _add_child(db_path, issue_id="issue:intake:rev",
                   role="reviewer", status="done", comment=_REVIEWER_OK)
        # Insert QA child with long-form role name
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("issue:intake:qa2", "goal-1", "issue:intake", "Quality Assurance task",
                 "done", "quality_assurance", "role:qa"),
            )
            conn.execute(
                "INSERT INTO issue_comments (issue_id, author_agent_id, body) VALUES (?, ?, ?)",
                ("issue:intake:qa2", "role:qa", _QA_PARTIAL),
            )
            conn.commit()
        ex = _executor(db_path)
        assert ex._all_children_done("issue:intake") is False, (
            "role='quality_assurance' should also be subject to the QA quality gate"
        )


# ── Integration: QA gate prevents cycle-close interaction ────────────────────

class TestQaGateIntegration:
    """End-to-end: verify the QA gate prevents the initial_cycle_ready interaction."""

    def _run_lead_after_qa(self, db_path: Path, *, qa_comment: str) -> None:
        _init_db(db_path)
        _add_child(db_path, issue_id="issue:intake:eng",
                   role="engineer", status="done", comment=_ENGINEER_DONE)
        _add_child(db_path, issue_id="issue:intake:rev",
                   role="reviewer", status="done", comment=_REVIEWER_OK)
        _add_child(db_path, issue_id="issue:intake:qa",
                   role="qa", status="done", comment=qa_comment)
        dispatch = _dispatch_child_report(db_path)
        assert dispatch is not None
        RunExecutor(db_path, build_default_registry()).execute(dispatch)

    def _pending_interactions(self, db_path: Path) -> list[dict]:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM issue_thread_interactions"
                " WHERE issue_id = 'issue:intake' AND status = 'pending'",
            ).fetchall()
        return [dict(r) for r in rows]

    def test_cycle_close_fires_when_qa_done(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        self._run_lead_after_qa(db_path, qa_comment=_QA_DONE)
        interactions = self._pending_interactions(db_path)
        reasons = [(i.get("payload") or {}).get("reason") or
                   (i.get("payload_json") and __import__("json").loads(i["payload_json"] or "{}").get("reason"))
                   for i in interactions]
        # Find initial_cycle_ready via raw payload_json column
        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM issue_thread_interactions"
                " WHERE issue_id = 'issue:intake' AND status = 'pending'"
                " AND payload_json LIKE '%initial_cycle_ready%'",
            ).fetchone()[0]
        assert count == 1, (
            "Cycle-close interaction should fire when QA result is 'done'"
        )

    def test_no_cycle_close_when_qa_partial(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        self._run_lead_after_qa(db_path, qa_comment=_QA_PARTIAL)
        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM issue_thread_interactions"
                " WHERE issue_id = 'issue:intake' AND status = 'pending'"
                " AND payload_json LIKE '%initial_cycle_ready%'",
            ).fetchone()[0]
        assert count == 0, (
            "Cycle-close interaction must NOT fire when QA result is 'partial'"
        )

    def test_no_cycle_close_when_qa_blocked_report(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        self._run_lead_after_qa(db_path, qa_comment=_QA_BLOCKED)
        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM issue_thread_interactions"
                " WHERE issue_id = 'issue:intake' AND status = 'pending'"
                " AND payload_json LIKE '%initial_cycle_ready%'",
            ).fetchone()[0]
        assert count == 0, (
            "Cycle-close interaction must NOT fire when QA report result is 'blocked'"
        )

    def test_cycle_close_fires_without_qa(self, tmp_path: Path) -> None:
        """QA is optional — cycle-close fires when only engineer+reviewer are done."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_child(db_path, issue_id="issue:intake:eng",
                   role="engineer", status="done", comment=_ENGINEER_DONE)
        _add_child(db_path, issue_id="issue:intake:rev",
                   role="reviewer", status="done", comment=_REVIEWER_OK)
        dispatch = _dispatch_child_report(db_path)
        assert dispatch is not None
        RunExecutor(db_path, build_default_registry()).execute(dispatch)
        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM issue_thread_interactions"
                " WHERE issue_id = 'issue:intake' AND status = 'pending'"
                " AND payload_json LIKE '%initial_cycle_ready%'",
            ).fetchone()[0]
        assert count == 1, (
            "Cycle-close should fire when no QA child exists (QA is optional)"
        )
