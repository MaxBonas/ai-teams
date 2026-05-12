"""Integration tests: executor applies route_action when creating delegated issues.

When the Lead emits a create_issue op with criticality + action_type, the executor
calls route_action() and overrides the proposed role if the routing disagrees.

Covered:
  test_create_delegated_issue_overrides_role_per_routing
  test_action_routed_activity_log_emitted
  test_non_routing_spec_creates_issue_with_proposed_role
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aiteam.adapters.registry import AdapterDescriptor, ExecutionResult, build_default_registry
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Test goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Team Lead", "lead", "openai_api"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "issue:intake", "goal-1", "Build critical feature",
                "in_progress", "lead", "role:lead",
                json.dumps({"profile": "full_team"}),
            ),
        )
        conn.commit()


class _LeadCreateIssueWithRoutingRuntime:
    """Simulates Lead proposing an engineer for a critical+high task.

    The routing override must change role to lead_executor.
    """

    class _Descriptor:
        adapter_type = "openai_api"
        provider = "openai"
        model = "gpt-4"
        channel = "api"

    descriptor = _Descriptor()

    def __init__(self, *, criticality: str, complexity: str, action_type: str, proposed_role: str) -> None:
        self.criticality = criticality
        self.complexity = complexity
        self.action_type = action_type
        self.proposed_role = proposed_role

    def build_env(self, *, run_id: str, wake_context: dict[str, Any]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="Lead created delegated issue with routing",
            actions={
                "create_issues": [
                    {
                        "title": "Implement critical auth module",
                        "description": "Core authentication — critical, high complexity",
                        "role": self.proposed_role,
                        "complexity": self.complexity,
                        "criticality": self.criticality,
                        "action_type": self.action_type,
                    }
                ],
                "issue_status": "in_progress",
            },
        )


def _run_lead(db_path: Path, runtime: Any) -> None:
    registry = build_default_registry()
    registry._items["openai_api"] = runtime
    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="manual",
        reason="manual",
        payload={"issue_id": "issue:intake"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    RunExecutor(db_path, registry).execute(dispatch)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestCreateDelegatedIssueRoutingOverride:

    def test_create_delegated_issue_overrides_role_per_routing(self, tmp_path: Path) -> None:
        """Lead proposes 'engineer' for critical+high task → executor overrides to 'lead_executor'."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _run_lead(db_path, _LeadCreateIssueWithRoutingRuntime(
            criticality="critical",
            complexity="high",
            action_type="code",
            proposed_role="engineer",
        ))
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            issues = conn.execute(
                "SELECT role FROM issues WHERE parent_id = 'issue:intake' AND title LIKE '%auth%'"
            ).fetchall()
        assert len(issues) == 1
        assert issues[0]["role"] == "lead_executor", (
            f"Expected lead_executor but got {issues[0]['role']} — "
            "routing must override the LLM-proposed engineer for critical+high actions"
        )

    def test_action_routed_activity_log_emitted(self, tmp_path: Path) -> None:
        """When routing overrides a role, an 'action.routed' activity log entry must be created."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _run_lead(db_path, _LeadCreateIssueWithRoutingRuntime(
            criticality="critical",
            complexity="high",
            action_type="code",
            proposed_role="engineer",
        ))
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            log_entries = conn.execute(
                "SELECT action, payload_json FROM activity_log WHERE action = 'action.routed'"
            ).fetchall()
        assert len(log_entries) >= 1
        payload = json.loads(log_entries[0]["payload_json"] or "{}")
        assert payload.get("proposed_role") == "engineer"
        assert payload.get("effective_role") == "lead_executor"
        assert payload.get("routing") == "lead_self"

    def test_non_routing_spec_creates_issue_with_proposed_role(self, tmp_path: Path) -> None:
        """When no criticality+action_type, the proposed role is used as-is."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)

        class _NoRoutingRuntime:
            class _Descriptor:
                adapter_type = "openai_api"
                provider = "openai"
                model = "gpt-4"
                channel = "api"
            descriptor = _Descriptor()

            def build_env(self, *, run_id: str, wake_context: dict[str, Any]) -> dict[str, str]:
                return {"AITEAM_RUN_ID": run_id}

            def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
                return ExecutionResult(
                    status="completed",
                    output="Lead created issue without routing",
                    actions={
                        "create_issues": [
                            {
                                "title": "Build basic feature",
                                "description": "Simple feature",
                                "role": "engineer",
                                "complexity": "medium",
                                # No criticality or action_type — no routing override
                            }
                        ],
                        "issue_status": "in_progress",
                    },
                )

        registry = build_default_registry()
        registry._items["openai_api"] = _NoRoutingRuntime()
        enqueue_wakeup(
            db_path, agent_id="role:lead", source="manual", reason="manual",
            payload={"issue_id": "issue:intake"},
        )
        dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
        assert dispatch is not None
        RunExecutor(db_path, registry).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            issues = conn.execute(
                "SELECT role FROM issues WHERE parent_id = 'issue:intake'"
            ).fetchall()
        assert any(i["role"] == "engineer" for i in issues), (
            "Without routing fields, the proposed 'engineer' role must be preserved"
        )
