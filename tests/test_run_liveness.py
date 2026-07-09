"""Tests for the pure run liveness classifier (aiteam/run_liveness.py).

Covers:
- Evidence taxonomy (workspace files excluded from has_concrete_action_evidence)
- Pure classification without regex for all agent roles and adapter types
- API-only engineering adapter with workspace changes → advanced (file ops)
- API-only engineering adapter without workspace changes → plan_only / empty_response (continuation)
- Bounded continuations: max MAX_CONTINUATION_ATTEMPTS for plan_only/empty_response
- Exhaustion escalates to blocked with an explanatory comment
- Workspace changes → advanced (auto-close for engineering if no explicit status)
- Non-engineering roles: text output is sufficient for advanced
- Builtin adapters are always terminal (no continuation)
- Failed / skipped runs always terminal
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aiteam.run_liveness import (
    MAX_CONTINUATION_ATTEMPTS,
    RunEvidence,
    classify_run_liveness,
    collect_run_evidence,
)


# ---------------------------------------------------------------------------
# RunEvidence helpers
# ---------------------------------------------------------------------------


def _empty_evidence(**overrides: int) -> RunEvidence:
    return RunEvidence(
        issue_comments_created=overrides.get("issue_comments_created", 0),
        document_revisions_created=overrides.get("document_revisions_created", 0),
        activity_events_created=overrides.get("activity_events_created", 0),
        tool_events_created=overrides.get("tool_events_created", 0),
        workspace_files_changed=overrides.get("workspace_files_changed", 0),
    )


class TestRunEvidence:
    def test_has_concrete_action_evidence_false_when_all_zero(self):
        ev = _empty_evidence()
        assert ev.has_concrete_action_evidence is False

    def test_has_concrete_action_evidence_via_comment(self):
        ev = _empty_evidence(issue_comments_created=1)
        assert ev.has_concrete_action_evidence is True

    def test_has_concrete_action_evidence_via_revision(self):
        ev = _empty_evidence(document_revisions_created=2)
        assert ev.has_concrete_action_evidence is True

    def test_has_concrete_action_evidence_via_activity(self):
        ev = _empty_evidence(activity_events_created=1)
        assert ev.has_concrete_action_evidence is True

    def test_has_concrete_action_evidence_via_tools(self):
        ev = _empty_evidence(tool_events_created=3)
        assert ev.has_concrete_action_evidence is True

    def test_workspace_files_alone_do_not_count_as_concrete_action_evidence(self):
        """Workspace operations are tracked separately — not concrete action evidence."""
        ev = _empty_evidence(workspace_files_changed=5)
        assert ev.has_concrete_action_evidence is False

    def test_workspace_files_and_comment_together(self):
        ev = _empty_evidence(workspace_files_changed=2, issue_comments_created=1)
        assert ev.has_concrete_action_evidence is True


# ---------------------------------------------------------------------------
# Terminal states
# ---------------------------------------------------------------------------


class TestTerminalStates:
    def test_failed_run_is_terminal(self):
        result = classify_run_liveness(
            run_status="failed",
            evidence=_empty_evidence(),
            adapter_type="subscription_cli",
            agent_role="engineer",
            useful_output=False,
        )
        assert result.state == "failed"
        assert result.needs_continuation is False

    def test_skipped_run_is_completed(self):
        result = classify_run_liveness(
            run_status="skipped",
            evidence=_empty_evidence(),
            adapter_type="subscription_cli",
            agent_role="engineer",
            useful_output=False,
        )
        assert result.state == "completed"
        assert result.needs_continuation is False


# ---------------------------------------------------------------------------
# Builtin adapters
# ---------------------------------------------------------------------------


class TestBuiltinAdapters:
    @pytest.mark.parametrize("adapter", ["role_builtin", "lead_builtin", "manual"])
    def test_builtin_with_output_is_advanced(self, adapter: str):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type=adapter,
            agent_role="engineer",
            useful_output=True,
        )
        assert result.state == "advanced"
        assert result.needs_continuation is False

    @pytest.mark.parametrize("adapter", ["role_builtin", "lead_builtin", "manual"])
    def test_builtin_without_output_is_completed(self, adapter: str):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(),
            adapter_type=adapter,
            agent_role="engineer",
            useful_output=False,
        )
        assert result.state == "completed"
        assert result.needs_continuation is False


# ---------------------------------------------------------------------------
# API-only engineering adapter — now treated same as CLI adapters
# File ops (write_file/append_file/delete_file) allow all adapters to write files.
# Without workspace changes: plan_only/empty_response (continuation loop).
# With workspace changes: advanced (same as CLI).
# ---------------------------------------------------------------------------


class TestApiOnlyEngineer:
    @pytest.mark.parametrize("adapter", ["openai_api", "anthropic_api", "gemini_api", "anthropic_sonnet"])
    def test_api_only_engineer_no_workspace_changes_with_output_is_plan_only(self, adapter: str):
        """API-only engineer with useful output but no workspace changes → plan_only (continuable)."""
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type=adapter,
            agent_role="engineer",
            useful_output=True,
        )
        assert result.state == "plan_only"
        assert result.needs_continuation is True

    def test_api_only_engineer_no_output_no_changes_is_empty_response(self):
        """API-only engineer with no output and no workspace changes → empty_response (continuable)."""
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(),
            adapter_type="openai_api",
            agent_role="engineer",
            useful_output=False,
        )
        assert result.state == "empty_response"
        assert result.needs_continuation is True

    def test_api_only_engineer_with_workspace_changes_is_advanced(self):
        """API-only engineer using write_file ops → workspace changes → advanced."""
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(workspace_files_changed=3),
            adapter_type="openai_api",
            agent_role="software_engineer",
            useful_output=True,
        )
        assert result.state == "advanced"
        assert result.needs_continuation is False

    def test_api_only_engineer_continuation_exhaustion_is_blocked(self):
        """After MAX continuation attempts without progress, escalate to blocked."""
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(),
            adapter_type="openai_api",
            agent_role="engineer",
            useful_output=False,
            continuation_attempt=MAX_CONTINUATION_ATTEMPTS,
        )
        assert result.state == "blocked"
        assert result.needs_continuation is False

    @pytest.mark.parametrize("role", ["lead", "reviewer", "qa"])
    def test_api_only_non_engineering_role_is_advanced_with_output(self, role: str):
        """API-only adapters work fine for non-engineering roles (text output is enough)."""
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type="openai_api",
            agent_role=role,
            useful_output=True,
        )
        assert result.state == "advanced"
        assert result.needs_continuation is False


# ---------------------------------------------------------------------------
# Engineering role with CLI/local adapter
# ---------------------------------------------------------------------------


class TestEngineeringCli:
    def test_workspace_changes_is_advanced(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(workspace_files_changed=2),
            adapter_type="subscription_cli",
            agent_role="engineer",
            useful_output=True,
        )
        assert result.state == "advanced"
        assert result.needs_continuation is False

    def test_workspace_changes_auto_closes_when_no_explicit_status(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(workspace_files_changed=1),
            adapter_type="subscription_cli",
            agent_role="engineer",
            useful_output=True,
            has_explicit_issue_status=False,
        )
        assert result.state == "advanced"
        assert result.actions_override.get("issue_status") == "done"
        assert result.actions_override.get("notify_supervisor") is True

    def test_workspace_changes_does_not_override_explicit_status(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(workspace_files_changed=1),
            adapter_type="subscription_cli",
            agent_role="engineer",
            useful_output=True,
            has_explicit_issue_status=True,
        )
        assert result.state == "advanced"
        assert "issue_status" not in result.actions_override
        assert result.actions_override.get("notify_supervisor") is True

    def test_no_workspace_changes_useful_output_is_plan_only(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type="subscription_cli",
            agent_role="engineer",
            useful_output=True,
            continuation_attempt=0,
        )
        assert result.state == "plan_only"
        assert result.needs_continuation is True

    def test_no_workspace_changes_no_output_is_empty_response(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(),
            adapter_type="subscription_cli",
            agent_role="engineer",
            useful_output=False,
            continuation_attempt=0,
        )
        assert result.state == "empty_response"
        assert result.needs_continuation is True

    def test_plan_only_first_attempt_is_continuable(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type="subscription_cli",
            agent_role="engineer",
            useful_output=True,
            continuation_attempt=0,
            max_continuation_attempts=MAX_CONTINUATION_ATTEMPTS,
        )
        assert result.state == "plan_only"
        assert result.needs_continuation is True
        assert result.continuation_attempt == 0

    def test_plan_only_second_attempt_is_still_continuable(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type="subscription_cli",
            agent_role="engineer",
            useful_output=True,
            continuation_attempt=1,
            max_continuation_attempts=MAX_CONTINUATION_ATTEMPTS,
        )
        assert result.state == "plan_only"
        assert result.needs_continuation is True

    def test_plan_only_exhausted_at_max_is_blocked(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type="subscription_cli",
            agent_role="engineer",
            useful_output=True,
            continuation_attempt=MAX_CONTINUATION_ATTEMPTS,
        )
        assert result.state == "blocked"
        assert result.needs_continuation is False
        assert result.actions_override.get("issue_status") == "blocked"
        assert result.actions_override.get("notify_supervisor") is True
        assert any("Bloqueado" in c for c in result.actions_override.get("add_comments", []))

    def test_empty_response_exhausted_at_max_is_blocked(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(),
            adapter_type="subscription_cli",
            agent_role="software_engineer",
            useful_output=False,
            continuation_attempt=MAX_CONTINUATION_ATTEMPTS,
        )
        assert result.state == "blocked"
        assert result.needs_continuation is False

    def test_custom_max_continuation_attempts_respected(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(),
            adapter_type="subscription_cli",
            agent_role="engineer",
            useful_output=False,
            continuation_attempt=1,
            max_continuation_attempts=1,  # exhausted at 1
        )
        assert result.state == "blocked"
        assert result.needs_continuation is False

    # ── Rule 6: explicit blocking declared ───────────────────────────────────

    def test_explicit_blocked_op_suppresses_continuation(self):
        """Engineer that uses set_status:blocked op must NOT get a liveness continuation."""
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type="openai_api",
            agent_role="engineer",
            useful_output=True,
            has_explicit_issue_status=True,
            explicit_blocking_declared=True,  # set_status:blocked op was used
        )
        assert result.state == "advanced"
        assert result.needs_continuation is False

    def test_explicit_cancelled_op_suppresses_continuation(self):
        """Engineer that uses set_status:cancelled op must NOT get a liveness continuation."""
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(),
            adapter_type="openai_api",
            agent_role="engineer",
            useful_output=False,
            has_explicit_issue_status=True,
            explicit_blocking_declared=True,  # set_status:cancelled op was used
        )
        assert result.state == "completed"
        assert result.needs_continuation is False

    def test_explicit_done_without_workspace_still_plan_only(self):
        """Engineer that claims done without workspace changes still goes through plan_only.

        A 'done' claim is not a deliberate block — it should still be nudged via
        the continuation loop to produce real workspace output.
        """
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type="openai_api",
            agent_role="engineer",
            useful_output=True,
            has_explicit_issue_status=True,
            explicit_blocking_declared=False,  # set_status:done — NOT a blocking op
            continuation_attempt=0,
        )
        assert result.state == "plan_only"
        assert result.needs_continuation is True

    def test_explicit_blocked_no_output_is_completed_not_advanced(self):
        """Blocked op with no output returns 'completed', not 'advanced'."""
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(),
            adapter_type="openai_api",
            agent_role="engineer",
            useful_output=False,
            has_explicit_issue_status=True,
            explicit_blocking_declared=True,
        )
        assert result.state == "completed"
        assert result.needs_continuation is False

    def test_explicit_blocking_declared_false_by_default(self):
        """Passing only has_explicit_issue_status=True without explicit_blocking_declared
        does not bypass the plan_only loop — backward compat with older callers."""
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type="openai_api",
            agent_role="engineer",
            useful_output=True,
            has_explicit_issue_status=True,
            # explicit_blocking_declared omitted → defaults to False
            continuation_attempt=0,
        )
        assert result.state == "plan_only"
        assert result.needs_continuation is True


# ---------------------------------------------------------------------------
# Non-engineering roles
# ---------------------------------------------------------------------------


class TestNonEngineeringRoles:
    @pytest.mark.parametrize("role", ["lead", "reviewer", "qa", "team_lead", "product_manager"])
    def test_non_engineering_with_output_is_advanced(self, role: str):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type="openai_api",
            agent_role=role,
            useful_output=True,
        )
        assert result.state == "advanced"
        assert result.needs_continuation is False

    @pytest.mark.parametrize("role", ["file_scout", "web_scout", "test_runner"])
    def test_one_shot_scout_auto_closes_its_issue(self, role: str):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type="subscription_cli",
            agent_role=role,
            useful_output=True,
        )
        assert result.state == "advanced"
        assert result.actions_override.get("issue_status") == "done"

    def test_scout_does_not_auto_close_when_it_set_its_own_status(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type="subscription_cli",
            agent_role="file_scout",
            useful_output=True,
            has_explicit_issue_status=True,
        )
        assert result.actions_override.get("issue_status") is None

    def test_lead_is_not_auto_closed_like_a_scout(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(issue_comments_created=1),
            adapter_type="subscription_cli",
            agent_role="lead",
            useful_output=True,
        )
        assert result.actions_override.get("issue_status") is None

    def test_non_engineering_no_output_no_evidence_is_empty_response(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(),
            adapter_type="openai_api",
            agent_role="lead",
            useful_output=False,
            continuation_attempt=0,
        )
        assert result.state == "empty_response"
        assert result.needs_continuation is True

    def test_non_engineering_empty_response_exhausted_is_blocked(self):
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(),
            adapter_type="openai_api",
            agent_role="reviewer",
            useful_output=False,
            continuation_attempt=MAX_CONTINUATION_ATTEMPTS,
        )
        assert result.state == "blocked"
        assert result.needs_continuation is False

    def test_non_engineering_workspace_changes_alone_not_sufficient(self):
        """Workspace ops alone don't count as concrete action evidence.
        But useful_output could still make it advanced.
        """
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(workspace_files_changed=3),  # only workspace, no comments
            adapter_type="subscription_cli",
            agent_role="lead",
            useful_output=False,
        )
        # workspace alone isn't concrete_action_evidence, and useful_output=False
        # → empty_response
        assert result.state == "empty_response"

    def test_non_engineering_with_concrete_evidence_but_no_output_is_advanced(self):
        """Activity log events count even without text output."""
        result = classify_run_liveness(
            run_status="completed",
            evidence=_empty_evidence(activity_events_created=2),
            adapter_type="subscription_cli",
            agent_role="qa",
            useful_output=False,
        )
        assert result.state == "advanced"


# ---------------------------------------------------------------------------
# collect_run_evidence — DB integration tests
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> Path:
    from aiteam.db.migration import SCHEMA_PATH

    db = tmp_path / "test.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'Test')")
        conn.execute(
            "INSERT INTO agents (id, role, name) VALUES ('a1', 'engineer', 'Eng')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) "
            "VALUES ('i1', 'g1', 'Build', 'in_progress', 'a1')"
        )
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status, invocation_source) "
            "VALUES ('run-1', 'a1', 'i1', 'completed', 'test')"
        )
    return db


class TestCollectRunEvidence:
    def test_empty_db_returns_zero_evidence(self, tmp_path: Path):
        db = _make_db(tmp_path)
        ev = collect_run_evidence(db, run_id="run-1", workspace_files_changed=0)
        assert ev.issue_comments_created == 0
        assert ev.document_revisions_created == 0
        assert ev.activity_events_created == 0
        assert ev.tool_events_created == 0
        assert ev.workspace_files_changed == 0
        assert ev.has_concrete_action_evidence is False

    def test_counts_comment_with_matching_source_run_id(self, tmp_path: Path):
        db = _make_db(tmp_path)
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO issue_comments (id, issue_id, author_agent_id, body, source_run_id) "
                "VALUES ('c1', 'i1', 'a1', 'hello', 'run-1')"
            )
        ev = collect_run_evidence(db, run_id="run-1")
        assert ev.issue_comments_created == 1
        assert ev.has_concrete_action_evidence is True

    def test_ignores_comment_from_different_run(self, tmp_path: Path):
        db = _make_db(tmp_path)
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO issue_comments (id, issue_id, author_agent_id, body, source_run_id) "
                "VALUES ('c1', 'i1', 'a1', 'hello', 'run-OTHER')"
            )
        ev = collect_run_evidence(db, run_id="run-1")
        assert ev.issue_comments_created == 0

    def test_workspace_files_changed_passed_through(self, tmp_path: Path):
        db = _make_db(tmp_path)
        ev = collect_run_evidence(db, run_id="run-1", workspace_files_changed=7)
        assert ev.workspace_files_changed == 7
        # workspace alone doesn't count as concrete action evidence
        assert ev.has_concrete_action_evidence is False

    def test_counts_activity_log_events_excluding_comment_created(self, tmp_path: Path):
        db = _make_db(tmp_path)
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO activity_log (id, action, target_type, target_id, run_id) "
                "VALUES ('al1', 'issue.updated', 'issue', 'i1', 'run-1')"
            )
            # comment.created should be excluded
            conn.execute(
                "INSERT INTO activity_log (id, action, target_type, target_id, run_id) "
                "VALUES ('al2', 'comment.created', 'comment', 'c1', 'run-1')"
            )
        ev = collect_run_evidence(db, run_id="run-1")
        assert ev.activity_events_created == 1  # only issue.updated counted

    def test_counts_tool_access_allowed_non_adapter(self, tmp_path: Path):
        db = _make_db(tmp_path)
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO tool_access (id, run_id, agent_id, tool_name, decision) "
                "VALUES ('ta1', 'run-1', 'a1', 'file_read', 'allowed')"
            )
            # adapter startup grant should be excluded
            conn.execute(
                "INSERT INTO tool_access (id, run_id, agent_id, tool_name, decision) "
                "VALUES ('ta2', 'run-1', 'a1', 'adapter:openai_api', 'allowed')"
            )
        ev = collect_run_evidence(db, run_id="run-1")
        assert ev.tool_events_created == 1  # only file_read counted

    def test_denied_tool_access_not_counted(self, tmp_path: Path):
        db = _make_db(tmp_path)
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO tool_access (id, run_id, agent_id, tool_name, decision) "
                "VALUES ('ta1', 'run-1', 'a1', 'delete_file', 'denied')"
            )
        ev = collect_run_evidence(db, run_id="run-1")
        assert ev.tool_events_created == 0


# ---------------------------------------------------------------------------
# reconcile_stalled_subtrees — integration tests
# ---------------------------------------------------------------------------


def _make_stall_db(tmp_path: Path) -> Path:
    """DB with lead + parent in_progress + one blocked build child."""
    from aiteam.db.migration import SCHEMA_PATH

    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Game')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES (?, ?, ?, ?)",
            ("role:lead", "lead", "Lead", "lead_builtin"),
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type, supervisor_agent_id) VALUES (?, ?, ?, ?, ?)",
            ("role:engineer", "engineer", "Engineer", "openai_api", "role:lead"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)
            VALUES ('issue:intake', 'goal-1', 'Build game', 'in_progress', 'lead', 'role:lead')
            """
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)
            VALUES ('issue:intake:build', 'goal-1', 'issue:intake',
                    'Implement', 'blocked', 'engineer', 'role:engineer')
            """
        )
        conn.commit()
    return db


class TestReconcileStalledSubtrees:
    def test_enqueues_subtree_stalled_wakeup_for_supervisor(self, tmp_path: Path):
        from aiteam.db.liveness import reconcile_stalled_subtrees

        db = _make_stall_db(tmp_path)
        enqueued = reconcile_stalled_subtrees(db)

        assert "issue:intake" in enqueued
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            wakeup = conn.execute(
                """
                SELECT agent_id, reason, payload_json, idempotency_key
                FROM wakeup_requests
                WHERE reason = 'subtree_stalled'
                """
            ).fetchone()
        assert wakeup is not None
        assert wakeup["agent_id"] == "role:lead"
        payload = __import__("json").loads(wakeup["payload_json"])
        assert payload["issue_id"] == "issue:intake"
        assert payload["wake_reason"] == "child_report"
        assert "issue:intake:build" in payload["blocked_child_ids"]

    def test_idempotent_does_not_double_enqueue(self, tmp_path: Path):
        from aiteam.db.liveness import reconcile_stalled_subtrees

        db = _make_stall_db(tmp_path)
        first = reconcile_stalled_subtrees(db)
        second = reconcile_stalled_subtrees(db)

        assert first  # first call enqueues
        assert not second  # second call: parent issue now has live wakeup — skipped

    def test_no_escalation_when_some_children_progressing(self, tmp_path: Path):
        from aiteam.db.liveness import reconcile_stalled_subtrees

        db = _make_stall_db(tmp_path)
        # Add a non-blocked, non-terminal child
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                """
                INSERT INTO issues (id, goal_id, parent_id, title, status, role)
                VALUES ('issue:intake:review', 'goal-1', 'issue:intake',
                        'Review', 'in_progress', 'reviewer')
                """
            )
            conn.commit()
        enqueued = reconcile_stalled_subtrees(db)

        assert "issue:intake" not in enqueued  # not all blocked → no escalation

    def test_no_escalation_when_parent_is_not_in_progress(self, tmp_path: Path):
        from aiteam.db.liveness import reconcile_stalled_subtrees

        db = _make_stall_db(tmp_path)
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE issues SET status = 'done' WHERE id = 'issue:intake'"
            )
            conn.commit()
        enqueued = reconcile_stalled_subtrees(db)

        assert not enqueued

    def test_no_escalation_when_all_children_terminal(self, tmp_path: Path):
        from aiteam.db.liveness import reconcile_stalled_subtrees

        db = _make_stall_db(tmp_path)
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE issues SET status = 'done' WHERE id = 'issue:intake:build'"
            )
            conn.commit()
        enqueued = reconcile_stalled_subtrees(db)

        assert not enqueued

    def test_escalates_even_when_supervisor_has_unrelated_live_wakeup(self, tmp_path: Path):
        """Live bug: a root issue kept receiving child_report wakeups that
        never produced a lead.unblock_attempted for the specific blocked
        child. The old gate ('any wakeup pointing at the parent') treated
        that churn as 'being handled' and silenced the escalation for 24h+.
        The gate must be 'is THIS stall's escalation already pending', not
        'does the supervisor have any wakeup at all'."""
        from aiteam.db.interactions import create_interaction
        from aiteam.db.liveness import reconcile_stalled_subtrees
        from aiteam.db.wakeups import enqueue_wakeup

        db = _make_stall_db(tmp_path)
        # A live wakeup exists for the parent for a COMPLETELY unrelated
        # reason (e.g. a fresh child_report re-enqueued by another
        # reconciler) — the old code treated this as "already handled".
        enqueue_wakeup(
            db, agent_id="role:lead", source="reconcile", reason="child_report",
            payload={"issue_id": "issue:intake", "wake_reason": "child_report"},
        )

        enqueued = reconcile_stalled_subtrees(db)

        assert "issue:intake" in enqueued
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            interaction = conn.execute(
                "SELECT status FROM issue_thread_interactions WHERE issue_id = 'issue:intake'"
            ).fetchone()
        assert interaction is not None and interaction["status"] == "pending"

    def test_second_call_after_resolution_does_not_re_escalate_same_stall(self, tmp_path: Path):
        """Idempotency still holds: resolving the escalation must not cause
        an infinite re-escalation loop for the identical blocked-id set."""
        from aiteam.db.interactions import resolve_interaction
        from aiteam.db.liveness import reconcile_stalled_subtrees

        db = _make_stall_db(tmp_path)
        first = reconcile_stalled_subtrees(db)
        assert first
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            interaction_id = conn.execute(
                "SELECT id FROM issue_thread_interactions WHERE issue_id = 'issue:intake'"
            ).fetchone()["id"]
        resolve_interaction(db, interaction_id=interaction_id, action="accept", resolved_by_user_id="user")

        second = reconcile_stalled_subtrees(db)

        # Same blocked_ids → same idempotency_key → create_interaction returns
        # the existing (now resolved) row instead of a fresh pending one.
        assert not second
