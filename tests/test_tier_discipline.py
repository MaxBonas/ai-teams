"""Tests for Tier 2/3 boundary enforcement.

Phase 1 — Tier discipline:

- filter_forbidden_ops_for_role: Tier 3 scouts cannot create issues, interactions,
  update plans, or write/delete workspace files.
- Executor _apply_result_actions: Tier 3 forbidden action groups are dropped with
  a warning log before any DB writes occur.

Covered:
  test_filter_forbidden_ops_drops_create_issue_for_file_scout
  test_filter_forbidden_ops_drops_write_file_for_context_curator
  test_filter_forbidden_ops_preserves_add_comment_for_tier3
  test_filter_forbidden_ops_no_effect_on_tier2_engineer
  test_executor_drops_create_interaction_for_web_scout
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aiteam.adapters.work_contract import filter_forbidden_ops_for_role
from aiteam.adapters.registry import build_default_registry
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


# ── Unit: filter_forbidden_ops_for_role ──────────────────────────────────────

class TestFilterForbiddenOps:

    def test_filter_forbidden_ops_drops_create_issue_for_file_scout(self) -> None:
        ops = [
            {"type": "add_comment", "body": "Found files."},
            {"type": "create_issue", "title": "New sub-task", "role": "engineer"},
            {"type": "set_status", "status": "done"},
        ]
        allowed, dropped = filter_forbidden_ops_for_role(ops, role="file_scout")
        assert len(allowed) == 2
        assert len(dropped) == 1
        assert dropped[0]["type"] == "create_issue"
        assert all(op["type"] != "create_issue" for op in allowed)

    def test_filter_forbidden_ops_drops_write_file_for_context_curator(self) -> None:
        ops = [
            {"type": "add_comment", "body": "Plan compressed."},
            {"type": "write_file", "path": "src/main.py", "body": "print('hi')"},
            {"type": "append_file", "path": "README.md", "body": "\n## New section"},
            {"type": "delete_file", "path": "old_file.txt"},
            {"type": "set_status", "status": "done"},
        ]
        allowed, dropped = filter_forbidden_ops_for_role(ops, role="context_curator")
        dropped_types = {op["type"] for op in dropped}
        allowed_types = {op["type"] for op in allowed}
        assert dropped_types == {"write_file", "append_file", "delete_file"}
        assert allowed_types == {"add_comment", "set_status"}

    def test_filter_forbidden_ops_preserves_add_comment_for_tier3(self) -> None:
        """add_comment, set_status, and notify_supervisor are always allowed for Tier 3."""
        ops = [
            {"type": "add_comment", "body": "Web search complete."},
            {"type": "set_status", "status": "done"},
            {"type": "notify_supervisor"},
        ]
        for role in ("file_scout", "web_scout", "context_curator", "test_runner"):
            allowed, dropped = filter_forbidden_ops_for_role(ops, role=role)
            assert dropped == [], f"role={role} should not drop {ops}"
            assert len(allowed) == 3

    def test_filter_tier2_keeps_work_ops_drops_lead_levers(self) -> None:
        """Tier 2 keeps its work vocabulary (write_file, set_status,
        create_interaction) but must not pull Lead levers: create_issue,
        update_child_issue, update_plan collapse the hierarchy."""
        ops = [
            {"type": "write_file", "path": "src/app.py", "body": "x = 1"},
            {"type": "create_issue", "title": "Sub-task", "role": "reviewer"},
            {"type": "update_child_issue", "path": "child-1", "status": "todo"},
            {"type": "update_plan", "title": "Plan", "body": "..."},
            {"type": "set_status", "status": "done"},
            {"type": "add_comment", "body": "done"},
        ]
        for role in ("engineer", "reviewer", "software_engineer", "code_reviewer"):
            allowed, dropped = filter_forbidden_ops_for_role(ops, role=role)
            dropped_types = sorted(op["type"] for op in dropped)
            assert dropped_types == ["create_issue", "update_child_issue", "update_plan"], f"role={role}"
            assert {op["type"] for op in allowed} == {"write_file", "set_status", "add_comment"}

    def test_filter_no_effect_on_lead_tier1(self) -> None:
        """Tier 1 keeps the full vocabulary — it orchestrates."""
        ops = [
            {"type": "create_issue", "title": "Sub-task", "role": "engineer"},
            {"type": "update_child_issue", "path": "child-1", "status": "todo"},
            {"type": "update_plan", "title": "Plan", "body": "..."},
            {"type": "set_status", "status": "done"},
        ]
        for role in ("lead", "team_lead", "lead_executor"):
            allowed, dropped = filter_forbidden_ops_for_role(ops, role=role)
            assert dropped == [], f"role={role} should pass all ops through"
            assert len(allowed) == 4

    def test_filter_forbidden_ops_drops_create_interaction_for_web_scout(self) -> None:
        ops = [
            {"type": "add_comment", "body": "Found results."},
            {"type": "create_interaction", "kind": "request_confirmation",
             "title": "Approve?", "summary": "...", "payload": {"reason": "scout_wants_help"}},
            {"type": "set_status", "status": "done"},
        ]
        allowed, dropped = filter_forbidden_ops_for_role(ops, role="web_scout")
        assert len(dropped) == 1
        assert dropped[0]["type"] == "create_interaction"
        assert len(allowed) == 2

    def test_filter_forbidden_ops_drops_update_plan_for_test_runner(self) -> None:
        ops = [
            {"type": "add_comment", "body": "Tests ran."},
            {"type": "update_plan", "title": "New plan", "body": "# Plan"},
            {"type": "set_status", "status": "done"},
        ]
        allowed, dropped = filter_forbidden_ops_for_role(ops, role="test_runner")
        assert len(dropped) == 1
        assert dropped[0]["type"] == "update_plan"
        assert len(allowed) == 2

    def test_filter_role_case_insensitive(self) -> None:
        """Role comparison should be case-insensitive."""
        ops = [{"type": "create_issue", "title": "New task", "role": "engineer"}]
        allowed, dropped = filter_forbidden_ops_for_role(ops, role="FILE_SCOUT")
        assert len(dropped) == 1

    def test_filter_empty_ops_list_returns_empty(self) -> None:
        allowed, dropped = filter_forbidden_ops_for_role([], role="file_scout")
        assert allowed == []
        assert dropped == []


# ── Integration: executor drops Tier 3 forbidden actions ─────────────────────

def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Test goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Lead", "lead", "lead_builtin"),
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("role:web_scout", "web_scout", "Web Scout", "standard", "openai_api", "role:lead"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("issue:scout", "goal-1", "Search for API docs", "in_progress", "web_scout", "role:web_scout"),
        )
        conn.commit()


class _WebScoutWithForbiddenOpsRuntime:
    """Static adapter that returns a create_interaction (forbidden for web_scout)."""

    class _Descriptor:
        adapter_type = "static_test"
        provider = "test"
        model = "test"
        channel = "test"

    descriptor = _Descriptor()

    def build_env(self, **_: Any) -> dict[str, str]:
        return {}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> Any:
        from aiteam.adapters.registry import ExecutionResult
        return ExecutionResult(
            status="completed",
            output="Web scout ran",
            actions={
                "add_comments": ["I found the docs."],
                "interactions": [
                    {
                        "kind": "request_confirmation",
                        "title": "Should I search more?",
                        "summary": "scout wants user input",
                        "idempotency_key": "scout:ask:1",
                        "payload": {"version": 1, "reason": "scout_wants_confirmation"},
                        "continuation_policy": "wake_assignee",
                    }
                ],
                "issue_status": "done",
            },
        )


class TestExecutorDropsTier3ForbiddenActions:

    def test_executor_drops_create_interaction_for_web_scout(self, tmp_path: Path) -> None:
        """The executor must drop 'interactions' action group for web_scout."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)

        # Register the static adapter
        registry = build_default_registry()
        registry._items["static_test"] = _WebScoutWithForbiddenOpsRuntime()

        enqueue_wakeup(
            db_path,
            agent_id="role:web_scout",
            source="delegation",
            reason="new_task",
            payload={"issue_id": "issue:scout"},
        )
        dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:web_scout")
        assert dispatch is not None

        # Patch adapter_type in DB so executor picks our static adapter
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE agents SET adapter_type = 'static_test' WHERE id = 'role:web_scout'"
            )

        executor = RunExecutor(db_path, registry)
        executor.execute(dispatch)

        # The interaction must NOT have been created
        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM issue_thread_interactions WHERE issue_id = 'issue:scout'"
            ).fetchone()[0]
        assert count == 0, (
            "Interaction must be dropped for Tier 3 role 'web_scout' even if the adapter emitted it"
        )
