"""Tests for the test_runner Tier 3 scout role.

test_runner is a Tier 3 specialist: it executes commands and reports
stdout/exitcode without making verdicts.  It is subject to the same
Tier 3 op filter as file_scout, web_scout, and context_curator.

Covered:
  test_test_runner_role_in_tier3_set
  test_test_runner_cannot_create_issues
  test_test_runner_cannot_update_plan
  test_test_runner_can_add_comment_and_set_status
  test_test_runner_in_workspace_reader_roles
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aiteam.adapters.work_contract import filter_forbidden_ops_for_role, _TIER3_ROLES_FOR_VALIDATION


class TestTestRunnerTier3Membership:

    def test_test_runner_role_in_tier3_set(self) -> None:
        """test_runner must be in the Tier 3 validation set."""
        assert "test_runner" in _TIER3_ROLES_FOR_VALIDATION

    def test_test_runner_in_executor_tier3_roles(self) -> None:
        """test_runner must be in executor._TIER3_ROLES for scout-blocker handling."""
        from aiteam.heartbeat.executor import RunExecutor
        assert "test_runner" in RunExecutor._TIER3_ROLES

    def test_test_runner_in_workspace_reader_roles(self) -> None:
        """test_runner receives workspace_files in its wake payload (to read files before running tests)."""
        from aiteam.heartbeat.executor import _WORKSPACE_READER_ROLES
        assert "test_runner" in _WORKSPACE_READER_ROLES


class TestTestRunnerForbiddenOps:

    def test_test_runner_cannot_create_issues(self) -> None:
        ops = [
            {"type": "add_comment", "body": "Tests ran."},
            {"type": "create_issue", "title": "Fix failures", "role": "engineer"},
            {"type": "set_status", "status": "done"},
        ]
        allowed, dropped = filter_forbidden_ops_for_role(ops, role="test_runner")
        assert len(dropped) == 1
        assert dropped[0]["type"] == "create_issue"
        assert len(allowed) == 2

    def test_test_runner_cannot_update_plan(self) -> None:
        ops = [
            {"type": "add_comment", "body": "Tests ran."},
            {"type": "update_plan", "title": "Updated plan", "body": "# Plan\n..."},
            {"type": "set_status", "status": "done"},
        ]
        allowed, dropped = filter_forbidden_ops_for_role(ops, role="test_runner")
        assert any(op["type"] == "update_plan" for op in dropped)
        assert not any(op["type"] == "update_plan" for op in allowed)

    def test_test_runner_cannot_write_files(self) -> None:
        ops = [
            {"type": "add_comment", "body": "Tests ran."},
            {"type": "write_file", "path": "test_results.json", "body": "{}"},
            {"type": "set_status", "status": "done"},
        ]
        allowed, dropped = filter_forbidden_ops_for_role(ops, role="test_runner")
        assert any(op["type"] == "write_file" for op in dropped)
        assert not any(op["type"] == "write_file" for op in allowed)

    def test_test_runner_can_add_comment_and_set_status(self) -> None:
        """Core allowed ops must pass through unchanged."""
        ops = [
            {"type": "add_comment", "body": "pytest tests/: exit 0\nnpm test: exit 1"},
            {"type": "set_status", "status": "done"},
            {"type": "notify_supervisor"},
        ]
        allowed, dropped = filter_forbidden_ops_for_role(ops, role="test_runner")
        assert dropped == []
        assert len(allowed) == 3

    def test_test_runner_cannot_create_interaction(self) -> None:
        ops = [
            {"type": "add_comment", "body": "Tests ran."},
            {
                "type": "create_interaction",
                "kind": "request_confirmation",
                "title": "Tests failed — should I fix?",
                "summary": "3 tests failed.",
                "payload": {"reason": "test_runner_wants_decision"},
            },
            {"type": "set_status", "status": "done"},
        ]
        allowed, dropped = filter_forbidden_ops_for_role(ops, role="test_runner")
        assert any(op["type"] == "create_interaction" for op in dropped)
        assert not any(op["type"] == "create_interaction" for op in allowed)

    def test_test_runner_reports_done_even_when_command_exits_nonzero(self) -> None:
        """result: done means all commands ran (even with non-zero exit). Verifying filter doesn't affect this."""
        # The filter only drops forbidden ops — the result semantics are in the skill, not in code.
        # This test verifies that a test_runner with result:done (all commands ran) passes through add_comment.
        ops = [
            {
                "type": "add_comment",
                "body": (
                    "pytest tests/: exit 1 (3 failures)\n"
                    "| Command | Exit code | Status |\n"
                    "|---|---|---|\n"
                    "| pytest tests/ | 1 | ✗ ran (non-zero) |\n\n"
                    "---AGENT-REPORT---\n"
                    "role: test_runner\n"
                    "result: done\n"
                    "issue_status: done\n"
                    "next_owner: lead\n"
                    "blocker: none\n"
                    "evidence: pytest tests/ (exit 1)\n"
                ),
            },
            {"type": "set_status", "status": "done"},
        ]
        allowed, dropped = filter_forbidden_ops_for_role(ops, role="test_runner")
        assert dropped == []
        assert len(allowed) == 2
