"""Tests for lead_executor — senior execution arm of the Lead.

lead_executor is a Tier 1 (senior) role created on demand by route_action
when criticality+complexity places an action at LEAD_SELF tier.

Covered:
  test_lead_executor_agent_created_on_first_lead_self_routing
  test_lead_executor_seniority_is_senior
  test_lead_executor_uses_lead_adapter
  test_lead_executor_reports_to_lead_via_notify_supervisor
  test_lead_executor_role_routed_correctly_for_critical_high_action
  test_lead_executor_skill_loadable
  test_lead_executor_not_in_tier3_filter
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aiteam.action_routing import Routing, pick_role_for_routing, route_action
from aiteam.adapters.registry import AdapterDescriptor, ExecutionResult, build_default_registry
from aiteam.adapters.work_contract import filter_forbidden_ops_for_role, _TIER3_ROLES_FOR_VALIDATION
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler
from aiteam.skills import load_skill


# ── Pure unit tests ───────────────────────────────────────────────────────────

class TestLeadExecutorRouting:

    def test_lead_executor_role_routed_correctly_for_critical_high_action(self) -> None:
        """critical + high + code → LEAD_SELF → lead_executor."""
        routing = route_action(criticality="critical", complexity="high", action_type="code")
        assert routing == Routing.LEAD_SELF
        role = pick_role_for_routing(routing, action_type="code")
        assert role == "lead_executor"

    def test_lead_executor_not_in_tier3_filter(self) -> None:
        """lead_executor is NOT Tier 3 — it must NOT have forbidden ops filtered."""
        ops = [
            {"type": "write_file", "path": "src/critical.py", "body": "x = 1"},
            {"type": "create_issue", "title": "Follow-up", "role": "reviewer"},
            {"type": "set_status", "status": "done"},
        ]
        allowed, dropped = filter_forbidden_ops_for_role(ops, role="lead_executor")
        assert dropped == [], "lead_executor must not have any ops filtered"
        assert len(allowed) == 3

    def test_lead_executor_not_in_tier3_validation_set(self) -> None:
        assert "lead_executor" not in _TIER3_ROLES_FOR_VALIDATION


class TestLeadExecutorSkill:

    def test_lead_executor_skill_loadable(self) -> None:
        skill = load_skill("lead_executor")
        assert skill is not None, "skills/lead_executor.md must exist and be loadable"

    def test_lead_executor_skill_mentions_action_type(self) -> None:
        skill = load_skill("lead_executor")
        assert skill is not None
        assert "action_type" in skill

    def test_lead_executor_skill_mentions_notify_supervisor(self) -> None:
        skill = load_skill("lead_executor")
        assert skill is not None
        assert "notify_supervisor" in skill

    def test_lead_executor_skill_mentions_senior(self) -> None:
        skill = load_skill("lead_executor")
        assert skill is not None
        assert "senior" in skill.lower()


# ── Integration: agent creation ───────────────────────────────────────────────

def _init_db(db_path: Path, lead_adapter_type: str = "openai_api") -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Test goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Team Lead", "lead", lead_adapter_type),
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "issue:intake", "goal-1", "Build critical auth module",
                "in_progress", "lead", "role:lead",
                json.dumps({"profile": "full_team"}),
            ),
        )
        conn.commit()


class _LeadCreatesLeadExecutorIssue:
    """Lead proposes a critical+high code action → executor creates lead_executor child."""

    class _Descriptor:
        adapter_type = "openai_api"
        provider = "openai"
        model = "gpt-4"
        channel = "api"

    descriptor = _Descriptor()

    def build_env(self, *, run_id: str, wake_context: dict[str, Any]) -> dict[str, str]:
        return {}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="Lead routed critical action to self",
            actions={
                "create_issues": [
                    {
                        "title": "Implement critical authentication module",
                        "description": "Critical core auth — must be done at senior level.",
                        "role": "engineer",  # LLM proposed engineer
                        "complexity": "high",
                        "criticality": "critical",
                        "action_type": "code",
                    }
                ],
                "issue_status": "in_progress",
            },
        )


class TestLeadExecutorAgentCreation:

    def test_lead_executor_agent_created_on_first_lead_self_routing(self, tmp_path: Path) -> None:
        """When routing overrides to lead_executor, the agent must be created in DB."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path, lead_adapter_type="openai_api")

        registry = build_default_registry()
        registry._items["openai_api"] = _LeadCreatesLeadExecutorIssue()

        enqueue_wakeup(db_path, agent_id="role:lead", source="manual", reason="manual",
                       payload={"issue_id": "issue:intake"})
        dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
        assert dispatch is not None
        RunExecutor(db_path, registry).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            agent = conn.execute(
                "SELECT id, role, seniority, adapter_type FROM agents WHERE id = 'role:lead_executor'"
            ).fetchone()
        assert agent is not None, "role:lead_executor agent must be created"
        assert agent["role"] == "lead_executor"

    def test_lead_executor_seniority_is_senior(self, tmp_path: Path) -> None:
        """lead_executor must be created with seniority='senior'."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path, lead_adapter_type="openai_api")

        registry = build_default_registry()
        registry._items["openai_api"] = _LeadCreatesLeadExecutorIssue()

        enqueue_wakeup(db_path, agent_id="role:lead", source="manual", reason="manual",
                       payload={"issue_id": "issue:intake"})
        dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
        RunExecutor(db_path, registry).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            agent = conn.execute(
                "SELECT seniority FROM agents WHERE id = 'role:lead_executor'"
            ).fetchone()
        assert agent is not None
        assert agent["seniority"] == "senior", (
            f"lead_executor must have seniority='senior', got {agent['seniority']!r}"
        )

    def test_lead_executor_uses_lead_adapter(self, tmp_path: Path) -> None:
        """lead_executor must inherit the Lead's adapter_type."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path, lead_adapter_type="openai_api")

        registry = build_default_registry()
        registry._items["openai_api"] = _LeadCreatesLeadExecutorIssue()

        enqueue_wakeup(db_path, agent_id="role:lead", source="manual", reason="manual",
                       payload={"issue_id": "issue:intake"})
        dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
        RunExecutor(db_path, registry).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            agent = conn.execute(
                "SELECT adapter_type FROM agents WHERE id = 'role:lead_executor'"
            ).fetchone()
        assert agent is not None
        assert agent["adapter_type"] == "openai_api", (
            f"lead_executor must inherit Lead's adapter_type='openai_api', got {agent['adapter_type']!r}"
        )

    def test_lead_executor_child_issue_created_with_correct_role(self, tmp_path: Path) -> None:
        """The delegated issue for a critical+high action must have role='lead_executor'."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path, lead_adapter_type="openai_api")

        registry = build_default_registry()
        registry._items["openai_api"] = _LeadCreatesLeadExecutorIssue()

        enqueue_wakeup(db_path, agent_id="role:lead", source="manual", reason="manual",
                       payload={"issue_id": "issue:intake"})
        dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
        RunExecutor(db_path, registry).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            child = conn.execute(
                "SELECT role, assignee_agent_id FROM issues WHERE parent_id = 'issue:intake' AND title LIKE '%auth%'"
            ).fetchone()
        assert child is not None
        assert child["role"] == "lead_executor", (
            f"Child issue must have role='lead_executor', got {child['role']!r}"
        )
        assert child["assignee_agent_id"] == "role:lead_executor"
