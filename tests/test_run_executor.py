from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from aiteam.adapters.registry import (
    AdapterDescriptor,
    AdapterRegistry,
    ExecutionResult,
    StaticAdapterRuntime,
    build_default_registry,
)
from aiteam.adapters.subprocess_adapter import SubprocessAdapterRuntime
from aiteam.adapters.subscription_cli_adapter import ClaudeSubscriptionCliRuntime
from aiteam.db.finops import current_period
from aiteam.db.interactions import create_interaction, resolve_interaction
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler
from aiteam.user_config import model_options, record_model_health


@pytest.fixture(autouse=True)
def _verified_api_model_fixture(tmp_path_factory, monkeypatch) -> None:
    """Los tests del executor no auditan catálogo salvo cuando lo declaran."""
    user_config_dir = tmp_path_factory.mktemp("executor-user-config")
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(user_config_dir))
    for profile_id in ("openai_api", "gemini_api", "anthropic_api"):
        for option in model_options().get(profile_id, []):
            record_model_health(
                profile_id, str(option["value"]), available=True, reason="executor fixture"
            )


def _init_db(
    db_path: Path,
    *,
    criticality: str = "medium",
    budget_monthly_cents: int = 0,
) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type, budget_monthly_cents) VALUES (?, ?, ?, ?, ?)",
            ("agent-1", "engineer", "Engineer", "subscription_cli", budget_monthly_cents),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, criticality, assignee_agent_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("issue-1", "goal-1", "Implement feature", "todo", criticality, "agent-1"),
        )
        conn.commit()


class _OkRuntime:
    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(status="completed", output="done", actual_cost_cents=5)


class _FailRuntime:
    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(status="failed", error="adapter error", exit_code=1)


class _ModelUnavailableRuntime:
    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def __init__(self) -> None:
        self.calls = 0

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(
            status="failed",
            error="model requires a newer version of Codex",
            error_code="model_unavailable",
            exit_code=1,
        )


class _CountingRuntime:
    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def __init__(self) -> None:
        self.calls = 0

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(status="completed", output="done")


class _LeadCreateIssuesRuntime:
    descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="plan delegated",
            actions={
                "create_issues": [
                    {
                        "title": "Implement playable prototype",
                        "description": "Build the first playable loop.",
                        "role": "engineer",
                        "complexity": "medium",
                    },
                    {
                        "title": "Review prototype risks",
                        "description": "Review implementation risks.",
                        "role": "reviewer",
                        "complexity": "medium",
                    },
                ],
                "issue_status": "in_progress",
            },
        )


class _LeadCreateIssuesWithoutReviewerRuntime:
    descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="plan delegated without review",
            actions={
                "create_issues": [
                    {
                        "title": "Implement playable prototype",
                        "description": "Build the first playable loop.",
                        "role": "engineer",
                        "complexity": "medium",
                    },
                    {
                        "title": "Run test suite",
                        "description": "Execute tests and report exit codes.",
                        "role": "test_runner",
                        "complexity": "medium",
                    },
                ],
                "issue_status": "in_progress",
            },
        )


class _OpenAIOkRuntime:
    descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(status="completed", output="openai ok")


class _OpenAIImplementationClaimRuntime:
    descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(status="completed", output="Implementado el prototipo en cartografo-ecos/index.html.")


class _OpenAIDeliveryWithoutVerbRuntime:
    descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(status="completed", output="Entrega de prototipo con archivos README.md e index.html.")


class _AmbiguousEvidenceRuntime:
    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(status="completed", output="Entrega de prototipo con README.md e index.html.")


class _WritingImplementationRuntime:
    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        workspace = self.db_path.parent
        target_dir = workspace / "cartografo-ecos"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "index.html").write_text("<h1>Cartografo de Ecos</h1>\n", encoding="utf-8")
        return ExecutionResult(status="completed", output="Implementado el prototipo en cartografo-ecos/index.html.")


class _LeadPlanCommentRuntime:
    descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="plan emitted",
            actions={
                "add_comments": [
                    (
                        "## Plan inicial con accountability\n\n"
                        "Objetivo: entregar una primera version.\n\n"
                        "Sub-issues: implementacion y QA.\n\n"
                        "Riesgos: alcance y pruebas.\n\n"
                        "Criterio: evidencia visible."
                    )
                ]
            },
        )


def _dispatch_one(db_path: Path) -> Any:
    enqueue_wakeup(
        db_path,
        agent_id="agent-1",
        source="assignment",
        reason="new_issue",
        payload={"issue_id": "issue-1"},
    )
    scheduler = HeartbeatScheduler(db_path)
    return scheduler.dispatch_next()


@pytest.mark.parametrize(
    ("owner_action", "expected_model", "expected_status", "expected_queued"),
    [
        ("accept", "gpt-5.6-terra", "todo", 1),
        ("reject", "gpt-5.6-luna", "blocked", 0),
    ],
)
def test_model_unavailable_requires_owner_before_same_profile_fallback(
    tmp_path: Path,
    monkeypatch,
    owner_action: str,
    expected_model: str,
    expected_status: str,
    expected_queued: int,
) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    import aiteam.user_config as config_mod

    monkeypatch.setattr(config_mod, "_codex_catalog_compatibility", lambda _config: {
        "status": "cli_update_required",
        "installed_version": "0.128.0",
        "catalog_client_version": "0.145.0",
        "models": [],
    })
    record_model_health("codex_subscription", "gpt-5.6-terra", available=True, reason="run_completed")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE agents SET adapter_config_json = ? WHERE id = 'agent-1'",
            (json.dumps({
                "profile_id": "codex_subscription",
                "model": "gpt-5.6-luna",
                "cli_kind": "codex",
            }),),
        )
        conn.commit()

    runtime = _ModelUnavailableRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))
    executor.execute(_dispatch_one(db_path))

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        issue = conn.execute("SELECT status FROM issues WHERE id = 'issue-1'").fetchone()
        interaction = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE issue_id = 'issue-1' AND status = 'pending'"
        ).fetchone()
    assert issue["status"] == "blocked"
    assert interaction is not None
    payload = json.loads(interaction["payload_json"])
    assert payload["reason"] == "model_fallback_required"
    assert payload["failed_model"] == "gpt-5.6-luna"
    assert payload["proposed_model"] == "gpt-5.6-terra"
    assert payload["changes_tier"] is True

    resolve_interaction(
        db_path,
        interaction_id=interaction["id"],
        action=owner_action,
        resolved_by_user_id="user",
    )
    transition = HeartbeatScheduler(db_path).dispatch_next(agent_id="agent-1")
    assert transition is not None
    executor.execute(transition)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        agent = conn.execute(
            "SELECT adapter_config_json FROM agents WHERE id = 'agent-1'"
        ).fetchone()
        issue = conn.execute("SELECT status FROM issues WHERE id = 'issue-1'").fetchone()
        queued = conn.execute(
            "SELECT COUNT(*) AS n FROM wakeup_requests WHERE status = 'queued' AND source = 'model_fallback'"
        ).fetchone()
    assert json.loads(agent["adapter_config_json"])["model"] == expected_model
    assert issue["status"] == expected_status
    assert queued["n"] == expected_queued
    assert runtime.calls == 0


def test_runtime_preflight_blocks_incompatible_role_without_calling_model(tmp_path: Path) -> None:
    from aiteam.project_adapters import write_project_adapter_policy

    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api"])
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE agents
            SET role = 'lead', adapter_type = 'openai_api', adapter_config_json = ?
            WHERE id = 'agent-1'
            """,
            (json.dumps({"profile_id": "openai_api", "model": "gpt-5.6-luna"}),),
        )
        conn.execute(
            "UPDATE issues SET role = 'lead', metadata_json = ? WHERE id = 'issue-1'",
            (json.dumps({"profile": "full_team", "data_class": "internal"}),),
        )
        conn.commit()

    class _CountingOpenAIRuntime:
        descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

        def __init__(self) -> None:
            self.calls = 0

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            self.calls += 1
            return ExecutionResult(status="completed", output="no debe ejecutarse")

    runtime = _CountingOpenAIRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))
    executor.execute(_dispatch_one(db_path))

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        issue = conn.execute("SELECT status FROM issues WHERE id = 'issue-1'").fetchone()
        interaction = conn.execute(
            """
            SELECT payload_json FROM issue_thread_interactions
            WHERE issue_id = 'issue-1' AND status = 'pending'
            """
        ).fetchone()
        denied = conn.execute(
            "SELECT COUNT(*) AS n FROM tool_access WHERE issue_id = 'issue-1' AND decision = 'denied'"
        ).fetchone()

    assert runtime.calls == 0
    assert issue["status"] == "blocked"
    payload = json.loads(interaction["payload_json"])
    assert payload["reason"] == "model_compatibility_blocked"
    assert payload["decision"]["code"] == "model_tier_insufficient"
    assert int(denied["n"]) == 1


@pytest.mark.parametrize("verified", [False, True])
def test_runtime_requires_exact_api_model_probe_before_consumption(
    tmp_path: Path, monkeypatch, verified: bool,
) -> None:
    from aiteam.project_adapters import write_project_adapter_policy

    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "isolated-user-config"))
    if verified:
        record_model_health(
            "openai_api", "gpt-5.6-terra", available=True, reason="live_test_completed"
        )
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api"])
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE agents SET adapter_type='openai_api', adapter_config_json=? WHERE id='agent-1'",
            (json.dumps({"profile_id": "openai_api", "model": "gpt-5.6-terra"}),),
        )
        conn.execute(
            "UPDATE issues SET role='engineer', metadata_json=? WHERE id='issue-1'",
            (json.dumps({"profile": "full_team", "data_class": "internal"}),),
        )
        conn.commit()

    class _Runtime:
        descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

        def __init__(self) -> None:
            self.calls = 0

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            self.calls += 1
            return ExecutionResult(status="completed", output="Plan técnico completado.")

    runtime = _Runtime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))
    executor.execute(_dispatch_one(db_path))

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        interaction = conn.execute(
            """
            SELECT payload_json FROM issue_thread_interactions
            WHERE issue_id='issue-1' AND status='pending'
            ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()

    assert runtime.calls == (1 if verified else 0)
    if not verified:
        payload = json.loads(interaction["payload_json"])
        assert payload["reason"] == "model_assignment_required"
        assert payload["required_action"] == "verify_model_or_change_team_assignment"


def _init_lead_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Game"))
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Team Lead", "lead", "lead_builtin"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, description, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("issue:intake", "goal-1", "Build a game", "Build a game with a hired team", "todo", "lead", "role:lead"),
        )
        conn.commit()


def _dispatch_lead(db_path: Path, *, payload: dict[str, Any] | None = None) -> Any:
    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="manual",
        reason=(payload or {}).get("wake_reason") or "manual",
        payload={"issue_id": "issue:intake", **(payload or {})},
    )
    return HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")


def test_executor_completes_run_and_wakeup(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    registry = AdapterRegistry([_OkRuntime()])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()
        wakeup = conn.execute(
            "SELECT * FROM wakeup_requests WHERE id = ?",
            (dispatch.wakeup_request["id"],),
        ).fetchone()
        events = conn.execute(
            "SELECT * FROM run_events WHERE run_id = ?",
            (dispatch.run["id"],),
        ).fetchall()
        activity = conn.execute(
            "SELECT * FROM activity_log WHERE run_id = ?",
            (dispatch.run["id"],),
        ).fetchall()

    assert run["status"] == "completed"
    assert run["actual_cost_cents"] == 5
    assert run["exit_code"] is None
    assert wakeup["status"] == "finished"
    assert wakeup["run_id"] == run["id"]
    assert len(events) == 1
    assert json.loads(events[0]["payload_json"])["text"] == "done"
    # issue.auto_in_progress now fires before comment.created when issue starts
    # in 'todo' — assert the comment is present rather than the exact list.
    assert "comment.created" in [row["action"] for row in activity]
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cost = conn.execute(
            "SELECT * FROM cost_events WHERE run_id = ?",
            (dispatch.run["id"],),
        ).fetchone()
        tool_access = conn.execute(
            "SELECT * FROM tool_access WHERE run_id = ? ORDER BY created_at ASC, rowid ASC",
            (dispatch.run["id"],),
        ).fetchall()
        spent = conn.execute(
            "SELECT spent_monthly_cents FROM agents WHERE id = ?",
            ("agent-1",),
        ).fetchone()[0]
    assert cost["cost_cents"] == 5
    assert cost["agent_id"] == "agent-1"
    assert [(row["tool_name"], row["decision"]) for row in tool_access] == [
        ("adapter:subscription_cli", "allowed")
    ]
    assert spent == 5


@pytest.mark.parametrize("cli_kind,provider,channel", [
    ("codex", "openai-codex", "subscription"),
    ("opencode", "opencode-zen", "subscription"),
])
def test_subscription_run_injects_active_mcp_and_records_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    cli_kind: str, provider: str, channel: str,
) -> None:
    from aiteam.extensions import approve_mcp_server, approve_mcp_server_tools, set_mcp_server_health, set_mcp_server_status
    from aiteam.mcp_runtime import artifact_identity

    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE agents SET capabilities_json=? WHERE id='agent-1'",
            (json.dumps(["external_mcp"]),),
        )
        conn.commit()
    approve_mcp_server(
        tmp_path,
        name="docs",
        source=sys.executable,
        version="1.0.0",
        args=["-m", "fake_mcp"],
        applies_to_roles=["engineer"],
        approved_by="user",
    )
    set_mcp_server_status(tmp_path, name="docs", status="active")
    set_mcp_server_health(
        tmp_path,
        name="docs",
        health={
            "status": "ok",
            "server_version": "1.0.0",
            "artifact_identity": artifact_identity([sys.executable, "-m", "fake_mcp"]),
            "tools": [
                {"name": "lookup", "read_only": True},
                {"name": "publish", "read_only": False},
            ],
        },
    )
    approve_mcp_server_tools(
        tmp_path,
        name="docs",
        tools=[{"name": "lookup", "access": "read"}, {"name": "publish", "access": "write"}],
        approved_by="user",
    )
    captured: dict[str, str] = {}

    def _execute(_runtime, run, env):
        captured.update(env)
        return ExecutionResult(status="completed", output="done")

    monkeypatch.setattr(ClaudeSubscriptionCliRuntime, "execute", _execute)
    runtime = ClaudeSubscriptionCliRuntime(
        AdapterDescriptor(
            adapter_type="subscription_cli", channel=channel, provider=provider
        ),
        cli_kind=cli_kind,
    )
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    injected = json.loads(captured["AITEAM_MCP_SERVERS_JSON"])
    assert injected[0]["name"] == "docs"
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT decision, reason, metadata_json FROM tool_access "
            "WHERE run_id=? AND tool_name='mcp:docs'",
            (dispatch.run["id"],),
        ).fetchone()
    assert row[0] == "allowed"
    assert "health-checked" in row[1]
    assert json.loads(row[2])["version"] == "1.0.0"
    with sqlite3.connect(str(db_path)) as conn:
        tool_rows = conn.execute(
            "SELECT tool_name, decision FROM tool_access "
            "WHERE run_id=? AND tool_name LIKE 'mcp:docs:%' ORDER BY tool_name",
            (dispatch.run["id"],),
        ).fetchall()
    assert tool_rows == [("mcp:docs:lookup", "allowed"), ("mcp:docs:publish", "denied")]


def test_claude_read_only_role_denies_server_with_mixed_mcp_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aiteam.extensions import approve_mcp_server, approve_mcp_server_tools, set_mcp_server_health, set_mcp_server_status
    from aiteam.mcp_runtime import artifact_identity

    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE agents SET capabilities_json=? WHERE id='agent-1'",
            (json.dumps(["external_mcp"]),),
        )
        conn.commit()
    approve_mcp_server(
        tmp_path,
        name="mixed",
        source=sys.executable,
        version="1.0.0",
        applies_to_roles=["engineer"],
        approved_by="user",
    )
    set_mcp_server_status(tmp_path, name="mixed", status="active")
    set_mcp_server_health(
        tmp_path,
        name="mixed",
        health={
            "status": "ok",
            "server_version": "1.0.0",
            "artifact_identity": artifact_identity([sys.executable]),
            "tools": [
                {"name": "lookup", "read_only": True},
                {"name": "publish", "read_only": False},
            ],
        },
    )
    approve_mcp_server_tools(
        tmp_path,
        name="mixed",
        tools=[{"name": "lookup", "access": "read"}, {"name": "publish", "access": "write"}],
        approved_by="user",
    )
    captured: dict[str, str] = {}

    def _execute(_runtime, run, env):
        captured.update(env)
        return ExecutionResult(status="completed", output="done")

    monkeypatch.setattr(ClaudeSubscriptionCliRuntime, "execute", _execute)
    runtime = ClaudeSubscriptionCliRuntime(
        AdapterDescriptor(
            adapter_type="subscription_cli", channel="subscription", provider="anthropic"
        ),
        cli_kind="claude",
    )
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    assert "AITEAM_MCP_SERVERS_JSON" not in captured
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT tool_name, decision, reason FROM tool_access "
            "WHERE run_id=? AND tool_name LIKE 'mcp:mixed%' ORDER BY tool_name",
            (dispatch.run["id"],),
        ).fetchall()
    assert rows == [
        ("mcp:mixed", "denied", "mcp_adapter_cannot_enforce_tool_allowlist"),
        ("mcp:mixed:lookup", "denied", "mcp_adapter_cannot_enforce_tool_allowlist"),
        ("mcp:mixed:publish", "denied", "mcp_adapter_cannot_enforce_tool_allowlist"),
    ]


def test_builtin_lead_creates_structured_team_proposal(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)

    executor = RunExecutor(db_path, build_default_registry())
    dispatch = _dispatch_lead(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        interaction = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE issue_id = ?",
            ("issue:intake",),
        ).fetchone()
        comment = conn.execute(
            "SELECT * FROM issue_comments WHERE source_run_id = ?",
            (dispatch.run["id"],),
        ).fetchone()

    assert interaction["kind"] == "suggest_tasks"
    assert interaction["status"] == "pending"
    assert interaction["title"] == "Plan inicial y equipo propuesto"
    payload = json.loads(interaction["payload_json"])
    assert payload["profile"] == "full_team"
    team_ids = {m["id"] for m in payload["proposed_team"]}
    assert "role:engineer" in team_ids
    assert "role:reviewer" in team_ids
    # QA is no longer a default member — Reviewer absorbs static QA
    assert "role:qa" not in team_ids
    build_issue = next(item for item in payload["suggested_issues"] if item["id"] == "issue:intake:build")
    assert build_issue["delegation_type"] == "well_scoped_code_change"
    assert build_issue["cost_tier"] == "standard_worker"
    assert build_issue["report_to"] == "role:lead"
    assert "role:reviewer" in build_issue["reviewed_by"]
    assert "pruebas ejecutadas o razon de no ejecutarlas" in build_issue["evidence_required"]
    assert "scope creep" in build_issue["risk_checks"]
    assert "Propuesta inicial del Lead" in comment["body"]


def test_builtin_lead_creates_team_and_child_issues_after_acceptance(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    executor = RunExecutor(db_path, build_default_registry())

    first = _dispatch_lead(db_path)
    executor.execute(first)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        interaction = conn.execute("SELECT * FROM issue_thread_interactions").fetchone()

    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept", resolved_by_user_id="user")
    second = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(second)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        agents = {row[0] for row in conn.execute("SELECT id FROM agents")}
        issues = {row[0] for row in conn.execute("SELECT id FROM issues")}
        parent_status = conn.execute("SELECT status FROM issues WHERE id = ?", ("issue:intake",)).fetchone()[0]
        build_issue = conn.execute(
            "SELECT metadata_json FROM issues WHERE id = ?",
            ("issue:intake:build",),
        ).fetchone()
        build_wakeup = conn.execute(
            """
            SELECT payload_json FROM wakeup_requests
            WHERE source = 'assignment' AND payload_json LIKE '%issue:intake:build%'
            """,
        ).fetchone()
        assignment_wakeups = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE source = 'assignment'"
        ).fetchone()[0]

    # QA removed from full_team default — Reviewer absorbs static QA
    assert {"role:engineer", "role:reviewer"} <= agents
    assert "role:qa" not in agents
    assert {
        "issue:intake:plan",
        "issue:intake:build",
        "issue:intake:review",
    } <= issues
    assert "issue:intake:qa" not in issues
    assert parent_status == "in_progress"
    # plan → lead, build → engineer, review → reviewer = 3 assignment wakeups
    assert assignment_wakeups == 3
    issue_metadata = json.loads(build_issue["metadata_json"])
    assert issue_metadata["delegation_type"] == "well_scoped_code_change"
    assert issue_metadata["cost_tier"] == "standard_worker"
    assert issue_metadata["report_to"] == "role:lead"
    assert issue_metadata["reviewed_by"] == "role:reviewer"
    assert "resumen de cambios" in issue_metadata["evidence_required"]
    wake_payload = json.loads(build_wakeup["payload_json"])
    assert wake_payload["delegation_type"] == "well_scoped_code_change"
    assert wake_payload["cost_tier"] == "standard_worker"
    assert wake_payload["reviewed_by"] == "role:reviewer"


def test_builtin_lead_uses_parent_issue_id_for_new_task_children(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, description, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("issue:second", "goal-1", "Second task", "A second user task", "todo", "lead", "role:lead"),
        )
        conn.commit()

    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="manual",
        reason="new_task",
        payload={"issue_id": "issue:second", "wake_reason": "new_task"},
    )
    first = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    RunExecutor(db_path, build_default_registry()).execute(first)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        interaction = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE issue_id = ?",
            ("issue:second",),
        ).fetchone()
    payload = json.loads(interaction["payload_json"])
    # QA removed from full_team default — plan + build + review only
    assert [item["id"] for item in payload["suggested_issues"]] == [
        "issue:second:plan",
        "issue:second:build",
        "issue:second:review",
    ]

    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept", resolved_by_user_id="user")
    second = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    RunExecutor(db_path, build_default_registry()).execute(second)

    with sqlite3.connect(str(db_path)) as conn:
        issues = {row[0] for row in conn.execute("SELECT id FROM issues")}
    assert {
        "issue:second:plan",
        "issue:second:build",
        "issue:second:review",
    } <= issues
    assert "issue:second:qa" not in issues


def test_builtin_roles_write_first_delegation_result(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    executor = RunExecutor(db_path, build_default_registry())

    first = _dispatch_lead(db_path)
    executor.execute(first)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        interaction = conn.execute("SELECT * FROM issue_thread_interactions").fetchone()
    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept", resolved_by_user_id="user")
    accepted = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(accepted)

    # Settle the plan and review siblings so the sibling-completion gate allows
    # the Lead to be woken when the engineer finishes.  Without this, the gate
    # correctly suppresses the supervisor wakeup (siblings still todo).
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE issues SET status = 'done' WHERE id IN ('issue:intake:plan', 'issue:intake:review')"
        )
        conn.commit()

    engineer = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:engineer")
    executor.execute(engineer)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        comment = conn.execute(
            "SELECT * FROM issue_comments WHERE issue_id = ? ORDER BY created_at DESC, rowid DESC",
            ("issue:intake:build",),
        ).fetchone()
        issue = conn.execute("SELECT status FROM issues WHERE id = ?", ("issue:intake:build",)).fetchone()

    assert "Engineer intake" in comment["body"]
    assert issue["status"] == "done"
    with sqlite3.connect(str(db_path)) as conn:
        supervisor_wakeup = conn.execute(
            """
            SELECT * FROM wakeup_requests
            WHERE source = 'delegation' AND reason = 'child_report'
            """
        ).fetchone()
    assert supervisor_wakeup is not None


def test_child_reports_to_same_lead_are_coalesced(tmp_path: Path) -> None:
    """Lead is woken exactly once when parallel engineer + reviewer finish.

    With the sibling-completion gate, the engineer's notify_supervisor is
    suppressed while the reviewer is still active (and vice-versa).  Only the
    last settling child fires the Lead wakeup, so the result is 1 wakeup total
    (not 2 that were coalesced, and not 0 because of the gate blocking both).
    The plan sibling is cancelled upfront so it doesn't hold the gate open.
    """
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    executor = RunExecutor(db_path, build_default_registry())

    first = _dispatch_lead(db_path)
    executor.execute(first)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        interaction = conn.execute("SELECT * FROM issue_thread_interactions").fetchone()
    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept", resolved_by_user_id="user")
    accepted = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(accepted)

    # Cancel the plan sibling so it doesn't hold the sibling-completion gate open
    # while we run only engineer and reviewer in this test.
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE issues SET status = 'cancelled' WHERE id = 'issue:intake:plan'")
        conn.commit()

    # Run engineer then reviewer.  With the gate:
    # - engineer finishes: reviewer still todo → gate suppresses (0 wakeups)
    # - reviewer finishes: no more active siblings → gate allows (1 wakeup created)
    scheduler = HeartbeatScheduler(db_path)
    for agent_id in ("role:engineer", "role:reviewer"):
        dispatch = scheduler.dispatch_next(agent_id=agent_id)
        assert dispatch is not None
        executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        wakeups = conn.execute(
            """
            SELECT *
            FROM wakeup_requests
            WHERE source = 'delegation'
              AND reason = 'child_report'
              AND agent_id = 'role:lead'
            """
        ).fetchall()

    # Exactly ONE wakeup for the Lead — not two (gate suppressed the first;
    # second fires when no active siblings remain).
    assert len(wakeups) == 1
    assert wakeups[0]["status"] == "queued"


# ── Sibling-completion gate tests ────────────────────────────────────────────


def _init_sibling_db(db_path: Path) -> None:
    """Lead with two parallel engineer children under issue:intake."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Lead", "lead", "lead_builtin"),
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:engineer", "engineer", "Engineer", "standard", "manual"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("issue:intake", "goal-1", "Build", "in_progress", "lead", "role:lead"),
        )
        # Two parallel children
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("issue:child-a", "goal-1", "issue:intake", "Child A", "in_progress", "engineer", "role:engineer"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("issue:child-b", "goal-1", "issue:intake", "Child B", "in_progress", "engineer", "role:engineer"),
        )
        conn.commit()


class _DoneRuntime:
    """Engineer adapter that simply returns done with a comment."""

    descriptor = AdapterDescriptor(adapter_type="manual", channel="builtin")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="Done.",
            actions={"issue_status": "done", "notify_supervisor": True},
        )


class _BlockedRuntime:
    """Engineer adapter that declares itself blocked."""

    descriptor = AdapterDescriptor(adapter_type="manual", channel="builtin")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="Blocked.",
            actions={"issue_status": "blocked", "notify_supervisor": True},
        )


def _count_lead_child_report_wakeups(db_path: Path) -> list[dict]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM wakeup_requests
            WHERE agent_id = 'role:lead'
              AND reason = 'child_report'
              AND status = 'queued'
            """
        ).fetchall()
    return [dict(r) for r in rows]


def test_supervisor_not_woken_while_sibling_still_active(tmp_path: Path) -> None:
    """When child-A finishes but child-B is still in_progress, the Lead must NOT be woken."""
    db_path = tmp_path / "aiteam.db"
    _init_sibling_db(db_path)
    registry = AdapterRegistry([_DoneRuntime()])
    executor = RunExecutor(db_path, registry)

    # Dispatch and run only child-A
    enqueue_wakeup(db_path, agent_id="role:engineer", source="test", reason="assignment", payload={"issue_id": "issue:child-a"})
    dispatch_a = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:engineer")
    assert dispatch_a is not None
    executor.execute(dispatch_a)

    # child-B is still in_progress → Lead must NOT be woken
    wakeups = _count_lead_child_report_wakeups(db_path)
    assert wakeups == [], f"Lead should not be woken while child-B is still active, got: {wakeups}"


def test_supervisor_woken_when_last_sibling_finishes(tmp_path: Path) -> None:
    """When all siblings are done (or settled), the Lead MUST be woken exactly once."""
    db_path = tmp_path / "aiteam.db"
    _init_sibling_db(db_path)
    registry = AdapterRegistry([_DoneRuntime()])
    executor = RunExecutor(db_path, registry)

    # Run child-A (sibling B still in_progress → Lead suppressed)
    enqueue_wakeup(db_path, agent_id="role:engineer", source="test", reason="assignment", payload={"issue_id": "issue:child-a"})
    dispatch_a = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:engineer")
    executor.execute(dispatch_a)
    assert _count_lead_child_report_wakeups(db_path) == []

    # Run child-B (no more active siblings → Lead woken)
    enqueue_wakeup(db_path, agent_id="role:engineer", source="test", reason="assignment", payload={"issue_id": "issue:child-b"})
    dispatch_b = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:engineer")
    executor.execute(dispatch_b)

    wakeups = _count_lead_child_report_wakeups(db_path)
    assert len(wakeups) == 1, f"Lead should be woken once after last sibling finishes, got: {wakeups}"


def test_supervisor_woken_immediately_for_blocked_child(tmp_path: Path) -> None:
    """A blocked child must always wake the supervisor immediately, even with active siblings."""
    db_path = tmp_path / "aiteam.db"
    _init_sibling_db(db_path)
    registry = AdapterRegistry([_BlockedRuntime()])
    executor = RunExecutor(db_path, registry)

    # child-A blocks (child-B still in_progress) → Lead MUST be woken immediately
    enqueue_wakeup(db_path, agent_id="role:engineer", source="test", reason="assignment", payload={"issue_id": "issue:child-a"})
    dispatch_a = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:engineer")
    executor.execute(dispatch_a)

    wakeups = _count_lead_child_report_wakeups(db_path)
    assert len(wakeups) == 1, f"Lead should be woken immediately for blocked child, got: {wakeups}"


def test_supervisor_woken_for_sole_child_completion(tmp_path: Path) -> None:
    """A single child completing (no siblings) must always wake the supervisor."""
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Lead", "lead", "lead_builtin"),
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:engineer", "engineer", "Engineer", "standard", "manual"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("issue:intake", "goal-1", "Build", "in_progress", "lead", "role:lead"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("issue:child-a", "goal-1", "issue:intake", "Only child", "in_progress", "engineer", "role:engineer"),
        )
        conn.commit()

    registry = AdapterRegistry([_DoneRuntime()])
    executor = RunExecutor(db_path, registry)
    enqueue_wakeup(db_path, agent_id="role:engineer", source="test", reason="assignment", payload={"issue_id": "issue:child-a"})
    dispatch_a = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:engineer")
    executor.execute(dispatch_a)

    wakeups = _count_lead_child_report_wakeups(db_path)
    assert len(wakeups) == 1, f"Sole child completion must wake Lead, got: {wakeups}"


def test_full_team_lead_delegation_adds_review_guardrail_when_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Game"))
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES (?, ?, ?, ?)",
            ("role:lead", "lead", "Team Lead", "openai_api"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, description, status, role, assignee_agent_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "issue:intake",
                "goal-1",
                "Build a game",
                "Build a game with a hired team",
                "todo",
                "lead",
                "role:lead",
                json.dumps({"profile": "full_team"}),
            ),
        )
        conn.commit()

    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="manual",
        reason="manual",
        payload={"issue_id": "issue:intake"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor = RunExecutor(db_path, AdapterRegistry([_LeadCreateIssuesWithoutReviewerRuntime()]))
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        issues = conn.execute(
            """
            SELECT role, metadata_json, assignee_agent_id
            FROM issues
            WHERE parent_id = ?
            ORDER BY role ASC
            """,
            ("issue:intake",),
        ).fetchall()
        reviewer_wakeup = conn.execute(
            """
            SELECT *
            FROM wakeup_requests
            WHERE agent_id = 'role:reviewer'
              AND reason = 'new_issue'
            """
        ).fetchone()

    roles = {row["role"] for row in issues}
    guardrail_issue = next(row for row in issues if row["role"] == "reviewer")
    assert {"engineer", "test_runner", "reviewer"} <= roles
    assert json.loads(guardrail_issue["metadata_json"])["source"] == "full_team_review_guardrail"
    assert guardrail_issue["assignee_agent_id"] == "role:reviewer"
    assert reviewer_wakeup is not None


def test_api_engineer_implementation_claim_without_workspace_changes_is_plan_only(tmp_path: Path) -> None:
    """API-only engineer with useful output but no workspace changes → plan_only + continuation.

    Previously this immediately blocked.  Now that all adapters can use write_file ops,
    the agent gets a continuation pass to try file ops before being escalated to blocked.
    """
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE agents SET adapter_type = ?, capabilities_json = ? WHERE id = ?",
            ("openai_api", json.dumps(["repo_read", "repo_write"]), "agent-1"),
        )
        conn.commit()

    executor = RunExecutor(db_path, AdapterRegistry([_OpenAIImplementationClaimRuntime()]))
    dispatch = _dispatch_one(db_path)
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        issue = conn.execute("SELECT status FROM issues WHERE id = ?", ("issue-1",)).fetchone()
        run = conn.execute(
            "SELECT status, liveness_state, liveness_reason FROM runs WHERE id = ?",
            (dispatch.run["id"],),
        ).fetchone()
        continuation = conn.execute(
            """
            SELECT *
            FROM wakeup_requests
            WHERE agent_id = ? AND reason = 'liveness_continuation'
            """,
            ("agent-1",),
        ).fetchone()

    # Issue should NOT be blocked — agent gets a continuation pass
    assert issue["status"] != "blocked"
    assert run["status"] == "completed"
    assert run["liveness_state"] == "plan_only"
    assert "output_without_workspace_changes" in run["liveness_reason"]
    # A continuation wakeup must be enqueued so the agent can try write_file ops
    assert continuation is not None


def test_api_engineer_delivery_without_claim_verbs_without_workspace_changes_is_plan_only(tmp_path: Path) -> None:
    """API-only engineer with output text (no verbs) and no workspace changes → plan_only."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE agents SET adapter_type = ?, capabilities_json = ? WHERE id = ?",
            ("openai_api", json.dumps(["repo_read", "repo_write"]), "agent-1"),
        )
        conn.commit()

    executor = RunExecutor(db_path, AdapterRegistry([_OpenAIDeliveryWithoutVerbRuntime()]))
    dispatch = _dispatch_one(db_path)
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        issue = conn.execute("SELECT status FROM issues WHERE id = ?", ("issue-1",)).fetchone()
        run = conn.execute(
            "SELECT liveness_state, liveness_reason FROM runs WHERE id = ?",
            (dispatch.run["id"],),
        ).fetchone()
        continuation = conn.execute(
            "SELECT * FROM wakeup_requests WHERE agent_id = ? AND reason = 'liveness_continuation'",
            ("agent-1",),
        ).fetchone()

    assert issue["status"] != "blocked"
    assert run["liveness_state"] == "plan_only"
    assert "output_without_workspace_changes" in run["liveness_reason"]
    assert continuation is not None


def test_api_engineer_write_file_ops_produce_workspace_evidence_and_advanced(tmp_path: Path) -> None:
    """API-only engineer using write_file ops → files materialised → advanced."""
    from aiteam.adapters.registry import AdapterDescriptor, ExecutionResult

    class _WriteFileOpsRuntime:
        descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(
                status="completed",
                output="Implemented feature via write_file ops.",
                actions={
                    "file_ops": [
                        {"op": "write_file", "path": "src/main.py", "body": "print('hello')"},
                        {"op": "write_file", "path": "README.md", "body": "# Project"},
                    ],
                    "set_status": "done",
                },
            )

    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE agents SET adapter_type = ? WHERE id = ?",
            ("openai_api", "agent-1"),
        )
        conn.commit()

    executor = RunExecutor(db_path, AdapterRegistry([_WriteFileOpsRuntime()]))
    dispatch = _dispatch_one(db_path)
    executor.execute(dispatch)

    # Verify files were materialised on disk
    assert (db_path.parent / "src" / "main.py").exists()
    assert (db_path.parent / "README.md").read_text() == "# Project"

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute(
            "SELECT liveness_state, liveness_reason FROM runs WHERE id = ?",
            (dispatch.run["id"],),
        ).fetchone()
        file_ops_event = conn.execute(
            "SELECT payload_json FROM run_events WHERE run_id = ? AND event_type = 'file_ops'",
            (dispatch.run["id"],),
        ).fetchone()

    assert run["liveness_state"] == "advanced"
    assert file_ops_event is not None
    file_ops_payload = json.loads(file_ops_event["payload_json"])
    assert file_ops_payload["count"] == 2


def test_liveness_continuation_blocks_after_max_attempts_without_workspace_changes(tmp_path: Path) -> None:
    """After MAX_CONTINUATION_ATTEMPTS plan_only runs, the issue should be blocked."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    # Simulate a run that has already exhausted 2 continuation attempts (new max = 2)
    enqueue_wakeup(
        db_path,
        agent_id="agent-1",
        source="automation",
        reason="liveness_continuation",
        trigger_detail="source_run:run:previous:plan_only",
        payload={
            "issue_id": "issue-1",
            "wake_reason": "liveness_continuation",
            "source_run_id": "run:previous",
            "liveness_state": "plan_only",
            "liveness_reason": "output_without_workspace_changes",
            "continuation_attempt": 2,  # at max → next run must block
            "max_continuation_attempts": 2,
            "instruction": "Create files or block.",
        },
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="agent-1")

    RunExecutor(db_path, AdapterRegistry([_AmbiguousEvidenceRuntime()])).execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        issue = conn.execute("SELECT status FROM issues WHERE id = ?", ("issue-1",)).fetchone()
        run = conn.execute("SELECT liveness_state FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()
        evidence = json.loads(
            conn.execute(
                "SELECT payload_json FROM run_events WHERE run_id = ? AND event_type = 'workspace_evidence'",
                (dispatch.run["id"],),
            ).fetchone()["payload_json"]
        )
        comments = [
            row["body"]
            for row in conn.execute(
                "SELECT body FROM issue_comments WHERE issue_id = ? ORDER BY created_at ASC, rowid ASC",
                ("issue-1",),
            )
        ]

    assert issue["status"] == "blocked"
    assert run["liveness_state"] == "blocked"
    assert evidence["changed"] is False
    assert any("Bloqueado" in body for body in comments)


def test_engineer_workspace_changes_auto_complete_and_notify_supervisor(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO agents (id, role, name, adapter_type, supervisor_agent_id, capabilities_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("role:engineer", "engineer", "Engineer", "subscription_cli", "role:lead", json.dumps(["repo_write"])),
        )
        conn.execute(
            """
            INSERT INTO issues (id, parent_id, goal_id, title, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("issue:build", "issue:intake", "goal-1", "Build prototype", "todo", "engineer", "role:engineer"),
        )
        conn.commit()
    enqueue_wakeup(
        db_path,
        agent_id="role:engineer",
        source="assignment",
        reason="new_issue",
        payload={"issue_id": "issue:build", "wake_reason": "new_issue"},
    )

    executor = RunExecutor(db_path, AdapterRegistry([_WritingImplementationRuntime(db_path)]))
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:engineer")
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        issue = conn.execute("SELECT status FROM issues WHERE id = ?", ("issue:build",)).fetchone()
        run = conn.execute("SELECT liveness_state FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()
        evidence = json.loads(
            conn.execute(
                "SELECT payload_json FROM run_events WHERE run_id = ? AND event_type = 'workspace_evidence'",
                (dispatch.run["id"],),
            ).fetchone()["payload_json"]
        )
        report = conn.execute(
            """
            SELECT *
            FROM wakeup_requests
            WHERE agent_id = 'role:lead'
              AND reason = 'child_report'
            """
        ).fetchone()

    assert issue["status"] == "done"
    assert run["liveness_state"] == "advanced"
    assert evidence["changed"] is True
    assert "cartografo-ecos/index.html" in evidence["delta"]["created"]
    assert report is not None


def test_blocked_dependency_wakeup_is_skipped_until_blocker_done(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES (?, ?, ?, ?)",
            ("role:engineer", "engineer", "Engineer", "role_builtin"),
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES (?, ?, ?, ?)",
            ("role:test_runner", "test_runner", "Test Runner", "role_builtin"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, parent_id, goal_id, title, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("issue:build", "issue:intake", "goal-1", "Build prototype", "todo", "engineer", "role:engineer"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, parent_id, goal_id, title, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("issue:qa", "issue:intake", "goal-1", "Run tests", "todo", "test_runner", "role:test_runner"),
        )
        conn.execute(
            "INSERT INTO issue_dependencies (issue_id, depends_on_issue_id) VALUES (?, ?)",
            ("issue:qa", "issue:build"),
        )
        conn.commit()
    enqueue_wakeup(
        db_path,
        agent_id="role:test_runner",
        source="assignment",
        reason="new_issue",
        payload={"issue_id": "issue:qa"},
        wakeup_id="wake:001-test_runner",
    )
    enqueue_wakeup(
        db_path,
        agent_id="role:engineer",
        source="assignment",
        reason="new_issue",
        payload={"issue_id": "issue:build"},
        wakeup_id="wake:002-engineer",
    )

    scheduler = HeartbeatScheduler(db_path)
    dispatch = scheduler.dispatch_next()

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        skipped = conn.execute(
            "SELECT status, error FROM wakeup_requests WHERE agent_id = 'role:test_runner'"
        ).fetchone()

    assert dispatch is not None
    assert dispatch.run["agent_id"] == "role:engineer"
    assert skipped["status"] == "skipped"
    assert skipped["error"] == "issue_dependencies_blocked"

def test_lead_summarizes_child_reports_and_requests_light_review(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    executor = RunExecutor(db_path, build_default_registry())

    first = _dispatch_lead(db_path)
    executor.execute(first)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        interaction = conn.execute("SELECT * FROM issue_thread_interactions").fetchone()
    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept", resolved_by_user_id="user")
    accepted = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(accepted)

    scheduler = HeartbeatScheduler(db_path)
    while True:
        dispatch = scheduler.dispatch_next()
        if dispatch is None:
            break
        executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        summary = conn.execute(
            """
            SELECT * FROM issue_comments
            WHERE issue_id = ? AND author_agent_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            ("issue:intake", "role:lead"),
        ).fetchone()
        review = conn.execute(
            """
            SELECT * FROM issue_thread_interactions
            WHERE idempotency_key = ?
            """,
            ("lead:cycle-review:issue:intake",),
        ).fetchone()

    assert "Resumen del Lead" in summary["body"]
    assert "primera ronda del equipo esta completa" in summary["body"]
    assert review["kind"] == "request_confirmation"
    assert review["status"] == "pending"


def test_lead_closes_parent_after_initial_cycle_confirmation(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    executor = RunExecutor(db_path, build_default_registry())

    first = _dispatch_lead(db_path)
    executor.execute(first)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        proposal = conn.execute("SELECT * FROM issue_thread_interactions").fetchone()
    resolve_interaction(db_path, interaction_id=proposal["id"], action="accept", resolved_by_user_id="user")

    scheduler = HeartbeatScheduler(db_path)
    while True:
        dispatch = scheduler.dispatch_next()
        if dispatch is None:
            break
        executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        review = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE idempotency_key = ?",
            ("lead:cycle-review:issue:intake",),
        ).fetchone()
    resolve_interaction(db_path, interaction_id=review["id"], action="accept", resolved_by_user_id="user")

    close_dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(close_dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        parent = conn.execute("SELECT status FROM issues WHERE id = ?", ("issue:intake",)).fetchone()
        comment = conn.execute(
            """
            SELECT * FROM issue_comments
            WHERE issue_id = ? AND author_agent_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            ("issue:intake", "role:lead"),
        ).fetchone()

    assert parent["status"] == "done"
    assert "Ciclo inicial cerrado" in comment["body"]


def test_lead_recovers_parent_closed_state_after_accepted_cycle_review(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    executor = RunExecutor(db_path, build_default_registry())

    first = _dispatch_lead(db_path)
    executor.execute(first)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        proposal = conn.execute("SELECT * FROM issue_thread_interactions").fetchone()
    resolve_interaction(db_path, interaction_id=proposal["id"], action="accept", resolved_by_user_id="user")

    scheduler = HeartbeatScheduler(db_path)
    while True:
        dispatch = scheduler.dispatch_next()
        if dispatch is None:
            break
        executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        review = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE idempotency_key = ?",
            ("lead:cycle-review:issue:intake",),
        ).fetchone()
    resolve_interaction(db_path, interaction_id=review["id"], action="accept", resolved_by_user_id="user")
    # Simulate an older dirty project where the accepted review wakeup completed
    # before the parent issue was marked done.
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE issues SET status = 'in_progress' WHERE id = ?", ("issue:intake",))
        conn.execute("DELETE FROM wakeup_requests WHERE reason = 'interaction_resolved'")
        conn.commit()

    recovery = _dispatch_lead(db_path)
    executor.execute(recovery)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        parent = conn.execute("SELECT status FROM issues WHERE id = ?", ("issue:intake",)).fetchone()
        comment = conn.execute(
            """
            SELECT * FROM issue_comments
            WHERE issue_id = ? AND author_agent_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            ("issue:intake", "role:lead"),
        ).fetchone()

    assert parent["status"] == "done"
    assert "Ciclo recuperado" in comment["body"]


def test_lead_manual_wake_without_pending_work_is_skipped(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    executor = RunExecutor(db_path, build_default_registry())

    first = _dispatch_lead(db_path)
    executor.execute(first)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        proposal = conn.execute("SELECT * FROM issue_thread_interactions").fetchone()
    resolve_interaction(db_path, interaction_id=proposal["id"], action="accept", resolved_by_user_id="user")

    scheduler = HeartbeatScheduler(db_path)
    while True:
        dispatch = scheduler.dispatch_next()
        if dispatch is None:
            break
        executor.execute(dispatch)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        review = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE idempotency_key = ?",
            ("lead:cycle-review:issue:intake",),
        ).fetchone()
    resolve_interaction(db_path, interaction_id=review["id"], action="accept", resolved_by_user_id="user")
    close_dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(close_dispatch)

    noop = _dispatch_lead(db_path)
    executor.execute(noop)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (noop.run["id"],)).fetchone()
        comment_count = conn.execute(
            "SELECT COUNT(*) FROM issue_comments WHERE source_run_id = ?",
            (noop.run["id"],),
        ).fetchone()[0]

    assert run["status"] == "skipped"
    assert run["error"] == "no_pending_lead_work"
    assert comment_count == 0


def test_lead_keeps_parent_open_after_initial_cycle_rejection(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    executor = RunExecutor(db_path, build_default_registry())

    first = _dispatch_lead(db_path)
    executor.execute(first)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        proposal = conn.execute("SELECT * FROM issue_thread_interactions").fetchone()
    resolve_interaction(db_path, interaction_id=proposal["id"], action="accept", resolved_by_user_id="user")

    scheduler = HeartbeatScheduler(db_path)
    while True:
        dispatch = scheduler.dispatch_next()
        if dispatch is None:
            break
        executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        review = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE idempotency_key = ?",
            ("lead:cycle-review:issue:intake",),
        ).fetchone()
    resolve_interaction(db_path, interaction_id=review["id"], action="reject", resolved_by_user_id="user")

    reject_dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(reject_dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        parent = conn.execute("SELECT status FROM issues WHERE id = ?", ("issue:intake",)).fetchone()
        comment = conn.execute(
            """
            SELECT * FROM issue_comments
            WHERE issue_id = ? AND author_agent_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            ("issue:intake", "role:lead"),
        ).fetchone()

    assert parent["status"] == "in_progress"
    assert "mantenido abierto" in comment["body"]


def test_executor_fails_run_on_adapter_error(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    registry = AdapterRegistry([_FailRuntime()])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()
        wakeup = conn.execute(
            "SELECT * FROM wakeup_requests WHERE id = ?",
            (dispatch.wakeup_request["id"],),
        ).fetchone()

    assert run["status"] == "failed"
    assert run["error"] == "adapter error"
    assert run["error_code"] == "agent_reported_failure"
    assert run["exit_code"] == 1
    assert wakeup["status"] == "failed"


def test_executor_falls_back_to_manual_when_adapter_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    # registry only has 'manual' — agent has adapter_type='subscription_cli' but it's missing
    manual = StaticAdapterRuntime(
        AdapterDescriptor(adapter_type="manual", channel="manual", provider="human", cost_tier=0)
    )
    registry = AdapterRegistry([manual])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()
        tool_access = conn.execute(
            "SELECT tool_name, decision FROM tool_access WHERE run_id = ? ORDER BY created_at ASC, rowid ASC",
            (dispatch.run["id"],),
        ).fetchall()

    assert run["status"] == "skipped"
    assert [(row["tool_name"], row["decision"]) for row in tool_access] == [
        ("adapter:subscription_cli", "denied"),
        ("adapter:manual", "allowed"),
    ]


def test_executor_handles_adapter_exception(tmp_path: Path) -> None:
    """If adapter.execute() throws, run is marked failed without crashing executor."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    class _BrokenRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            raise RuntimeError("adapter crashed")

    registry = AdapterRegistry([_BrokenRuntime()])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)  # must not raise

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()

    assert run["status"] == "failed"
    assert "adapter crashed" in run["error"]


def test_subprocess_adapter_runs_echo(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")
    import sys
    runtime = SubprocessAdapterRuntime(
        descriptor=descriptor,
        command=[sys.executable, "-c", "print('hello from subprocess')"],
    )
    registry = AdapterRegistry([runtime])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()
        events = conn.execute(
            "SELECT * FROM run_events WHERE run_id = ?",
            (dispatch.run["id"],),
        ).fetchall()

    assert run["status"] == "completed"
    assert run["exit_code"] == 0
    assert any("hello from subprocess" in json.loads(e["payload_json"]).get("text", "") for e in events)


def test_subprocess_adapter_fails_on_nonzero_exit(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")
    import sys
    runtime = SubprocessAdapterRuntime(
        descriptor=descriptor,
        command=[sys.executable, "-c", "import sys; sys.exit(2)"],
    )
    registry = AdapterRegistry([runtime])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()

    assert run["status"] == "failed"
    assert run["exit_code"] == 2


def test_subscription_cli_adapter_parses_structured_submit_work(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    fake_cli = tmp_path / "fake_claude.py"
    fake_cli.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import json",
                f"Path({str(tmp_path / 'structured-output.txt')!r}).write_text('done\\n', encoding='utf-8')",
                "work = {",
                "    'ops': [",
                "        {'type': 'add_comment', 'body': 'structured comment'},",
                "        {'type': 'set_status', 'status': 'done'},",
                "    ],",
                "    'status': 'completed',",
                "    'summary': 'cli structured summary',",
                "}",
                "print(json.dumps({'result': json.dumps(work), 'usage': {'input_tokens': 7, 'output_tokens': 5}}))",
            ]
        ),
        encoding="utf-8",
    )

    import sys
    runtime = ClaudeSubscriptionCliRuntime(
        descriptor=AdapterDescriptor(adapter_type="subscription_cli", channel="subscription"),
        command=[sys.executable, str(fake_cli)],
    )
    registry = AdapterRegistry([runtime])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()
        issue = conn.execute("SELECT status FROM issues WHERE id = ?", ("issue-1",)).fetchone()
        comments = conn.execute(
            """
            SELECT body FROM issue_comments
            WHERE issue_id = ?
            ORDER BY created_at ASC, rowid ASC
            """,
            ("issue-1",),
        ).fetchall()

    assert run["status"] == "completed"
    assert json.loads(run["usage_json"]) == {"input_tokens": 7, "output_tokens": 5}
    assert issue["status"] == "done"
    assert [row["body"] for row in comments] == ["cli structured summary", "structured comment"]


def test_llm_lead_created_issues_get_agents_and_wakeups(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET adapter_type = ? WHERE id = ?", ("openai_api", "role:lead"))
        conn.commit()

    executor = RunExecutor(db_path, AdapterRegistry([_LeadCreateIssuesRuntime()]))
    dispatch = _dispatch_lead(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        agents = {
            row["id"]: dict(row)
            for row in conn.execute(
                "SELECT id, role, adapter_type, supervisor_agent_id, capabilities_json FROM agents ORDER BY id"
            )
        }
        issues = [
            dict(row)
            for row in conn.execute(
                "SELECT title, role, assignee_agent_id, parent_id FROM issues WHERE parent_id = ? ORDER BY created_at ASC",
                ("issue:intake",),
            )
        ]
        wakeups = [
            dict(row)
            for row in conn.execute(
                "SELECT agent_id, source, reason, payload_json FROM wakeup_requests WHERE source = 'assignment'"
            )
        ]

    assert "role:engineer" in agents
    assert agents["role:engineer"]["adapter_type"] == "role_builtin"
    assert agents["role:engineer"]["supervisor_agent_id"] == "role:lead"
    assert "repo_read" in json.loads(agents["role:engineer"]["capabilities_json"])
    assert "role:reviewer" in agents
    # test_designer: guardrail de suite independiente (P1, 2026-07-15) — se
    # materializa siempre que el Lead delega engineering.
    assert {row["assignee_agent_id"] for row in issues} == {"role:engineer", "role:reviewer", "role:test_designer"}
    assert {row["agent_id"] for row in wakeups} == {"role:engineer", "role:reviewer", "role:test_designer"}


def test_llm_lead_created_issue_agents_use_project_adapter_policy(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    (tmp_path / "project_config.json").write_text(
        json.dumps({"version": 1, "adapter_profile_ids": ["openai_api"]}),
        encoding="utf-8",
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET adapter_type = ? WHERE id = ?", ("openai_api", "role:lead"))
        conn.commit()

    executor = RunExecutor(db_path, AdapterRegistry([_LeadCreateIssuesRuntime()]))
    dispatch = _dispatch_lead(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        engineer = conn.execute(
            "SELECT adapter_type, adapter_config_json FROM agents WHERE id = 'role:engineer'"
        ).fetchone()

    assert engineer["adapter_type"] == "openai_api"
    config = json.loads(engineer["adapter_config_json"])
    assert config["profile_id"] == "openai_api"
    assert config["model"] == "gpt-5.6-terra"


def test_executor_repairs_existing_builtin_agent_before_execution(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    (tmp_path / "project_config.json").write_text(
        json.dumps({"version": 1, "adapter_profile_ids": ["openai_api"]}),
        encoding="utf-8",
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET adapter_type = ?, capabilities_json = ? WHERE id = ?", ("role_builtin", "[]", "agent-1"))
        conn.commit()

    executor = RunExecutor(db_path, AdapterRegistry([_OpenAIOkRuntime()]))
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        agent = conn.execute("SELECT adapter_type, adapter_config_json, capabilities_json FROM agents WHERE id = ?", ("agent-1",)).fetchone()
        run = conn.execute("SELECT status, adapter_type, model, channel FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()

    assert agent["adapter_type"] == "openai_api"
    assert json.loads(agent["adapter_config_json"])["model"] == "gpt-5.6-terra"
    assert "repo_read" in json.loads(agent["capabilities_json"])
    assert run["status"] == "completed"
    assert run["adapter_type"] == "openai_api"
    assert run["model"] == "gpt-5.6-terra"
    assert run["channel"] == "api"


def test_lead_plan_comment_does_not_mutate_plan_document(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET adapter_type = ? WHERE id = ?", ("openai_api", "role:lead"))
        conn.commit()

    executor = RunExecutor(db_path, AdapterRegistry([_LeadPlanCommentRuntime()]))
    dispatch = _dispatch_lead(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        doc = conn.execute("SELECT title, body, metadata_json FROM issue_documents WHERE issue_id = ? AND key = ?", ("issue:intake", "plan")).fetchone()

    assert doc is None


def test_executor_creates_request_confirmation_for_high_criticality_issue(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, criticality="high")

    runtime = _CountingRuntime()
    registry = AdapterRegistry([runtime])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()
        wakeup = conn.execute(
            "SELECT * FROM wakeup_requests WHERE id = ?",
            (dispatch.wakeup_request["id"],),
        ).fetchone()
        interactions = conn.execute(
            """
            SELECT * FROM issue_thread_interactions
            WHERE issue_id = ? AND kind = 'request_confirmation'
            """,
            ("issue-1",),
        ).fetchall()

    assert runtime.calls == 0
    assert run["status"] == "queued"
    assert run["started_at"] is None
    assert wakeup["status"] == "skipped"
    assert wakeup["error"] == "approval_required"
    assert len(interactions) == 1
    assert interactions[0]["status"] == "pending"
    assert interactions[0]["continuation_policy"] == "wake_assignee"
    assert interactions[0]["idempotency_key"] == "compliance:issue-1:criticality"


def test_executor_does_not_duplicate_pending_compliance_interaction(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, criticality="critical")
    existing = create_interaction(
        db_path,
        issue_id="issue-1",
        kind="request_confirmation",
        payload={"reason": "already_pending"},
        idempotency_key="compliance:issue-1:criticality",
    )

    runtime = _CountingRuntime()
    registry = AdapterRegistry([runtime])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM issue_thread_interactions WHERE idempotency_key = ?",
            ("compliance:issue-1:criticality",),
        ).fetchone()[0]
        run = conn.execute("SELECT status FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()

    assert runtime.calls == 0
    assert count == 1
    assert existing["status"] == "pending"
    assert run[0] == "queued"


def test_executor_runs_high_criticality_issue_after_approval(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, criticality="high")
    interaction = create_interaction(
        db_path,
        issue_id="issue-1",
        kind="request_confirmation",
        payload={"reason": "approval"},
        idempotency_key="compliance:issue-1:criticality",
    )
    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept")

    runtime = _CountingRuntime()
    registry = AdapterRegistry([runtime])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        run = conn.execute("SELECT status FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()

    assert runtime.calls == 1
    assert run[0] == "completed"


def test_executor_fails_high_criticality_issue_after_rejection(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, criticality="critical")
    interaction = create_interaction(
        db_path,
        issue_id="issue-1",
        kind="request_confirmation",
        payload={"reason": "approval"},
        idempotency_key="compliance:issue-1:criticality",
    )
    resolve_interaction(db_path, interaction_id=interaction["id"], action="reject")

    runtime = _CountingRuntime()
    registry = AdapterRegistry([runtime])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()
        wakeup = conn.execute(
            "SELECT * FROM wakeup_requests WHERE id = ?",
            (dispatch.wakeup_request["id"],),
        ).fetchone()

    assert runtime.calls == 0
    assert run["status"] == "failed"
    assert run["error_code"] == "approval_rejected"
    assert wakeup["status"] == "failed"
    assert wakeup["error"] == "approval_rejected"


def test_executor_creates_budget_confirmation_when_agent_budget_exceeded(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, budget_monthly_cents=5)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO cost_events (id, agent_id, issue_id, cost_cents, period)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("cost-1", "agent-1", "issue-1", 5, current_period()),
        )
        conn.commit()

    runtime = _CountingRuntime()
    registry = AdapterRegistry([runtime])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()
        wakeup = conn.execute(
            "SELECT * FROM wakeup_requests WHERE id = ?",
            (dispatch.wakeup_request["id"],),
        ).fetchone()
        interaction = conn.execute(
            """
            SELECT * FROM issue_thread_interactions
            WHERE issue_id = ? AND title = ?
            """,
            ("issue-1", "Budget exceeded"),
        ).fetchone()

    assert runtime.calls == 0
    assert run["status"] == "queued"
    assert wakeup["status"] == "skipped"
    assert wakeup["error"] == "budget_approval_required"
    assert interaction["status"] == "pending"
    assert str(interaction["idempotency_key"]).startswith("budget:issue-1:agent-1:")


def test_executor_runs_after_budget_confirmation_is_accepted(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, budget_monthly_cents=5)
    period = current_period()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO cost_events (id, agent_id, issue_id, cost_cents, period) VALUES (?, ?, ?, ?, ?)",
            ("cost-1", "agent-1", "issue-1", 5, period),
        )
        conn.commit()
    interaction = create_interaction(
        db_path,
        issue_id="issue-1",
        kind="request_confirmation",
        payload={"reason": "budget_exceeded"},
        idempotency_key=f"budget:issue-1:agent-1:{period}",
    )
    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept")

    runtime = _CountingRuntime()
    registry = AdapterRegistry([runtime])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        run = conn.execute("SELECT status FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()

    assert runtime.calls == 1
    assert run[0] == "completed"


def test_executor_fails_after_budget_confirmation_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, budget_monthly_cents=5)
    period = current_period()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO cost_events (id, agent_id, issue_id, cost_cents, period) VALUES (?, ?, ?, ?, ?)",
            ("cost-1", "agent-1", "issue-1", 5, period),
        )
        conn.commit()
    interaction = create_interaction(
        db_path,
        issue_id="issue-1",
        kind="request_confirmation",
        payload={"reason": "budget_exceeded"},
        idempotency_key=f"budget:issue-1:agent-1:{period}",
    )
    resolve_interaction(db_path, interaction_id=interaction["id"], action="reject")

    runtime = _CountingRuntime()
    registry = AdapterRegistry([runtime])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()
        wakeup = conn.execute("SELECT * FROM wakeup_requests WHERE id = ?", (dispatch.wakeup_request["id"],)).fetchone()

    assert runtime.calls == 0
    assert run["status"] == "failed"
    assert run["error_code"] == "budget_rejected"
    assert wakeup["status"] == "failed"
    assert wakeup["error"] == "budget_rejected"


def test_lead_manual_wake_skips_when_non_terminal_children_exist(tmp_path: Path) -> None:
    """Guard: builtin lead skips re-proposal if non-terminal children already exist."""
    db_path = tmp_path / "aiteam.db"
    # Set up directly: lead + parent in_progress + children already created (non-terminal)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Game"))
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Team Lead", "lead", "lead_builtin"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("issue:intake", "goal-1", "Build a game", "in_progress", "lead", "role:lead"),
        )
        # Pre-existing non-terminal children (simulating already-delegated state)
        for suffix, role, status in [
            ("build", "engineer", "in_progress"),
            ("review", "reviewer", "todo"),
            ("tests", "test_runner", "todo"),
        ]:
            conn.execute(
                """
                INSERT INTO issues (id, goal_id, parent_id, title, status, role)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (f"issue:intake:{suffix}", "goal-1", "issue:intake",
                 f"{suffix.title()} issue", status, role),
            )
        conn.commit()

    executor = RunExecutor(db_path, build_default_registry())

    with sqlite3.connect(str(db_path)) as conn:
        child_count_before = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE parent_id = 'issue:intake'"
        ).fetchone()[0]

    # Manual wake on intake issue — should skip because non-terminal children exist
    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="manual",
        reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual"},
    )
    manual_dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(manual_dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        child_count_after = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE parent_id = 'issue:intake'"
        ).fetchone()[0]
        run = conn.execute(
            "SELECT status, error FROM runs WHERE id = ?", (manual_dispatch.run["id"],)
        ).fetchone()

    # No new children; run skipped
    assert child_count_after == child_count_before
    assert run["status"] == "skipped"
    assert run["error"] == "no_pending_lead_work"


def test_lead_child_report_blocked_child_escalates_to_user(tmp_path: Path) -> None:
    """Builtin lead creates escalation interaction when a child issue is blocked."""
    db_path = tmp_path / "aiteam.db"
    # Set up DB directly: lead agent + parent issue + one blocked child
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Game"))
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Team Lead", "lead", "lead_builtin"),
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("role:engineer", "engineer", "Engineer", "standard", "openai_api", "role:lead"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("issue:intake", "goal-1", "Build a game", "in_progress", "lead", "role:lead"),
        )
        # Add a blocked child build issue
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("issue:intake:build", "goal-1", "issue:intake",
             "Implement first vertical", "blocked", "engineer", "role:engineer"),
        )
        conn.commit()

    executor = RunExecutor(db_path, build_default_registry())

    # Enqueue a child_report wakeup for the lead (as executor would after blocking engineer run)
    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="delegation",
        reason="child_report",
        payload={
            "issue_id": "issue:intake",
            "child_issue_id": "issue:intake:build",
            "child_issue_status": "blocked",
            "child_liveness_state": "blocked",
            "child_liveness_reason": "api_only_engineer_no_workspace_changes",
            "wake_reason": "child_report",
        },
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        escalation = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE idempotency_key = ?",
            ("lead:blocked-child:issue:intake",),
        ).fetchone()
        comment = conn.execute(
            """
            SELECT body FROM issue_comments
            WHERE issue_id = 'issue:intake' AND author_agent_id = 'role:lead'
            ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
        ).fetchone()

    assert escalation is not None, "Expected escalation interaction to be created for blocked child"
    assert escalation["kind"] == "request_confirmation"
    assert escalation["status"] == "pending"
    escalation_payload = json.loads(escalation["payload_json"])
    assert escalation_payload["reason"] == "child_blocked_requires_action"
    assert any(c["id"] == "issue:intake:build" for c in escalation_payload["blocked_children"])
    assert comment is not None
    assert "bloqueada" in comment["body"].lower() or "bloqueado" in comment["body"].lower()


def test_lead_manual_wake_escalates_when_all_children_are_blocked(tmp_path: Path) -> None:
    """Lead does NOT skip on manual wake when all children are blocked — it escalates instead."""
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Game"))
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Team Lead", "lead", "lead_builtin"),
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("role:engineer", "engineer", "Engineer", "standard", "openai_api", "role:lead"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("issue:intake", "goal-1", "Build a game", "in_progress", "lead", "role:lead"),
        )
        # All children are blocked — should trigger escalation, NOT skip
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("issue:intake:build", "goal-1", "issue:intake",
             "Implement first vertical", "blocked", "engineer", "role:engineer"),
        )
        conn.commit()

    executor = RunExecutor(db_path, build_default_registry())
    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="manual",
        reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute(
            "SELECT status, error FROM runs WHERE id = ?", (dispatch.run["id"],)
        ).fetchone()
        escalation = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE idempotency_key = ?",
            ("lead:blocked-child:issue:intake",),
        ).fetchone()

    # Must NOT skip — must escalate
    assert run["status"] == "completed", f"Expected completed, got skipped with error={run['error']}"
    assert escalation is not None, "Expected escalation interaction when all children blocked"
    assert json.loads(escalation["payload_json"])["reason"] == "child_blocked_requires_action"


# ── update_child_issue tests ──────────────────────────────────────────────────


def _init_lead_child_db(db_path: Path) -> None:
    """Lead issue with one blocked engineer child.

    Lead uses adapter_type='subscription_cli' so the executor routes it through
    runtime.execute() (the else branch) rather than _execute_builtin_lead, which
    is triggered for 'manual' + lead/team_lead.
    """
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Lead", "lead", "subscription_cli", None),
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("role:engineer", "engineer", "Engineer", "standard", "manual", "role:lead"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("issue:intake", "goal-1", "Build", "in_progress", "lead", "role:lead"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("issue:child", "goal-1", "issue:intake", "Implement feature", "blocked", "engineer", "role:engineer"),
        )
        conn.commit()


class _UpdateChildRuntime:
    """Lead adapter that uses update_child_issue to unblock its child."""

    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="Unblocking engineer.",
            actions={
                "update_child_issues": [
                    {
                        "child_issue_id": "issue:child",
                        "status": "todo",
                        "body": "Use Web Audio API instead of WAV files.",
                    }
                ]
            },
        )


def test_update_child_issue_sets_status_and_posts_comment(tmp_path: Path) -> None:
    """update_child_issue op must set child status, post directive comment, and enqueue child wakeup."""
    db_path = tmp_path / "aiteam.db"
    _init_lead_child_db(db_path)

    registry = AdapterRegistry([_UpdateChildRuntime()])
    executor = RunExecutor(db_path, registry)

    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="test",
        reason="child_report",
        payload={"issue_id": "issue:intake"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        child = conn.execute("SELECT * FROM issues WHERE id = 'issue:child'").fetchone()
        comments = conn.execute(
            "SELECT * FROM issue_comments WHERE issue_id = 'issue:child' ORDER BY created_at"
        ).fetchall()
        wakeups = conn.execute(
            "SELECT * FROM wakeup_requests WHERE agent_id = 'role:engineer' AND status = 'queued'"
        ).fetchall()

    assert dict(child)["status"] == "todo", f"Child status should be 'todo', got {dict(child)['status']}"
    assert len(comments) >= 1, "Expected at least one directive comment on child issue"
    assert any("Web Audio API" in (c["body"] or "") for c in comments), "Directive body not found in comments"
    assert len(wakeups) >= 1, "Expected child to be re-queued after update_child_issue"


def test_unchanged_failed_test_runner_is_not_requeued(tmp_path: Path) -> None:
    """A failed deterministic runner must wait for a real workspace change."""
    workspace = tmp_path / "workspace"
    db_path = workspace / ".aiteam" / "aiteam.db"
    db_path.parent.mkdir(parents=True)
    (workspace / "app.py").write_text("print('broken')\n", encoding="utf-8")
    _init_lead_child_db(db_path)

    executor = RunExecutor(db_path, AdapterRegistry([_UpdateChildRuntime()]))
    digest = executor._workspace_digest(workspace)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET role = 'test_runner' WHERE id = 'role:engineer'")
        conn.execute("UPDATE issues SET role = 'test_runner' WHERE id = 'issue:child'")
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status) VALUES (?, ?, ?, ?)",
            ("run:test-failed", "role:engineer", "issue:child", "completed"),
        )
        conn.execute(
            """
            INSERT INTO agent_reports (
                id, issue_id, agent_id, run_id, agent_role, result,
                valid, is_assignee, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 1, '{}')
            """,
            (
                "report:test-failed", "issue:child", "role:engineer",
                "run:test-failed", "test_runner", "failed",
            ),
        )
        conn.execute(
            """
            INSERT INTO activity_log (
                id, run_id, actor_agent_id, action, target_type, target_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "activity:test-digest", "run:test-failed", "role:engineer",
                "workspace.context_digest", "issue", "issue:child",
                json.dumps({"digest": digest}),
            ),
        )
        conn.commit()

    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="test",
        reason="child_report",
        payload={"issue_id": "issue:intake"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        child = conn.execute(
            "SELECT status FROM issues WHERE id = 'issue:child'"
        ).fetchone()
        runner_wakeups = conn.execute(
            """
            SELECT COUNT(*) FROM wakeup_requests
            WHERE agent_id = 'role:engineer' AND reason = 'lead_directive'
            """
        ).fetchone()[0]
        correction_wakeups = conn.execute(
            """
            SELECT COUNT(*) FROM wakeup_requests
            WHERE agent_id = 'role:lead' AND reason = 'unchanged_test_failure'
            """
        ).fetchone()[0]
        suppressed = conn.execute(
            """
            SELECT COUNT(*) FROM activity_log
            WHERE action = 'lead.requeue_suppressed_unchanged_workspace'
            """
        ).fetchone()[0]

    assert child["status"] == "blocked"
    assert runner_wakeups == 0
    assert correction_wakeups == 1
    assert suppressed == 1


def test_failed_test_runner_can_be_requeued_after_workspace_change(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    db_path = workspace / ".aiteam" / "aiteam.db"
    db_path.parent.mkdir(parents=True)
    source = workspace / "app.py"
    source.write_text("print('broken')\n", encoding="utf-8")
    _init_lead_child_db(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([_UpdateChildRuntime()]))
    failed_digest = executor._workspace_digest(workspace)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET role = 'test_runner' WHERE id = 'role:engineer'")
        conn.execute("UPDATE issues SET role = 'test_runner' WHERE id = 'issue:child'")
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status) VALUES ('run:failed', 'role:engineer', 'issue:child', 'completed')"
        )
        conn.execute(
            """
            INSERT INTO agent_reports (
                id, issue_id, agent_id, run_id, agent_role, result,
                valid, is_assignee, raw_json
            ) VALUES ('report:failed', 'issue:child', 'role:engineer', 'run:failed',
                      'test_runner', 'failed', 1, 1, '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO activity_log (
                id, run_id, actor_agent_id, action, target_type, target_id, payload_json
            ) VALUES ('activity:digest', 'run:failed', 'role:engineer',
                      'workspace.context_digest', 'issue', 'issue:child', ?)
            """,
            (json.dumps({"digest": failed_digest}),),
        )
        conn.commit()
    source.write_text("print('fixed')\n", encoding="utf-8")

    enqueue_wakeup(
        db_path, agent_id="role:lead", source="test", reason="child_report",
        payload={"issue_id": "issue:intake"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        status = conn.execute(
            "SELECT status FROM issues WHERE id = 'issue:child'"
        ).fetchone()[0]
        runner_wakeups = conn.execute(
            """
            SELECT COUNT(*) FROM wakeup_requests
            WHERE agent_id = 'role:engineer' AND reason = 'lead_directive'
            """
        ).fetchone()[0]
    assert status == "todo"
    assert runner_wakeups == 1


def test_solo_lead_rejects_every_delegation_and_keeps_one_agent(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_lead_child_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM issues WHERE id = 'issue:child'")
        conn.execute(
            "UPDATE issues SET metadata_json = ? WHERE id = 'issue:intake'",
            (json.dumps({"profile": "solo_lead"}),),
        )
        conn.commit()

    class _OverHiringLeadRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(
                status="completed",
                output="Formando equipo.",
                actions={
                    "create_issues": [
                        {
                            "title": "Implementar",
                            "role": "engineer",
                            "criticality": "low",
                            "complexity": "low",
                            "action_type": "implementation",
                        },
                        {"title": "Revisar", "role": "reviewer"},
                        {"title": "Explorar", "role": "file_scout"},
                    ]
                },
            )

    executor = RunExecutor(db_path, AdapterRegistry([_OverHiringLeadRuntime()]))
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="test", reason="new_project",
        payload={"issue_id": "issue:intake"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="test", reason="child_report",
        payload={"issue_id": "issue:intake"}, idempotency_key="solo:second-lead-run",
    )
    second_dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert second_dispatch is not None
    executor.execute(second_dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        roles = [
            row[0] for row in conn.execute(
                "SELECT role FROM issues WHERE parent_id = 'issue:intake' ORDER BY rowid"
            )
        ]
        constrained = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'profile.delegation_constrained'"
        ).fetchone()[0]
    assert roles == []
    assert constrained == 2


def test_update_child_issue_ignores_non_child(tmp_path: Path) -> None:
    """update_child_issue on an issue that is NOT a child of the current issue must be silently dropped."""
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'G')")
        # Use subscription_cli so the executor routes through runtime.execute() (else branch),
        # not _execute_builtin_lead (which intercepts manual + lead/team_lead).
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES (?, ?, ?, ?)",
            ("role:lead", "lead", "Lead", "subscription_cli"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("issue:intake", "goal-1", "Build", "in_progress", "lead", "role:lead"),
        )
        # A SIBLING issue, not a child
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role) VALUES (?, ?, ?, ?, ?)",
            ("issue:unrelated", "goal-1", "Unrelated", "blocked", "engineer"),
        )
        conn.commit()

    class _BadUpdateRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(
                status="completed",
                output="Trying to update unrelated issue.",
                actions={
                    "update_child_issues": [
                        {"child_issue_id": "issue:unrelated", "status": "todo", "body": "Directive"}
                    ]
                },
            )

    registry = AdapterRegistry([_BadUpdateRuntime()])
    executor = RunExecutor(db_path, registry)

    enqueue_wakeup(
        db_path, agent_id="role:lead", source="test", reason="new_issue", payload={"issue_id": "issue:intake"}
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        unrelated = conn.execute("SELECT status FROM issues WHERE id = 'issue:unrelated'").fetchone()

    assert dict(unrelated)["status"] == "blocked", "Non-child issue must not be modified by update_child_issue"


# ── Circuit breaker tests ─────────────────────────────────────────────────────


def _make_circuit_breaker_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "G"))
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Lead", "lead", "subscription_cli", None),
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("role:engineer", "engineer", "Engineer", "standard", "manual", "role:lead"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("issue:intake", "goal-1", "Build", "in_progress", "lead", "role:lead"),
        )
        conn.execute(
            "INSERT INTO issues"
            " (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("issue:child", "goal-1", "issue:intake", "Feature", "blocked", "engineer", "role:engineer"),
        )
        conn.commit()


def _enqueue_blocked_child_wake(db_path: Path, idempotency_key: str | None = None) -> None:
    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="delegation",
        reason="child_report",
        payload={
            "issue_id": "issue:intake",
            "child_issue_id": "issue:child",
            "child_issue_status": "blocked",
            "wake_reason": "child_report",
        },
        idempotency_key=idempotency_key,
    )


class _NoOpLeadRuntime:
    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(status="completed", output="Engineer desbloqueado.", actions={})


def test_sod_signal_when_engineer_and_reviewer_share_provider(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        # Engineer + reviewer both on openai_api -> shared provider.
        conn.execute("UPDATE agents SET adapter_type = 'openai_api' WHERE id = 'role:engineer'")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id)"
            " VALUES ('role:reviewer', 'reviewer', 'R', 'standard', 'openai_api', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:rev', 'goal-1', 'issue:intake', 'Review', 'done', 'reviewer', 'role:reviewer')"
        )
        conn.commit()

    executor = RunExecutor(db_path, AdapterRegistry([]))
    verification = executor._machine_close_verification("issue:intake")

    assert "Separation of duties" in verification
    assert "openai" in verification


def test_no_sod_signal_with_distinct_providers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET adapter_type = 'openai_api' WHERE id = 'role:engineer'")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id)"
            " VALUES ('role:reviewer', 'reviewer', 'R', 'standard', 'gemini_api', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:rev', 'goal-1', 'issue:intake', 'Review', 'done', 'reviewer', 'role:reviewer')"
        )
        conn.commit()

    executor = RunExecutor(db_path, AdapterRegistry([]))
    verification = executor._machine_close_verification("issue:intake")

    assert "Separation of duties" not in verification


def test_close_verification_blocks_tests_without_test_runner(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_inventory.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    executor = RunExecutor(db_path, AdapterRegistry([]))
    verification = executor._machine_close_verification("issue:intake")

    assert "BLOQUEANTE" in verification
    assert "no existe report de test_runner" in verification


def test_close_verification_accepts_test_runner_exit_zero(tmp_path: Path) -> None:
    from aiteam.db.agent_reports import record_agent_report

    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_inventory.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id)"
            " VALUES ('role:test_runner', 'test_runner', 'T', 'cheap', 'subscription_cli', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:tests', 'goal-1', 'issue:intake', 'Run tests', 'done', 'test_runner', 'role:test_runner')"
        )
        conn.commit()
    record_agent_report(
        db_path,
        issue_id="issue:tests",
        agent_id="role:test_runner",
        run_id=None,
        agent_role="test_runner",
        parsed={
            "role": "test_runner",
            "result": "done",
            "issue_status": "done",
            "evidence": "pytest -q finished with exit 0",
        },
    )

    executor = RunExecutor(db_path, AdapterRegistry([]))
    verification = executor._machine_close_verification("issue:intake")

    assert "Test runner: suite detectada" in verification
    assert "BLOQUEANTE" not in verification


def test_acceptance_criteria_pipeline(tmp_path: Path) -> None:
    """Criteria set at delegation land in the child's metadata, surface in its
    wake payload, and appear as coverage in the close verification."""
    from aiteam.db.wake_payload import build_wake_payload

    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        # Close the fixture's engineer child so the same-role idempotency
        # check doesn't swallow the new delegation.
        conn.execute("UPDATE issues SET status = 'done' WHERE id = 'issue:child'")
        conn.commit()

    class _DelegatingLeadRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(
                status="completed",
                output="Delego con criterios",
                actions={
                    "create_issues": [
                        {
                            "title": "Implementar inventario",
                            "description": "Clase Inventory en C# con añadir/quitar items y cantidades. " * 3,
                            "role": "engineer",
                            "complexity": "medium",
                            "acceptance_criteria": [
                                "Inventory.AddItem incrementa cantidad",
                                "Inventory.RemoveItem falla sin stock",
                            ],
                        }
                    ]
                },
            )

    registry = AdapterRegistry([_DelegatingLeadRuntime()])
    executor = RunExecutor(db_path, registry)
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="manual", reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        child = conn.execute(
            "SELECT id, metadata_json FROM issues WHERE title = 'Implementar inventario'"
        ).fetchone()
    assert child is not None
    meta = json.loads(child["metadata_json"])
    assert meta["acceptance_criteria"] == [
        "Inventory.AddItem incrementa cantidad",
        "Inventory.RemoveItem falla sin stock",
    ]

    # The child's wake payload surfaces the done-bar…
    payload = build_wake_payload(db_path, issue_id=str(child["id"]))
    assert payload["issue"]["acceptance_criteria"] == meta["acceptance_criteria"]

    # …and the close verification reports coverage.
    verification = executor._machine_close_verification("issue:intake")
    assert "Criterios de aceptacion" in verification
    assert "0/2 con evidencia especifica" in verification
    assert "Criterio pendiente: Inventory.AddItem incrementa cantidad" in verification


def _make_status_runtime(target_status: str):
    class _StatusRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(status="completed", output="ok", actions={"issue_status": target_status})

    return _StatusRuntime()


def _run_status_transition(tmp_path: Path, *, agent_id: str, issue_id: str, target: str) -> None:
    registry = AdapterRegistry([_make_status_runtime(target)])
    executor = RunExecutor(tmp_path / "aiteam.db", registry)
    enqueue_wakeup(
        tmp_path / "aiteam.db", agent_id=agent_id, source="manual", reason="manual",
        payload={"issue_id": issue_id, "wake_reason": "manual"},
        idempotency_key=f"sm-{agent_id}-{target}",
    )
    dispatch = HeartbeatScheduler(tmp_path / "aiteam.db").dispatch_next(agent_id=agent_id)
    assert dispatch is not None
    executor.execute(dispatch)


def _issue_status(db_path: Path, issue_id: str) -> str:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute("SELECT status FROM issues WHERE id = ?", (issue_id,)).fetchone()[0]


def test_worker_cannot_requeue_own_issue(tmp_path: Path) -> None:
    """A worker setting its own issue back to `todo` is loop fuel — denied."""
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET adapter_type = 'subscription_cli' WHERE id = 'role:engineer'")
        conn.execute("UPDATE issues SET status = 'in_progress' WHERE id = 'issue:child'")
        conn.commit()

    _run_status_transition(tmp_path, agent_id="role:engineer", issue_id="issue:child", target="todo")

    assert _issue_status(db_path, "issue:child") == "in_progress"  # unchanged
    with sqlite3.connect(str(db_path)) as conn:
        denied = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'role.op_denied'"
            " AND json_extract(payload_json, '$.action_group') = 'issue_status'"
        ).fetchone()
    assert denied[0] == 1


def test_worker_can_close_own_issue(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET adapter_type = 'subscription_cli' WHERE id = 'role:engineer'")
        conn.execute("UPDATE issues SET status = 'in_progress' WHERE id = 'issue:child'")
        conn.commit()

    _run_status_transition(tmp_path, agent_id="role:engineer", issue_id="issue:child", target="done")

    assert _issue_status(db_path, "issue:child") == "done"


def test_worker_cannot_resurrect_terminal_issue(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET adapter_type = 'subscription_cli' WHERE id = 'role:engineer'")
        conn.execute("UPDATE issues SET status = 'cancelled' WHERE id = 'issue:child'")
        conn.commit()

    _run_status_transition(tmp_path, agent_id="role:engineer", issue_id="issue:child", target="in_progress")

    assert _issue_status(db_path, "issue:child") == "cancelled"  # stays terminal


def test_lead_keeps_full_status_authority(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)

    _run_status_transition(tmp_path, agent_id="role:lead", issue_id="issue:intake", target="todo")

    assert _issue_status(db_path, "issue:intake") == "todo"


def test_lead_cannot_close_when_tests_lack_test_runner_exit_zero(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_inventory.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    _run_status_transition(tmp_path, agent_id="role:lead", issue_id="issue:intake", target="done")

    assert _issue_status(db_path, "issue:intake") == "in_progress"
    with sqlite3.connect(str(db_path)) as conn:
        denied = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'quality_gate.denied'"
            " AND json_extract(payload_json, '$.reason') = 'test_runner_exit_zero_required'"
        ).fetchone()
    assert denied[0] == 1


def test_solo_lead_machine_verification_targets_tests_not_locked_scratch_dirs(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE issues SET metadata_json=? WHERE id='issue:intake'",
            (json.dumps({"profile": "solo_lead"}),),
        )
        conn.commit()
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    # A directory matching pytest's default recursion surface must not be
    # needed to validate the explicitly discovered public test file.
    (tmp_path / "tmp_cli_scratch").mkdir()

    _run_status_transition(tmp_path, agent_id="role:lead", issue_id="issue:intake", target="done")

    assert _issue_status(db_path, "issue:intake") == "done"
    with sqlite3.connect(str(db_path)) as conn:
        receipt = conn.execute(
            "SELECT payload_json FROM activity_log "
            "WHERE action='solo_lead.verification_passed' AND target_id='issue:intake'"
        ).fetchone()
    assert "test_ok.py" in json.loads(receipt[0])["command"]


def test_redundant_reviewer_wake_closes_reopened_issue(tmp_path: Path) -> None:
    (tmp_path / ".aiteam").mkdir()
    db_path = tmp_path / ".aiteam" / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    executor = RunExecutor(db_path, build_default_registry())
    fingerprint = executor._workspace_digest(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET role='reviewer' WHERE id='role:engineer'")
        conn.execute("UPDATE issues SET role='reviewer', status='todo' WHERE id='issue:child'")
        conn.execute(
            "INSERT INTO runs (id,agent_id,issue_id,status) VALUES ('run:prior-review','role:engineer','issue:child','completed')"
        )
        conn.execute(
            "INSERT INTO activity_log (id,run_id,actor_agent_id,action,target_type,target_id,payload_json) "
            "VALUES ('activity:review','run:prior-review','role:engineer','review.evidence','issue','issue:child',?)",
            (json.dumps({"fingerprint": fingerprint}),),
        )
        conn.commit()
    enqueue_wakeup(
        db_path,
        agent_id="role:engineer",
        source="unblock",
        reason="lead_directive",
        payload={"issue_id": "issue:child", "wake_reason": "lead_directive"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:engineer")
    assert dispatch is not None

    executor.execute(dispatch)

    assert _issue_status(db_path, "issue:child") == "done"
    with sqlite3.connect(str(db_path)) as conn:
        skipped = conn.execute(
            "SELECT error_code FROM runs WHERE id != 'run:prior-review' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0]
        lead_wakes = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE agent_id='role:lead' AND status='queued'"
        ).fetchone()[0]
    assert skipped == "review_evidence_unchanged"
    assert lead_wakes == 1


def test_lead_can_close_despite_unity_library_package_json(tmp_path: Path) -> None:
    """Live capa-2 bug: Library/PackageCache/**/package.json (Unity's own
    dependency cache, hundreds of files) was mistaken for a JS test suite,
    permanently blocking issue:intake's closure since no real test_runner
    could ever exist for a suite that doesn't exist."""
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    package_cache = tmp_path / "Library" / "PackageCache" / "com.unity.modules.ai@1.0.0"
    package_cache.mkdir(parents=True)
    (package_cache / "package.json").write_text('{"name": "com.unity.modules.ai"}', encoding="utf-8")

    _run_status_transition(tmp_path, agent_id="role:lead", issue_id="issue:intake", target="done")

    assert _issue_status(db_path, "issue:intake") == "done"
    with sqlite3.connect(str(db_path)) as conn:
        denied = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'quality_gate.denied'"
            " AND json_extract(payload_json, '$.reason') = 'test_runner_exit_zero_required'"
        ).fetchone()
    assert denied[0] == 0


def test_lead_file_ops_on_api_adapter_blocked_preventively(tmp_path: Path) -> None:
    """A Lead on an API adapter (no CLI sandbox) emitting file_ops must be
    blocked BEFORE materialization, not just flagged after."""
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)

    class _FileWritingLeadRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id, "AITEAM_WORKSPACE_ROOT": str(tmp_path)}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(
                status="completed",
                output="Implemento yo mismo vía ops",
                actions={"file_ops": [{"op": "write_file", "path": "hack.cs", "body": "// lead"}]},
            )

    registry = AdapterRegistry([_FileWritingLeadRuntime()])
    executor = RunExecutor(db_path, registry)
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="manual", reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)

    assert not (tmp_path / "hack.cs").exists()  # never materialized
    with sqlite3.connect(str(db_path)) as conn:
        denied = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'role.op_denied'"
        ).fetchone()
    assert denied[0] >= 1


def test_solo_lead_can_write_workspace_and_close_without_children(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM issues WHERE parent_id='issue:intake'")
        conn.execute(
            "UPDATE issues SET metadata_json=? WHERE id='issue:intake'",
            (json.dumps({"profile": "solo_lead"}),),
        )
        conn.commit()

    class _SoloWritingLeadRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id, "AITEAM_WORKSPACE_ROOT": str(tmp_path)}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(
                status="completed",
                output="Implementado y verificado por el único agente.",
                actions={
                    "file_ops": [
                        {"op": "write_file", "path": "solution.py", "body": "VALUE = 42\n"},
                        {
                            "op": "write_file",
                            "path": "tests/test_solution.py",
                            "body": "from solution import VALUE\n\ndef test_value():\n    assert VALUE == 42\n",
                        },
                    ],
                    "issue_status": "done",
                    "create_issues": [{"title": "No debe existir", "role": "reviewer"}],
                },
            )

    executor = RunExecutor(db_path, AdapterRegistry([_SoloWritingLeadRuntime()]))
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="manual", reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual", "profile": "solo_lead"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)

    assert (tmp_path / "solution.py").read_text(encoding="utf-8") == "VALUE = 42\n"
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT status FROM issues WHERE id='issue:intake'").fetchone()[0] == "done"
        assert conn.execute("SELECT COUNT(*) FROM issues WHERE parent_id='issue:intake'").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='role.violation'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='solo_lead.verification_passed'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE status='queued'"
        ).fetchone()[0] == 0


def test_non_editing_role_writing_files_logs_role_violation(tmp_path: Path) -> None:
    """A Lead/scout that produces workspace changes is recorded as a role
    violation (they must delegate/report, never edit files)."""
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)

    class _CodingLeadRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id, "AITEAM_WORKSPACE_ROOT": str(tmp_path)}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            # Simulate the Lead editing a file directly (role violation).
            (tmp_path / "sneaky.cs").write_text("// lead wrote this", encoding="utf-8")
            return ExecutionResult(status="completed", output="Lo implementé yo mismo.", actions={})

    registry = AdapterRegistry([_CodingLeadRuntime()])
    executor = RunExecutor(db_path, registry)
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="manual", reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)

    assert _count_activity(db_path, "role.violation", dispatch.run["id"]) == 1


class _FailingRawOutputRuntime:
    """A CLI run that fails and returns raw stdout (echoed prompt) as output."""

    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="failed",
            output="=== Instrucciones ===\nEres un ORQUESTADOR...\n" + "x" * 4000,
            error="exit code 1",
            error_code="subscription_cli_nonzero_exit",
        )


def test_failed_run_output_not_posted_as_chat_comment(tmp_path: Path) -> None:
    """A failed run's raw stdout must not become a chat comment (it spams the
    user with the echoed prompt), but the run event is still recorded."""
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    registry = AdapterRegistry([_FailingRawOutputRuntime()])
    executor = RunExecutor(db_path, registry)
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="manual", reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        comments = conn.execute(
            "SELECT COUNT(*) FROM issue_comments WHERE issue_id = 'issue:intake' AND body LIKE '%ORQUESTADOR%'"
        ).fetchone()
        events = conn.execute(
            "SELECT COUNT(*) FROM run_events WHERE event_type = 'output'"
        ).fetchone()
    assert comments[0] == 0            # chat is clean
    assert events[0] >= 1              # but the raw output is kept for debugging


def _count_activity(db_path: Path, action: str, target_id: str) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = ? AND target_id = ?",
            (action, target_id),
        ).fetchone()
    return int(row[0]) if row else 0


def _count_cb_interactions(db_path: Path, issue_id: str) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT COUNT(*) FROM issue_thread_interactions"
            " WHERE issue_id = ? AND idempotency_key LIKE ?",
            (issue_id, "loop_circuit_breaker:%"),
        ).fetchone()
    return int(row[0]) if row else 0


def test_circuit_breaker_logs_unblock_skipped_on_no_op(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    registry = AdapterRegistry([_NoOpLeadRuntime()])
    executor = RunExecutor(db_path, registry)
    _enqueue_blocked_child_wake(db_path)
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)
    assert _count_activity(db_path, "lead.unblock_skipped", "issue:child") == 1


def test_circuit_breaker_no_event_when_child_unblocked(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    registry = AdapterRegistry([_UpdateChildRuntime()])
    executor = RunExecutor(db_path, registry)
    _enqueue_blocked_child_wake(db_path)
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)
    assert _count_activity(db_path, "lead.unblock_skipped", "issue:child") == 0


def test_circuit_breaker_escalates_after_three_skips(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    registry = AdapterRegistry([_NoOpLeadRuntime()])
    executor = RunExecutor(db_path, registry)
    for i in range(3):
        _enqueue_blocked_child_wake(db_path, idempotency_key=f"cb_test_{i}")
        dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
        if dispatch is not None:
            executor.execute(dispatch)
    assert _count_activity(db_path, "lead.unblock_skipped", "issue:child") >= 3
    assert _count_activity(db_path, "loop.detected", "issue:child") >= 1
    assert _count_cb_interactions(db_path, "issue:intake") >= 1


def test_circuit_breaker_payload_has_mandatory_instruction(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    captured: list[dict] = []

    class _CapturingRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            raw = str(wake_context.get("wake_payload_json") or "{}")
            try:
                captured.append(json.loads(raw))
            except Exception:
                pass
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(status="completed", output="ok", actions={})

    registry = AdapterRegistry([_CapturingRuntime()])
    executor = RunExecutor(db_path, registry)
    _enqueue_blocked_child_wake(db_path)
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)
    assert captured, "Runtime was never called"
    payload = captured[0]
    assert "unblock_action_required" in payload
    assert "mandatory_instruction" in payload
    assert payload["unblock_action_required"][0]["child_issue_id"] == "issue:child"
    assert payload["project_toolchains"]["schema_version"] == "project_toolchain_projection_v1"
    assert payload["project_toolchains"]["commands_executed"] is False
    assert payload["project_toolchains"]["support_claim"] is False


def test_update_child_issue_empty_body_requeue_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)

    class _EmptyBodyRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(
                status="completed",
                output="Unblocking.",
                actions={
                    "update_child_issues": [
                        {"child_issue_id": "issue:child", "status": "todo", "body": ""}
                    ]
                },
            )

    registry = AdapterRegistry([_EmptyBodyRuntime()])
    executor = RunExecutor(db_path, registry)
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="test", reason="child_report",
        payload={"issue_id": "issue:intake"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        child = conn.execute(
            "SELECT status FROM issues WHERE id = ?", ("issue:child",)
        ).fetchone()
    assert dict(child)["status"] == "blocked"


class _FailingLeadRuntime:
    """Simulates a Lead run that dies at the provider transport layer (429)."""

    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="failed",
            error="HTTP 429: rate limit reached",
            error_code="api_error",
            exit_code=1,
        )


def test_circuit_breaker_ignores_failed_lead_runs(tmp_path: Path) -> None:
    """Infra failures are not Lead decisions: no unblock_skipped, no breaker."""
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    registry = AdapterRegistry([_FailingLeadRuntime()])
    executor = RunExecutor(db_path, registry)
    for i in range(4):
        _enqueue_blocked_child_wake(db_path, idempotency_key=f"cb_fail_{i}")
        dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
        if dispatch is not None:
            executor.execute(dispatch)
    assert _count_activity(db_path, "lead.unblock_skipped", "issue:child") == 0
    assert _count_activity(db_path, "loop.detected", "issue:child") == 0
    assert _count_cb_interactions(db_path, "issue:intake") == 0
    assert _count_activity(db_path, "lead.unblock_run_failed", "issue:child") == 4


def test_llm_lead_cycle_close_gets_machine_verification(tmp_path: Path) -> None:
    """An LLM Lead cannot whitewash the close proposal: the executor appends
    the machine-computed verification (reviewer verdict + workspace scan)."""
    from aiteam.db.comments import create_comment

    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("role:reviewer", "reviewer", "Reviewer", "standard", "openai_api", "role:lead"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("issue:review", "goal-1", "issue:intake", "Review", "done", "reviewer", "role:reviewer"),
        )
        conn.commit()
    create_comment(
        db_path,
        issue_id="issue:review",
        author_agent_id="role:reviewer",
        body="Approved with stubs.\n\n---AGENT-REPORT---\nrole: reviewer\nresult: approved\n",
    )
    (tmp_path / "Main.cs").write_text("// TODO: implement", encoding="utf-8")

    class _CycleCloseLeadRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(
                status="completed",
                output="Propongo cierre",
                actions={
                    "interactions": [
                        {
                            "kind": "request_confirmation",
                            "payload": {"version": 1, "reason": "initial_cycle_ready", "parent_issue_id": "issue:intake"},
                            "title": "Validación de entrega",
                            "summary": "Todo perfecto, sin stubs ni placeholders.",
                        }
                    ]
                },
            )

    registry = AdapterRegistry([_CycleCloseLeadRuntime()])
    executor = RunExecutor(db_path, registry)
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="test", reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT summary FROM issue_thread_interactions WHERE issue_id = 'issue:intake'"
            " ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    summary = str(row["summary"])
    assert "Todo perfecto" in summary
    assert "Verificación automática del sistema" in summary
    assert "approved" in summary
    assert "stub" in summary.lower()


def test_file_ops_reject_provider_convention_filenames(tmp_path: Path) -> None:
    """AI Teams must never create AGENTS.md/CLAUDE.md/etc. in managed projects."""
    from aiteam.heartbeat.executor import _execute_file_ops

    touched = _execute_file_ops(
        [
            {"op": "write_file", "path": "CLAUDE.md", "body": "x"},
            {"op": "write_file", "path": "docs/AGENTS.md", "body": "x"},
            {"op": "append_file", "path": "gemini.md", "body": "x"},
            {"op": "write_file", "path": ".aiteam/instructions.md", "body": "persistent"},
            {"op": "write_file", "path": "README.md", "body": "ok"},
        ],
        tmp_path,
    )

    assert touched == [".aiteam/instructions.md", "README.md"]
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / "docs" / "AGENTS.md").exists()
    assert not (tmp_path / "gemini.md").exists()
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "ok"


def test_file_ops_absolute_workspace_path_relativized(tmp_path: Path) -> None:
    """An agent emitting the FULL workspace path must write to the right spot,
    not re-root it as a nested 'Users/.../workspace/' tree (observed bug)."""
    from aiteam.heartbeat.executor import _execute_file_ops

    absolute_inside = str(tmp_path / "notes" / "context.md")
    touched = _execute_file_ops(
        [{"op": "write_file", "path": absolute_inside, "body": "hola"}],
        tmp_path,
    )

    assert touched == [str(Path("notes") / "context.md")]
    assert (tmp_path / "notes" / "context.md").read_text(encoding="utf-8") == "hola"
    # The old drive-strip behaviour would have created e.g. Users/... under tmp_path.
    stray_roots = [p.name for p in tmp_path.iterdir() if p.is_dir() and p.name not in {"notes"}]
    assert stray_roots == []


def test_file_ops_absolute_path_outside_workspace_skipped(tmp_path: Path) -> None:
    from aiteam.heartbeat.executor import _execute_file_ops

    outside = str(tmp_path.parent / "fuera.md")
    touched = _execute_file_ops(
        [{"op": "write_file", "path": outside, "body": "no"}],
        tmp_path,
    )

    assert touched == []
    assert not Path(outside).exists()
    # And no re-rooted copy inside the workspace either.
    assert list(tmp_path.rglob("fuera.md")) == []


def test_file_ops_leading_slash_still_means_workspace_root(tmp_path: Path) -> None:
    from aiteam.heartbeat.executor import _execute_file_ops

    touched = _execute_file_ops(
        [{"op": "write_file", "path": "/docs/guide.md", "body": "g"}],
        tmp_path,
    )

    assert touched == ["docs/guide.md"]
    assert (tmp_path / "docs" / "guide.md").read_text(encoding="utf-8") == "g"


# ── Delegation churn breaker ──────────────────────────────────────────────────

def _seed_churn_children(db_path: Path, parent: str, role: str, n: int) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
                " VALUES (?, 'goal-1', ?, ?, 'cancelled', ?, 'role:reviewer')",
                (f"churn-{role}-{i}", parent, f"Revisar intento {i}", role),
            )
        conn.commit()


class _ChurnLeadRuntime:
    """Lead that keeps creating yet another reviewer fix-cycle issue."""

    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="Creo otra revisión",
            actions={
                "create_issues": [
                    {
                        "title": "Revisar de nuevo el fix",
                        "description": "Revisión del último fix con criterios de aceptación claros y evidencia por archivo. " * 3,
                        "role": "reviewer",
                        "complexity": "medium",
                    }
                ]
            },
        )


def test_delegation_churn_blocks_and_escalates_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_DELEGATION_CHURN_LIMIT", "8")
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    _seed_churn_children(db_path, "issue:intake", "reviewer", 8)

    registry = AdapterRegistry([_ChurnLeadRuntime()])
    executor = RunExecutor(db_path, registry)
    for i in range(2):
        enqueue_wakeup(
            db_path, agent_id="role:lead", source="manual", reason="manual",
            payload={"issue_id": "issue:intake", "wake_reason": "manual"},
            idempotency_key=f"churn-wake-{i}",
        )
        dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
        if dispatch is not None:
            executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        created = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE parent_id = 'issue:intake' AND title = 'Revisar de nuevo el fix'"
        ).fetchone()
        escalations = conn.execute(
            "SELECT COUNT(*) FROM issue_thread_interactions WHERE idempotency_key LIKE 'delegation_churn:%'"
        ).fetchone()
        blocked_events = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'delegation.churn_blocked'"
        ).fetchone()
    assert created[0] == 0            # no more churn issues created
    assert escalations[0] == 1        # exactly one escalation (idempotent)
    assert blocked_events[0] == 2     # both attempts audited


def test_delegation_churn_allows_below_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_DELEGATION_CHURN_LIMIT", "8")
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    _seed_churn_children(db_path, "issue:intake", "reviewer", 3)

    registry = AdapterRegistry([_ChurnLeadRuntime()])
    executor = RunExecutor(db_path, registry)
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="manual", reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        created = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE parent_id = 'issue:intake' AND title = 'Revisar de nuevo el fix'"
        ).fetchone()
    assert created[0] == 1


def test_delegation_churn_accept_allows_another_round(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_DELEGATION_CHURN_LIMIT", "8")
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    _seed_churn_children(db_path, "issue:intake", "reviewer", 8)

    registry = AdapterRegistry([_ChurnLeadRuntime()])
    executor = RunExecutor(db_path, registry)
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="manual", reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)  # trips the breaker

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        interaction = conn.execute(
            "SELECT id FROM issue_thread_interactions WHERE idempotency_key LIKE 'delegation_churn:%'"
        ).fetchone()
    assert interaction is not None
    resolve_interaction(
        db_path, interaction_id=interaction["id"], action="accept", resolved_by_user_id="user",
    )

    enqueue_wakeup(
        db_path, agent_id="role:lead", source="manual", reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual"},
        idempotency_key="churn-after-accept",
    )
    dispatch2 = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch2 is not None
    executor.execute(dispatch2)

    with sqlite3.connect(str(db_path)) as conn:
        created = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE parent_id = 'issue:intake' AND title = 'Revisar de nuevo el fix'"
        ).fetchone()
    assert created[0] == 1  # accepted → a new round is allowed


def test_adapter_recovery_reopens_exhausted_issue_with_alternative_adapter(tmp_path: Path, monkeypatch) -> None:
    """RUN-003: recovery is proposed once and only an owner accept applies it."""
    from aiteam.project_adapters import write_project_adapter_policy
    from aiteam.user_config import store_secret

    user_cfg = tmp_path / "user-config"
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(user_cfg))
    store_secret(provider="google", name="default", secret="gemini-key")
    for profile_id in ("openai_api", "gemini_api"):
        for option in model_options().get(profile_id, []):
            record_model_health(
                profile_id,
                str(option["value"]),
                available=True,
                reason="recovery fixture",
            )

    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            """
            INSERT INTO agents (id, role, name, seniority, adapter_type, adapter_config_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "agent-1",
                "engineer",
                "Engineer",
                "standard",
                "openai_api",
                json.dumps({"profile_id": "openai_api", "model": "gpt-5.6-sol"}),
            ),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id, role) VALUES (?, ?, ?, ?, ?, ?)",
            ("issue-1", "g1", "Implement", "in_progress", "agent-1", "engineer"),
        )
        conn.commit()
    # Project allowlist without CLI profiles so reconcile cannot pre-upgrade.
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api", "gemini_api"])

    class _PlanOnlyOpenAIRuntime:
        descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(status="completed", output="Plan detallado, sin archivos.", actions={})

    registry = AdapterRegistry([_PlanOnlyOpenAIRuntime()])
    executor = RunExecutor(db_path, registry)
    enqueue_wakeup(
        db_path, agent_id="agent-1", source="test", reason="liveness_continuation",
        payload={"issue_id": "issue-1", "wake_reason": "liveness_continuation", "continuation_attempt": 2},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="agent-1")
    assert dispatch is not None
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        agent = conn.execute("SELECT adapter_type FROM agents WHERE id = 'agent-1'").fetchone()
        issue = conn.execute("SELECT status FROM issues WHERE id = 'issue-1'").fetchone()
        interaction = conn.execute(
            """
            SELECT id, status, payload_json FROM issue_thread_interactions
            WHERE issue_id = 'issue-1' AND idempotency_key LIKE 'assignment_change:adapter_recovery_required:%'
            """
        ).fetchone()

    assert agent["adapter_type"] == "openai_api", "proponer no puede mutar la asignación"
    assert issue["status"] == "blocked"
    assert interaction is not None and interaction["status"] == "pending"
    assert json.loads(interaction["payload_json"])["proposed"]["adapter_type"] == "gemini_api"

    resolve_interaction(
        db_path,
        interaction_id=str(interaction["id"]),
        action="accept",
        resolved_by_user_id="owner",
    )
    accepted_dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="agent-1")
    assert accepted_dispatch is not None
    executor.execute(accepted_dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        agent = conn.execute("SELECT adapter_type FROM agents WHERE id = 'agent-1'").fetchone()
        issue = conn.execute("SELECT status FROM issues WHERE id = 'issue-1'").fetchone()
        accepted = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'issue.assignment_change_accepted' AND target_id = 'issue-1'"
        ).fetchone()
        wakeup = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE agent_id = 'agent-1' AND source = 'assignment_change' AND status = 'queued'"
        ).fetchone()
    assert agent["adapter_type"] == "gemini_api"
    assert issue["status"] == "todo"
    assert int(accepted[0]) == 1
    assert int(wakeup[0]) == 1

    # Second exhaustion on the same issue must NOT trigger another recovery.
    assert executor._attempt_adapter_recovery(
        issue_id="issue-1",
        agent_id="agent-1",
        run_id="run:x",
        failed_adapter_type="gemini_api",
        agent_role="engineer",
        liveness_reason="plan_only_exhausted_at_attempt_2",
    ) is False


def test_adapter_recovery_noop_without_alternative_adapter(tmp_path: Path, monkeypatch) -> None:
    from aiteam.project_adapters import write_project_adapter_policy

    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("agent-1", "engineer", "Engineer", "standard", "openai_api"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id, role) VALUES (?, ?, ?, ?, ?, ?)",
            ("issue-1", "g1", "Implement", "blocked", "agent-1", "engineer"),
        )
        conn.commit()
    # Only the failed adapter in the allowlist, and no secret stored → no candidates.
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api"])

    executor = RunExecutor(db_path, AdapterRegistry([]))
    recovered = executor._attempt_adapter_recovery(
        issue_id="issue-1",
        agent_id="agent-1",
        run_id="run:x",
        failed_adapter_type="openai_api",
        agent_role="engineer",
        liveness_reason="plan_only_exhausted_at_attempt_2",
    )

    assert recovered is False
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT status FROM issues WHERE id = 'issue-1'").fetchone()
    assert row[0] == "blocked"


def test_file_ops_do_not_delete_provider_convention_files(tmp_path: Path) -> None:
    from aiteam.heartbeat.executor import _execute_file_ops

    existing = tmp_path / "AGENTS.md"
    existing.write_text("user-owned", encoding="utf-8")

    touched = _execute_file_ops(
        [{"op": "delete_file", "path": "AGENTS.md"}],
        tmp_path,
    )

    assert touched == []
    assert existing.read_text(encoding="utf-8") == "user-owned"


# ── Quality gate: waiver del usuario, denegación audible y test_runner builtin ─
# Deadlock visto en vivo (proyecto CLI Notas, 2026-07-15): el gate denegaba el
# cierre en silencio DESPUÉS de que el usuario aceptara cerrar sin pytest, el
# Lead creía haber cerrado y el proyecto quedaba blocked sin wakeups pendientes.

from aiteam.policies import RUNTIME_VERIFICATION_WAIVER_REASON


def _write_pytest_suite(tmp_path: Path, *, passing: bool = True) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(exist_ok=True)
    body = "def test_ok():\n    assert True\n" if passing else "def test_ko():\n    assert False\n"
    (tests_dir / "test_suite.py").write_text(body, encoding="utf-8")


def _add_test_runner_child(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("role:test_runner", "test_runner", "Test Runner", "cheap", "subscription_cli", "role:lead"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "issue:testrun", "goal-1", "issue:intake", "Ejecutar suite de tests",
                "in_progress", "test_runner", "role:test_runner",
            ),
        )
        conn.commit()


class _MustNotExecuteRuntime:
    """Registro válido para el dispatch, pero si el executor llega a llamarlo
    es que el builtin de test_runner NO interceptó la run."""

    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        raise AssertionError("role=test_runner debe ejecutarse por el builtin, nunca por el adapter")


def _gate_dispatch_one(tmp_path: Path, *, agent_id: str, issue_id: str, runtime=None) -> None:
    registry = AdapterRegistry([runtime or _make_status_runtime("done")])
    executor = RunExecutor(tmp_path / "aiteam.db", registry)
    enqueue_wakeup(
        tmp_path / "aiteam.db", agent_id=agent_id, source="manual", reason="manual",
        payload={"issue_id": issue_id, "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(tmp_path / "aiteam.db").dispatch_next(agent_id=agent_id)
    assert dispatch is not None
    executor.execute(dispatch)


def test_gate_denial_posts_corrective_comment_and_rewakes_lead(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    _write_pytest_suite(tmp_path)

    _run_status_transition(tmp_path, agent_id="role:lead", issue_id="issue:intake", target="done")

    assert _issue_status(db_path, "issue:intake") == "in_progress"  # denegado
    with sqlite3.connect(str(db_path)) as conn:
        comment = conn.execute(
            "SELECT body FROM issue_comments WHERE issue_id = 'issue:intake'"
            " AND author_user_id = 'system' AND body LIKE '%[gate:test_runner_exit_zero_required]%'"
        ).fetchone()
        wake = conn.execute(
            "SELECT status FROM wakeup_requests WHERE agent_id = 'role:lead'"
            " AND reason = 'quality_gate_denied' AND status = 'queued'"
        ).fetchone()
    assert comment is not None, "la denegación debe dejar un comentario correctivo visible"
    assert "test_runner" in comment[0]
    assert RUNTIME_VERIFICATION_WAIVER_REASON in comment[0]
    assert wake is not None, "la denegación debe re-despertar al Lead, no dejar el proyecto mudo"


def test_gate_denial_escalates_to_user_after_notify_cap(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    _write_pytest_suite(tmp_path)

    for _ in range(3):
        _gate_dispatch_one(tmp_path, agent_id="role:lead", issue_id="issue:intake")

    assert _issue_status(db_path, "issue:intake") == "in_progress"
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT payload_json, status FROM issue_thread_interactions"
            " WHERE issue_id = 'issue:intake' AND kind = 'request_confirmation'"
        ).fetchone()
        notices = conn.execute(
            "SELECT COUNT(*) FROM issue_comments WHERE issue_id = 'issue:intake'"
            " AND author_user_id = 'system' AND body LIKE '%[gate:%'"
        ).fetchone()[0]
    assert row is not None, "tras el cap de avisos el sistema debe escalar al usuario"
    assert row[1] == "pending"
    assert json.loads(row[0])["reason"] == RUNTIME_VERIFICATION_WAIVER_REASON
    assert notices <= 2, "el cap debe frenar el spam de comentarios correctivos"


def test_accepted_runtime_waiver_lets_lead_close(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    _write_pytest_suite(tmp_path)
    interaction = create_interaction(
        db_path,
        issue_id="issue:intake",
        kind="request_confirmation",
        payload={"parent_issue_id": "issue:intake", "reason": RUNTIME_VERIFICATION_WAIVER_REASON},
        title="Confirmar cierre sin pytest ejecutado",
    )
    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept", resolved_by_user_id="user")

    _run_status_transition(tmp_path, agent_id="role:lead", issue_id="issue:intake", target="done")

    assert _issue_status(db_path, "issue:intake") == "done", (
        "una confirmación aceptada por el usuario debe dispensar el gate, no ser ignorada"
    )
    with sqlite3.connect(str(db_path)) as conn:
        waived = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'quality_gate.waived'"
        ).fetchone()[0]
    assert waived >= 1


def test_rejected_runtime_waiver_keeps_gate_blocking(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    _write_pytest_suite(tmp_path)
    interaction = create_interaction(
        db_path,
        issue_id="issue:intake",
        kind="request_confirmation",
        payload={"parent_issue_id": "issue:intake", "reason": RUNTIME_VERIFICATION_WAIVER_REASON},
    )
    resolve_interaction(db_path, interaction_id=interaction["id"], action="reject", resolved_by_user_id="user")

    _run_status_transition(tmp_path, agent_id="role:lead", issue_id="issue:intake", target="done")

    assert _issue_status(db_path, "issue:intake") == "in_progress"


def test_builtin_test_runner_green_suite_reports_exit_zero_and_unlocks_close(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    _add_test_runner_child(db_path)
    _write_pytest_suite(tmp_path, passing=True)

    _gate_dispatch_one(
        tmp_path, agent_id="role:test_runner", issue_id="issue:testrun",
        runtime=_MustNotExecuteRuntime(),
    )

    assert _issue_status(db_path, "issue:testrun") == "done"
    with sqlite3.connect(str(db_path)) as conn:
        report = conn.execute(
            "SELECT result, evidence, valid, is_assignee FROM agent_reports"
            " WHERE issue_id = 'issue:testrun' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert report is not None
    assert report[0] == "done"
    assert "exit 0" in report[1]
    assert report[2] == 1 and report[3] == 1

    # Con la evidencia registrada, el gate deja cerrar al Lead.
    _run_status_transition(tmp_path, agent_id="role:lead", issue_id="issue:intake", target="done")
    assert _issue_status(db_path, "issue:intake") == "done"


def test_builtin_test_runner_failing_suite_blocks_issue(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    _add_test_runner_child(db_path)
    _write_pytest_suite(tmp_path, passing=False)

    _gate_dispatch_one(
        tmp_path, agent_id="role:test_runner", issue_id="issue:testrun",
        runtime=_MustNotExecuteRuntime(),
    )

    assert _issue_status(db_path, "issue:testrun") == "blocked"
    with sqlite3.connect(str(db_path)) as conn:
        report = conn.execute(
            "SELECT result, evidence FROM agent_reports"
            " WHERE issue_id = 'issue:testrun' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        run_row = conn.execute(
            "SELECT status FROM runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert report is not None
    assert report[0] == "failed"
    assert "exit 0" not in report[1]
    assert run_row[0] == "completed", "una suite roja no es un fallo del runner: la run completó su trabajo"


class _SubscriptionUsageRuntime:
    """Run de canal suscripción: coste 0 pero tokens reales consumidos."""

    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="done",
            actual_cost_cents=0,
            usage={"input_tokens": 14874, "output_tokens": 17, "cached_input_tokens": 3456},
        )


def test_zero_cost_run_with_tokens_records_cost_event(tmp_path: Path) -> None:
    """El canal de suscripción (tarifa plana) consumía millones de tokens sin
    dejar rastro: cost_events solo se escribía con cost > 0, así que la
    auditoría y la economía de hiring solo veían el canal API."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    registry = AdapterRegistry([_SubscriptionUsageRuntime()])
    executor = RunExecutor(db_path, registry)
    dispatch = _dispatch_one(db_path)

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cost = conn.execute(
            "SELECT * FROM cost_events WHERE run_id = ?", (dispatch.run["id"],)
        ).fetchone()
    assert cost is not None, "una run con tokens debe dejar cost_event aunque cueste 0"
    assert cost["cost_cents"] == 0
    assert cost["input_tokens"] == 14874
    assert cost["output_tokens"] == 17


# ── Hard daily cost cap (project-wide cascade pile-up mitigation) ─────────────

def test_daily_cost_cap_blocks_and_escalates_when_reached(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_DAILY_COST_CAP_CENTS", "100")
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO cost_events (id, agent_id, issue_id, cost_cents, period) VALUES (?, ?, ?, ?, ?)",
            ("cost-day-1", "agent-1", "issue-1", 120, current_period()),  # 120 >= 100 cap
        )
        conn.commit()

    runtime = _CountingRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))
    dispatch = _dispatch_one(db_path)
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        wakeup = conn.execute(
            "SELECT status, error FROM wakeup_requests WHERE id = ?",
            (dispatch.wakeup_request["id"],),
        ).fetchone()
        interaction = conn.execute(
            "SELECT status, payload_json FROM issue_thread_interactions WHERE title = 'Cap de coste diario alcanzado'"
        ).fetchone()

    assert runtime.calls == 0, "no debe gastar una run más con el cap alcanzado"
    assert wakeup["status"] == "skipped"
    assert wakeup["error"] == "daily_cost_cap_reached"
    assert interaction is not None and interaction["status"] == "pending"
    assert json.loads(interaction["payload_json"])["cap_cents"] == 100


def test_daily_cost_cap_allows_after_user_accepts(tmp_path: Path, monkeypatch) -> None:
    from datetime import datetime, timezone

    monkeypatch.setenv("AITEAM_DAILY_COST_CAP_CENTS", "100")
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO cost_events (id, agent_id, issue_id, cost_cents, period) VALUES (?, ?, ?, ?, ?)",
            ("cost-day-1", "agent-1", "issue-1", 120, current_period()),
        )
        conn.commit()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    interaction = create_interaction(
        db_path,
        issue_id="issue-1",
        kind="request_confirmation",
        payload={"reason": "daily_cost_cap_reached"},
        idempotency_key=f"daily_cost_cap:{day}:issue-1",
    )
    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept")

    runtime = _CountingRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))
    dispatch = _dispatch_one(db_path)
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        run = conn.execute("SELECT status FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()
    assert runtime.calls == 1, "tras aceptar el override, la run debe ejecutarse"
    assert run[0] == "completed"


def test_daily_cost_cap_disabled_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AITEAM_DAILY_COST_CAP_CENTS", raising=False)
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO cost_events (id, agent_id, issue_id, cost_cents, period) VALUES (?, ?, ?, ?, ?)",
            ("cost-day-1", "agent-1", "issue-1", 999999, current_period()),
        )
        conn.commit()

    runtime = _CountingRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))
    dispatch = _dispatch_one(db_path)
    executor.execute(dispatch)

    assert runtime.calls == 1, "sin cap configurado, el gasto no bloquea nada"


def test_subscription_run_provenance_uses_selected_profile_identity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE agents SET adapter_config_json = ? WHERE id = 'agent-1'",
            (json.dumps({"profile_id": "antigravity_subscription", "model": "Gemini 3.1 Pro (High)"}),),
        )
        conn.commit()

    runtime = _CountingRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))
    dispatch = _dispatch_one(db_path)
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT provider, model, channel FROM runs").fetchone()
        profile = conn.execute(
            "SELECT profile_id, provider, model, channel FROM run_adapter_profiles"
        ).fetchone()
    assert dict(run) == {
        "provider": "google-antigravity",
        "model": "gemini-3.1-pro-high",
        "channel": "subscription",
    }
    assert dict(profile) == {
        "profile_id": "antigravity_subscription",
        "provider": "google-antigravity",
        "model": "gemini-3.1-pro-high",
        "channel": "subscription",
    }
    model_health = json.loads(
        (tmp_path / "user-config" / "adapter_health.json").read_text(encoding="utf-8")
    )["profiles"]["antigravity_subscription"]
    assert model_health["verified_models"] == ["gemini-3.1-pro-high"]


# ── Cascada de calidad: escalado de modelo antes de cambio de canal ───────────

def _escalation_db(tmp_path: Path, *, model: str = "gpt-5.4-mini") -> Path:
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, adapter_config_json) VALUES (?, ?, ?, ?, ?, ?)",
            ("agent-1", "engineer", "E", "standard", "subscription_cli",
             json.dumps({"profile_id": "codex_subscription", "model": model, "cli_kind": "codex"})),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id, role) VALUES (?, ?, ?, ?, ?, ?)",
            ("issue-1", "g1", "Implement", "blocked", "agent-1", "engineer"),
        )
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status) VALUES ('run:x', 'agent-1', 'issue-1', 'failed')"
        )
        conn.commit()
    return db_path


def test_model_escalation_upgrades_to_profile_senior_once(tmp_path: Path, monkeypatch) -> None:
    """Peldaño 1 de la cascada FrugalGPT: el mini agotado escala al modelo
    senior del MISMO perfil, reabre la issue y despierta al agente — una vez."""
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    import aiteam.user_config as config_mod
    monkeypatch.setattr(config_mod, "_codex_catalog_compatibility", lambda _config: {
        "status": "compatible",
        "installed_version": "test",
        "catalog_client_version": "test",
        "models": ["gpt-5.6-sol"],
    })
    record_model_health("codex_subscription", "gpt-5.6-sol", available=True, reason="test")
    db_path = _escalation_db(tmp_path, model="gpt-5.4-mini")
    executor = RunExecutor(db_path, AdapterRegistry([]))

    escalated = executor._attempt_model_escalation(
        issue_id="issue-1", agent_id="agent-1", run_id="run:x",
        agent_role="engineer", liveness_reason="plan_only_exhausted_at_attempt_2",
    )

    assert escalated is True
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        agent = conn.execute("SELECT adapter_type, adapter_config_json FROM agents WHERE id='agent-1'").fetchone()
        issue = conn.execute("SELECT status FROM issues WHERE id='issue-1'").fetchone()
        wake = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE agent_id='agent-1' AND source='model_escalation' AND status='queued'"
        ).fetchone()[0]
    config = json.loads(agent["adapter_config_json"])
    assert agent["adapter_type"] == "subscription_cli", "mismo canal: solo cambia el modelo"
    assert config["model"] == "gpt-5.6-sol"
    assert issue["status"] == "todo"
    assert wake == 1

    # Segunda vez: no re-escala (cap por issue).
    assert executor._attempt_model_escalation(
        issue_id="issue-1", agent_id="agent-1", run_id="run:y",
        agent_role="engineer", liveness_reason="plan_only_exhausted_at_attempt_2",
    ) is False


def test_model_escalation_noop_when_already_on_senior_model(tmp_path: Path) -> None:
    db_path = _escalation_db(tmp_path, model="gpt-5.6-sol")
    executor = RunExecutor(db_path, AdapterRegistry([]))

    assert executor._attempt_model_escalation(
        issue_id="issue-1", agent_id="agent-1", run_id="run:x",
        agent_role="engineer", liveness_reason="plan_only_exhausted_at_attempt_2",
    ) is False, "ya corre el tope del perfil: toca el peldaño 2 (cambio de canal)"


def test_model_escalation_noop_without_profile_id(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type, adapter_config_json) VALUES (?, ?, ?, ?, ?)",
            ("agent-1", "engineer", "E", "subscription_cli", "{}"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) VALUES (?, ?, ?, ?, ?)",
            ("issue-1", "g1", "T", "blocked", "agent-1"),
        )
        conn.commit()
    executor = RunExecutor(db_path, AdapterRegistry([]))

    assert executor._attempt_model_escalation(
        issue_id="issue-1", agent_id="agent-1", run_id="run:x",
        agent_role="engineer", liveness_reason="plan_only_exhausted_at_attempt_2",
    ) is False


# ── Review cross-provider vinculante en criticidad alta ───────────────────────

def _cross_review_db(tmp_path: Path, *, criticality: str = "high") -> Path:
    """Root con engineer y reviewer AMBOS en openai — el juez comparte familia
    con el generador. gemini_api conectado como alternativa."""
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        explicit_openai = json.dumps({
            "profile_id": "openai_api",
            "model": "gpt-5.6-sol",
            "selection_intent": {"mode": "owner_explicit", "source": "test"},
        })
        conn.executemany(
            "INSERT INTO agents (id, role, name, adapter_type, adapter_config_json) VALUES (?, ?, ?, ?, ?)",
            [
                ("role:engineer", "engineer", "E", "openai_api", explicit_openai),
                ("role:reviewer", "reviewer", "R", "openai_api", explicit_openai),
            ],
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) VALUES "
            "('root', 'g1', 'Root', 'in_progress', 'lead', NULL)"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id) VALUES "
            "('issue-eng', 'g1', 'root', 'Impl', 'done', 'engineer', 'role:engineer')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id, criticality) VALUES "
            f"('issue-rev', 'g1', 'root', 'Review', 'in_progress', 'reviewer', 'role:reviewer', '{criticality}')"
        )
        conn.commit()
    return db_path


def test_cross_provider_review_requires_owner_before_repointing(tmp_path: Path, monkeypatch) -> None:
    from aiteam.project_adapters import write_project_adapter_policy
    from aiteam.user_config import store_secret

    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    store_secret(provider="google", name="default", secret="gemini-key")
    for option in model_options().get("gemini_api", []):
        record_model_health(
            "gemini_api", str(option["value"]), available=True, reason="review fixture"
        )
    db_path = _cross_review_db(tmp_path, criticality="high")
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api", "gemini_api"])

    executor = RunExecutor(db_path, build_default_registry())
    moved = executor._enforce_cross_provider_review(
        issue_id="issue-rev", agent_id="role:reviewer", agent_role="reviewer"
    )

    assert moved is True
    assert executor._enforce_cross_provider_review(
        issue_id="issue-rev", agent_id="role:reviewer", agent_role="reviewer"
    ) is True, "una proposal pendiente sigue pausando sin crear otra tarjeta"
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        reviewer = conn.execute("SELECT adapter_type FROM agents WHERE id='role:reviewer'").fetchone()
        interaction = conn.execute(
            "SELECT id, status FROM issue_thread_interactions WHERE issue_id='issue-rev'"
        ).fetchone()
        proposals = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='issue.assignment_change_proposed' AND target_id='issue-rev'"
        ).fetchone()[0]
    assert reviewer["adapter_type"] == "openai_api"
    assert interaction is not None and interaction["status"] == "pending"
    assert proposals == 1

    resolve_interaction(
        db_path,
        interaction_id=str(interaction["id"]),
        action="accept",
        resolved_by_user_id="owner",
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:reviewer")
    assert dispatch is not None
    executor.execute(dispatch)
    with sqlite3.connect(str(db_path)) as conn:
        reviewer = conn.execute("SELECT adapter_type FROM agents WHERE id='role:reviewer'").fetchone()
    assert reviewer[0] == "gemini_api", "solo aceptar puede cambiar la perspectiva del juez"

    # Segunda pasada: ya es cross-provider → no-op.
    assert executor._enforce_cross_provider_review(
        issue_id="issue-rev", agent_id="role:reviewer", agent_role="reviewer"
    ) is False


def test_assignment_change_reject_keeps_issue_blocked_and_assignment_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    from aiteam.project_adapters import write_project_adapter_policy
    from aiteam.user_config import store_secret

    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    store_secret(provider="google", name="default", secret="gemini-key")
    for option in model_options().get("gemini_api", []):
        record_model_health(
            "gemini_api", str(option["value"]), available=True, reason="reject fixture"
        )
    db_path = _cross_review_db(tmp_path, criticality="high")
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api", "gemini_api"])
    executor = RunExecutor(db_path, build_default_registry())
    assert executor._enforce_cross_provider_review(
        issue_id="issue-rev", agent_id="role:reviewer", agent_role="reviewer"
    )
    with sqlite3.connect(str(db_path)) as conn:
        interaction_id = conn.execute(
            "SELECT id FROM issue_thread_interactions WHERE issue_id='issue-rev'"
        ).fetchone()[0]
    resolve_interaction(
        db_path, interaction_id=interaction_id, action="reject", resolved_by_user_id="owner"
    )

    result = executor._resolved_assignment_change_result(
        run={"id": ""},
        agent_id="role:reviewer",
        issue_id="issue-rev",
        ctx={
            "wake_reason": "interaction_resolved",
            "kind": "request_confirmation",
            "interaction_id": interaction_id,
            "action": "reject",
        },
    )

    assert result is not None and result.status == "skipped"
    with sqlite3.connect(str(db_path)) as conn:
        adapter = conn.execute("SELECT adapter_type FROM agents WHERE id='role:reviewer'").fetchone()[0]
        status = conn.execute("SELECT status FROM issues WHERE id='issue-rev'").fetchone()[0]
    assert adapter == "openai_api"
    assert status == "blocked"


def test_assignment_change_preserves_newer_valid_owner_override(
    tmp_path: Path, monkeypatch
) -> None:
    from aiteam.model_selection_context import contextual_model_selection
    from aiteam.project_adapters import write_project_adapter_policy
    from aiteam.user_config import load_adapter_profiles, store_secret

    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    store_secret(provider="google", name="default", secret="gemini-key")
    gemini_models = [str(option["value"]) for option in model_options().get("gemini_api", [])]
    for model in gemini_models:
        record_model_health("gemini_api", model, available=True, reason="override fixture")
    db_path = _cross_review_db(tmp_path, criticality="high")
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api", "gemini_api"])
    executor = RunExecutor(db_path, build_default_registry())
    assert executor._enforce_cross_provider_review(
        issue_id="issue-rev", agent_id="role:reviewer", agent_role="reviewer"
    )
    with sqlite3.connect(str(db_path)) as conn:
        interaction = conn.execute(
            "SELECT id, payload_json FROM issue_thread_interactions WHERE issue_id='issue-rev'"
        ).fetchone()
        proposed_model = json.loads(interaction[1])["proposed"]["model"]
        override_model = next(
            str((candidate.get("identity") or {}).get("model_id") or "")
            for candidate in contextual_model_selection(
                db_path,
                role="reviewer",
                issue_id="issue-rev",
                profiles=[
                    profile for profile in load_adapter_profiles()
                    if profile.get("id") in {"openai_api", "gemini_api"}
                ],
            ).get("candidates") or ()
            if candidate.get("owner_selectable") is True
            and str((candidate.get("identity") or {}).get("profile_id") or "") == "gemini_api"
            and str((candidate.get("identity") or {}).get("model_id") or "") != proposed_model
        )
        conn.execute(
            "UPDATE agents SET adapter_type='gemini_api', adapter_config_json=? WHERE id='role:reviewer'",
            (json.dumps({
                "profile_id": "gemini_api",
                "model": override_model,
                "selection_intent": {"mode": "owner_explicit", "source": "team_editor"},
            }),),
        )
        conn.commit()
    interaction_id = str(interaction[0])
    resolve_interaction(
        db_path, interaction_id=interaction_id, action="accept", resolved_by_user_id="owner"
    )

    result = executor._resolved_assignment_change_result(
        run={"id": ""},
        agent_id="role:reviewer",
        issue_id="issue-rev",
        ctx={
            "wake_reason": "interaction_resolved",
            "kind": "request_confirmation",
            "interaction_id": interaction_id,
            "action": "accept",
        },
    )

    assert result is not None and "manual_override_preserved" in str(result.output)
    with sqlite3.connect(str(db_path)) as conn:
        config = json.loads(conn.execute(
            "SELECT adapter_config_json FROM agents WHERE id='role:reviewer'"
        ).fetchone()[0])
        status = conn.execute("SELECT status FROM issues WHERE id='issue-rev'").fetchone()[0]
    assert config["model"] == override_model
    assert status == "todo"


def test_assignment_change_rejects_owner_alternative_that_breaks_diversity_gate(
    tmp_path: Path, monkeypatch
) -> None:
    from aiteam.model_selection_context import contextual_model_selection
    from aiteam.project_adapters import write_project_adapter_policy
    from aiteam.user_config import load_adapter_profiles, store_secret

    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    store_secret(provider="google", name="default", secret="gemini-key")
    for profile_id in ("openai_api", "gemini_api"):
        for option in model_options().get(profile_id, []):
            record_model_health(
                profile_id, str(option["value"]), available=True, reason="diversity fixture"
            )
    db_path = _cross_review_db(tmp_path, criticality="high")
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api", "gemini_api"])
    executor = RunExecutor(db_path, build_default_registry())
    assert executor._enforce_cross_provider_review(
        issue_id="issue-rev", agent_id="role:reviewer", agent_role="reviewer"
    )
    same_perspective = next(
        candidate
        for candidate in contextual_model_selection(
            db_path,
            role="reviewer",
            issue_id="issue-rev",
            profiles=[
                profile for profile in load_adapter_profiles()
                if profile.get("id") in {"openai_api", "gemini_api"}
            ],
        ).get("candidates") or ()
        if candidate.get("owner_selectable") is True
        and str((candidate.get("identity") or {}).get("profile_id") or "") == "openai_api"
    )
    with sqlite3.connect(str(db_path)) as conn:
        interaction_id = conn.execute(
            "SELECT id FROM issue_thread_interactions WHERE issue_id='issue-rev'"
        ).fetchone()[0]
    identity = same_perspective["identity"]
    resolve_interaction(
        db_path,
        interaction_id=interaction_id,
        action="accept",
        resolution_data={
            "model_selection": {
                "candidateId": same_perspective["candidate_id"],
                "profileId": identity["profile_id"],
                "model": identity["model_id"],
            }
        },
        resolved_by_user_id="owner",
    )

    result = executor._resolved_assignment_change_result(
        run={"id": ""},
        agent_id="role:reviewer",
        issue_id="issue-rev",
        ctx={
            "wake_reason": "interaction_resolved",
            "kind": "request_confirmation",
            "interaction_id": interaction_id,
            "action": "accept",
        },
    )

    assert result is not None and result.error_code == "assignment_change_selection_stale"
    with sqlite3.connect(str(db_path)) as conn:
        adapter = conn.execute("SELECT adapter_type FROM agents WHERE id='role:reviewer'").fetchone()[0]
        status = conn.execute("SELECT status FROM issues WHERE id='issue-rev'").fetchone()[0]
    assert adapter == "openai_api"
    assert status == "blocked"


def test_cross_provider_review_ignores_normal_criticality(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    db_path = _cross_review_db(tmp_path, criticality="medium")

    executor = RunExecutor(db_path, AdapterRegistry([]))
    assert executor._enforce_cross_provider_review(
        issue_id="issue-rev", agent_id="role:reviewer", agent_role="reviewer"
    ) is False


def test_cross_provider_review_noop_without_alternative_provider(tmp_path: Path, monkeypatch) -> None:
    """Sin proveedor alternativo conectado: queda la señal informativa, el
    review NUNCA se bloquea por esto."""
    from aiteam.project_adapters import write_project_adapter_policy

    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    db_path = _cross_review_db(tmp_path, criticality="critical")
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api"])  # solo la familia del engineer

    executor = RunExecutor(db_path, AdapterRegistry([]))
    assert executor._enforce_cross_provider_review(
        issue_id="issue-rev", agent_id="role:reviewer", agent_role="reviewer"
    ) is False


def test_cross_provider_review_can_be_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_CROSS_PROVIDER_REVIEW", "0")
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    db_path = _cross_review_db(tmp_path, criticality="critical")

    executor = RunExecutor(db_path, AdapterRegistry([]))
    assert executor._enforce_cross_provider_review(
        issue_id="issue-rev", agent_id="role:reviewer", agent_role="reviewer"
    ) is False


def test_file_ops_accepts_content_as_body_alias(tmp_path: Path) -> None:
    """Cazado por el canario e2e: un op con 'content' en vez de 'body'
    escribía un archivo VACIO en silencio."""
    from aiteam.heartbeat.executor import _execute_file_ops

    touched = _execute_file_ops(
        [{"op": "write_file", "path": "x.py", "content": "print('hola')\n"}],
        tmp_path,
    )

    assert touched == ["x.py"]
    assert (tmp_path / "x.py").read_text(encoding="utf-8") == "print('hola')\n"


# ── El cierre de un hijo SIEMPRE reporta al padre (visto en CLI Tareas) ───────

def test_scout_closing_done_auto_reports_to_supervisor(tmp_path: Path) -> None:
    """El file_scout de verificación final cerró done sin notify_supervisor y
    el Lead nunca despertó: el auto-report debe cubrir TODOS los roles no-lead,
    no solo engineer/reviewer/qa."""
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type, supervisor_agent_id) VALUES "
            "('role:lead', 'lead', 'L', 'subscription_cli', NULL),"
            "('role:scout', 'file_scout', 'S', 'subscription_cli', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) VALUES "
            "('root', 'g1', 'Root', 'in_progress', 'lead', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id) VALUES "
            "('scout-issue', 'g1', 'root', 'Verificar', 'in_progress', 'file_scout', 'role:scout')"
        )
        conn.commit()

    class _ScoutRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            # Cierra con evidencia válida pero SIN notify_supervisor — como el LLM real.
            return ExecutionResult(
                status="completed",
                output="Verificado: archivos presentes y con contenido.",
                actions={
                    "issue_status": "done",
                    "add_comments": [
                        "Verificado.\n---AGENT-REPORT---\n"
                        "role: file_scout\nresult: done\nissue_status: done\n"
                        "next_owner: lead\nblocker: none\nevidence: archivos presentes"
                    ],
                },
            )

    executor = RunExecutor(db_path, AdapterRegistry([_ScoutRuntime()]))
    enqueue_wakeup(
        db_path, agent_id="role:scout", source="assignment", reason="new_issue",
        payload={"issue_id": "scout-issue", "wake_reason": "new_issue"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:scout")
    assert dispatch is not None
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        lead_wake = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE agent_id='role:lead' AND status='queued'"
        ).fetchone()[0]
    assert lead_wake == 1, "el cierre de un hijo debe despertar al padre SIEMPRE, sin depender del LLM"


def test_scout_close_without_agent_report_retries_once_then_blocks(tmp_path: Path) -> None:
    """Un summary no es evidencia: se corrige una vez y después se escala."""
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type, supervisor_agent_id) VALUES "
            "('role:lead', 'lead', 'L', 'subscription_cli', NULL),"
            "('role:scout', 'file_scout', 'S', 'subscription_cli', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) VALUES "
            "('root', 'g1', 'Root', 'in_progress', 'lead', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id) VALUES "
            "('scout-issue', 'g1', 'root', 'Verificar', 'in_progress', 'file_scout', 'role:scout')"
        )
        conn.commit()

    class _ReportlessScoutRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(
                status="completed",
                output="Verificado, cierro.",
                actions={"issue_status": "done"},
            )

    executor = RunExecutor(db_path, AdapterRegistry([_ReportlessScoutRuntime()]))
    enqueue_wakeup(
        db_path, agent_id="role:scout", source="assignment", reason="new_issue",
        payload={"issue_id": "scout-issue", "wake_reason": "new_issue"},
    )

    first = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:scout")
    assert first is not None
    executor.execute(first)
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT status FROM issues WHERE id='scout-issue'").fetchone()[0] == "todo"
        retry = conn.execute(
            "SELECT payload_json FROM wakeup_requests "
            "WHERE agent_id='role:scout' AND reason='tier3_report_retry' AND status='queued'"
        ).fetchone()
        lead_wakes = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE agent_id='role:lead' AND status='queued'"
        ).fetchone()[0]
    assert retry is not None
    assert json.loads(retry[0])["wake_reason"] == "tier3_report_retry"
    assert lead_wakes == 0

    second = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:scout")
    assert second is not None
    executor.execute(second)
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT status FROM issues WHERE id='scout-issue'").fetchone()[0] == "blocked"
        missing_count = conn.execute(
            "SELECT COUNT(*) FROM activity_log "
            "WHERE action='tier3.agent_report_missing' AND target_id='scout-issue'"
        ).fetchone()[0]
        lead_wakes = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE agent_id='role:lead' AND status='queued'"
        ).fetchone()[0]
    assert missing_count == 2
    assert lead_wakes == 1


# ── P3: métricas de calidad deterministas en el runner builtin ────────────────

def test_builtin_runner_records_coverage_and_lint_metrics(tmp_path: Path) -> None:
    """La evidencia gana cobertura y lint medidos por subprocess — números que
    ningún agente puede alucinar, persistidos en el raw_json del report."""
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    _add_test_runner_child(db_path)
    _write_pytest_suite(tmp_path, passing=True)
    (tmp_path / "modulo.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    _gate_dispatch_one(
        tmp_path, agent_id="role:test_runner", issue_id="issue:testrun",
        runtime=_MustNotExecuteRuntime(),
    )

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        report = conn.execute(
            "SELECT evidence, raw_json FROM agent_reports WHERE issue_id='issue:testrun' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert report is not None
    raw = json.loads(report["raw_json"])
    assert "coverage 100%" in report["evidence"] or "coverage" in report["evidence"]
    assert "coverage_percent" in raw, "la métrica debe persistir estructurada, no solo como texto"
    assert "lint_issues" in raw
    assert "exit 0" in report["evidence"]


def test_builtin_runner_blocks_below_coverage_threshold(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_MIN_COVERAGE_PERCENT", "101")  # inalcanzable
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)
    _add_test_runner_child(db_path)
    _write_pytest_suite(tmp_path, passing=True)

    _gate_dispatch_one(
        tmp_path, agent_id="role:test_runner", issue_id="issue:testrun",
        runtime=_MustNotExecuteRuntime(),
    )

    assert _issue_status(db_path, "issue:testrun") == "blocked"
    with sqlite3.connect(str(db_path)) as conn:
        report = conn.execute(
            "SELECT result, blocker FROM agent_reports WHERE issue_id='issue:testrun' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert report[0] == "failed"
    assert "cobertura" in report[1]


def test_fix_titled_delegation_to_readonly_role_rejected(tmp_path: Path) -> None:
    """CLI Textos en vivo: 'Fix: corregir stats...' delegado a file_scout pasó
    el guard (la señal iba en el TITULO y sin 'Files to modify'), el scout
    cerró done sin tocar nada y el review quemó 4 rondas contra un fix
    inexistente."""
    db_path = tmp_path / "aiteam.db"
    _make_circuit_breaker_db(db_path)

    class _FixToScoutLead:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(
                status="completed",
                output="delego",
                actions={
                    "create_issues": [{
                        "title": "Fix: corregir stats y aportar evidencia pytest real",
                        "description": "Confirmar que el comando stats cumple la especificacion y aportar evidencia.",
                        "role": "file_scout",
                        "complexity": "low",
                    }]
                },
            )

    executor = RunExecutor(db_path, AdapterRegistry([_FixToScoutLead()]))
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="manual", reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        created = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE parent_id='issue:intake' AND role='file_scout'"
        ).fetchone()[0]
        rejection = conn.execute(
            "SELECT COUNT(*) FROM issue_comments WHERE author_user_id='system' AND body LIKE '%solo lectura%'"
        ).fetchone()[0]
    assert created == 0, "un titulo con 'Fix:' es intencion de edicion — rol read-only rechazado"
    assert rejection == 1, "el rechazo debe dejar comentario correctivo para el Lead"
