"""Tests for subscription_cli_adapter — Codex path, helpers, and reconcile upgrade."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from aiteam.adapters.subscription_cli_adapter import (
    CODEX_OUTPUT_SCHEMA,
    ClaudeSubscriptionCliRuntime,
    _build_codex_prompt,
    _build_system_prompt,
    _command_context,
    _claude_mcp_config,
    _extract_opencode_usage,
    _parse_antigravity_output,
    _parse_codex_output,
    _parse_opencode_output,
    _opencode_inline_config,
    _resolve_cli_cmd,
)
from aiteam.adapters.registry import AdapterDescriptor
from aiteam.adapters.work_contract import parse_submit_work
from aiteam.db.migration import SCHEMA_PATH
from aiteam.project_adapters import reconcile_project_agent_policy
from aiteam.user_config import record_model_health


# ---------------------------------------------------------------------------
# _resolve_cli_cmd
# ---------------------------------------------------------------------------


class TestResolveCLICmd:
    def test_returns_cmd_shim_on_windows(self, monkeypatch):
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(
            "aiteam.adapters.subscription_cli_adapter.shutil.which",
            lambda name: f"C:/npm/{name}" if name.endswith(".cmd") else None,
        )
        result = _resolve_cli_cmd("codex")
        assert result == "C:/npm/codex.cmd"

    def test_falls_back_to_plain_which_on_windows(self, monkeypatch):
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(
            "aiteam.adapters.subscription_cli_adapter.shutil.which",
            lambda name: "C:/bin/codex" if name == "codex" else None,
        )
        result = _resolve_cli_cmd("codex")
        assert result == "C:/bin/codex"

    def test_returns_plain_which_on_posix(self, monkeypatch):
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(
            "aiteam.adapters.subscription_cli_adapter.shutil.which",
            lambda name: f"/usr/bin/{name}" if name == "codex" else None,
        )
        result = _resolve_cli_cmd("codex")
        assert result == "/usr/bin/codex"

    def test_returns_name_itself_when_not_found(self, monkeypatch):
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr("aiteam.adapters.subscription_cli_adapter.shutil.which", lambda _: None)
        result = _resolve_cli_cmd("codex")
        assert result == "codex"

    def test_resolves_antigravity_from_local_app_data(self, tmp_path, monkeypatch):
        binary = tmp_path / "agy" / "bin" / "agy.exe"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"")
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setattr("aiteam.adapters.subscription_cli_adapter.shutil.which", lambda _: None)

        assert _resolve_cli_cmd("agy") == str(binary)

    def test_already_resolved_exe_not_re_resolved(self, monkeypatch):
        """A name that already ends with .cmd is not double-resolved."""
        monkeypatch.setattr(os, "name", "nt")
        calls: list[str] = []
        def fake_which(name: str) -> str | None:
            calls.append(name)
            return f"C:/npm/{name}"
        monkeypatch.setattr("aiteam.adapters.subscription_cli_adapter.shutil.which", fake_which)
        result = _resolve_cli_cmd("codex.cmd")
        assert result == "C:/npm/codex.cmd"
        assert all(".cmd.cmd" not in c for c in calls)


def test_antigravity_command_uses_headless_plan_contract(tmp_path, monkeypatch):
    binary = tmp_path / "agy.exe"
    binary.write_bytes(b"")
    runtime = ClaudeSubscriptionCliRuntime(
        AdapterDescriptor(adapter_type="subscription_cli", channel="subscription", provider="google-antigravity"),
        command=[str(binary)],
        cli_kind="antigravity",
        model="gemini-3.1-pro-high",
        timeout_sec=90,
    )

    command = runtime._build_claude_command("SYSTEM", "USER")

    assert command[0] == str(binary)
    assert command[1:4] == ["--new-project", "--print", "SYSTEM\n\nUSER\n\nReturn ONLY the submit_work JSON object required by the contract. Do not wrap it in Markdown."]
    assert ["--mode", "plan"] == command[4:6]
    assert "--sandbox" in command
    assert "--dangerously-skip-permissions" in command
    assert command[-2:] == ["--model", "gemini-3.1-pro-high"]


def test_opencode_command_uses_exact_free_model_and_read_only_policy(tmp_path):
    runtime = ClaudeSubscriptionCliRuntime(
        AdapterDescriptor(adapter_type="subscription_cli", channel="free_gateway", provider="opencode-zen"),
        command=[str(tmp_path / "opencode.cmd")],
        cli_kind="opencode",
        model="opencode/nemotron-3-ultra-free",
    )
    env = {"AITEAM_AGENT_ROLE": "lead", "AITEAM_WAKE_PAYLOAD_JSON": '{"objective":"plan"}'}

    with _command_context(runtime, env, {"issue_id": "issue:free"}, effective_cwd=str(tmp_path)) as spec:
        command = spec["command"]
        policy = json.loads(spec["env_updates"]["OPENCODE_CONFIG_CONTENT"])
        assert command[1:4] == ["run", "--format", "json"]
        assert "--auto" not in command
        assert command[command.index("--model") + 1] == "opencode/nemotron-3-ultra-free"
        assert Path(command[command.index("--file") + 1]).is_file()
        assert command.index("--file") > command.index(
            "Follow the attached AI Teams contract. Return one JSON object with exactly "
            "the top-level keys status, summary, and ops; return no Markdown or other text."
        )
        assert policy["share"] == "disabled"
        assert policy["permission"]["read"] == "allow"
        assert policy["permission"]["edit"] == "deny"
        assert policy["permission"]["bash"] == "deny"


def test_opencode_mcp_config_enforces_exact_owner_allowlist_without_secrets():
    config = _opencode_inline_config([{
        "name": "docs",
        "command": "C:/tools/docs-mcp.exe",
        "args": ["--stdio"],
        "env_required": ["DOCS_TOKEN"],
        "enabled_tools": ["lookup"],
        "denied_tools": ["publish"],
    }])

    assert config["mcp"]["docs"]["command"] == ["C:/tools/docs-mcp.exe", "--stdio"]
    assert config["mcp"]["docs"]["environment"] == {"DOCS_TOKEN": "{env:DOCS_TOKEN}"}
    assert config["permission"]["docs_*"] == "deny"
    assert config["permission"]["docs_lookup"] == "allow"
    assert "docs_publish" not in config["permission"]
    assert "secret-value" not in json.dumps(config)


def test_parse_opencode_json_events_recovers_submit_work():
    payload = {"status": "completed", "summary": "ok", "add_comment": "", "ops": []}
    raw = "\n".join([
        json.dumps({"type": "step_start"}),
        json.dumps({"type": "text", "part": {"text": json.dumps(payload)}}),
    ])

    assert _parse_opencode_output(raw) == payload


def test_extract_opencode_usage_sums_step_finish_events_and_keeps_session():
    raw = "\n".join([
        json.dumps({
            "type": "step_finish",
            "sessionID": "ses_opencode_123",
            "part": {
                "type": "step-finish",
                "cost": 0,
                "tokens": {
                    "total": 130,
                    "input": 100,
                    "output": 25,
                    "reasoning": 5,
                    "cache": {"read": 40, "write": 3},
                },
            },
        }),
        json.dumps({
            "type": "step_finish",
            "sessionID": "ses_opencode_123",
            "part": {
                "type": "step-finish",
                "cost": 0,
                "tokens": {
                    "total": 13,
                    "input": 10,
                    "output": 3,
                    "reasoning": 0,
                    "cache": {"read": 2, "write": 0},
                },
            },
        }),
    ])

    assert _extract_opencode_usage(raw) == {
        "input_tokens": 110,
        "output_tokens": 28,
        "reasoning_output_tokens": 5,
        "cached_input_tokens": 42,
        "cache_write_tokens": 3,
        "total_tokens": 143,
        "provider_session_id": "ses_opencode_123",
    }


def test_extract_opencode_usage_derives_total_without_double_counting_cache_or_reasoning():
    raw = json.dumps({
        "type": "step_finish",
        "part": {
            "type": "step-finish",
            "tokens": {
                "input": 80,
                "output": 20,
                "reasoning": 7,
                "cache": {"read": 50, "write": 4},
            },
        },
    })

    assert _extract_opencode_usage(raw)["total_tokens"] == 100


def test_codex_command_injects_ephemeral_mcp_without_secret_values():
    runtime = ClaudeSubscriptionCliRuntime(
        AdapterDescriptor(
            adapter_type="subscription_cli", channel="subscription", provider="openai-codex"
        ),
        cli_kind="codex",
    )

    command = runtime._build_codex_command(
        "task",
        schema_path="/schema.json",
        output_path="/out.json",
        effective_cwd=None,
        mcp_servers=[{
            "name": "docs",
            "command": "C:/tools/docs-mcp.exe",
            "args": ["--stdio"],
            "version": "1.2.3",
            "enabled_tools": ["lookup"],
        }],
    )

    overrides = [command[index + 1] for index, item in enumerate(command[:-1]) if item == "-c"]
    assert 'mcp_servers.docs.command="C:/tools/docs-mcp.exe"' in overrides
    assert 'mcp_servers.docs.args=["--stdio"]' in overrides
    assert 'mcp_servers.docs.enabled_tools=["lookup"]' in overrides
    assert all("SECRET" not in item for item in command)


def test_claude_command_uses_strict_ephemeral_mcp_config_without_secret_values(tmp_path):
    runtime = ClaudeSubscriptionCliRuntime(
        AdapterDescriptor(
            adapter_type="subscription_cli", channel="subscription", provider="anthropic"
        ),
        cli_kind="claude",
    )
    servers = [{
        "name": "docs",
        "command": "C:/tools/docs-mcp.exe",
        "args": ["--stdio"],
        "version": "1.2.3",
        "env_required": ["DOCS_TOKEN"],
        "enabled_tools": ["lookup"],
        "denied_tools": ["publish"],
    }]
    config = _claude_mcp_config(servers)
    assert config["mcpServers"]["docs"]["env"] == {"DOCS_TOKEN": "${DOCS_TOKEN}"}

    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    command = runtime._build_claude_command(
        "SYSTEM", "USER", mcp_config_path=str(config_path), mcp_servers=servers
    )
    assert "--strict-mcp-config" in command
    assert command[command.index("--mcp-config") + 1] == str(config_path)
    assert "mcp__docs__publish" in command
    assert all("secret-value" not in item for item in command)


def test_antigravity_context_relays_large_prompt_through_ephemeral_file(tmp_path):
    runtime = ClaudeSubscriptionCliRuntime(
        AdapterDescriptor(adapter_type="subscription_cli", channel="subscription", provider="google-antigravity"),
        command=[str(tmp_path / "agy.exe")],
        cli_kind="antigravity",
    )
    env = {
        "AITEAM_AGENT_ROLE": "quorum_auditor",
        "AITEAM_AGENT_SKILL": "S" * 40_000,
        "AITEAM_WAKE_PAYLOAD_JSON": json.dumps({"plan": "P" * 40_000}),
    }

    with _command_context(runtime, env, {"issue_id": "issue:q"}, effective_cwd=str(tmp_path)) as spec:
        command = spec["command"]
        relay = command[command.index("--print") + 1]
        prompt_path = Path(relay.split("from ", 1)[1].split(" and follow", 1)[0])
        assert len(" ".join(command)) < 2_000
        assert prompt_path.is_file()
        assert len(prompt_path.read_text(encoding="utf-8")) > 80_000
        assert command[-2] == "--add-dir"
        assert Path(command[-1]) == prompt_path.parent

    assert not prompt_path.exists()


def test_antigravity_read_only_role_executes_outside_workspace(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    runtime = ClaudeSubscriptionCliRuntime(
        AdapterDescriptor(
            adapter_type="subscription_cli",
            channel="subscription",
            provider="google-antigravity",
        ),
        command=[str(tmp_path / "agy.exe")],
        cli_kind="antigravity",
        sandbox="read-only",
    )
    env = {
        "AITEAM_AGENT_ROLE": "lead",
        "AITEAM_WAKE_PAYLOAD_JSON": json.dumps({"workspace_files": []}),
    }

    with _command_context(runtime, env, {"issue_id": "issue:lead"}, effective_cwd=str(workspace)) as spec:
        assert Path(spec["cwd"]) != workspace
        assert Path(spec["cwd"]).is_dir()


def test_submit_work_parser_skips_non_json_braces_before_valid_object():
    raw = 'agy log {not json}\n' + json.dumps({
        "status": "completed", "summary": "ok", "ops": []
    }) + '\ntelemetry {also not json}'

    parsed = parse_submit_work(raw)

    assert parsed["status"] == "completed"


def test_submit_work_parser_invalid_braces_fail_without_recursion():
    with pytest.raises(ValueError, match="submit_work JSON object not found"):
        parse_submit_work("prefix {not valid} suffix")


def test_antigravity_parser_fills_transport_fields_without_changing_ops():
    ops = [{"type": "add_comment", "body": "---AGENT-REPORT---\nresult: passed"}]

    parsed = _parse_antigravity_output(json.dumps({"ops": ops}))

    assert parsed["status"] == "completed"
    assert parsed["ops"] == ops


def test_antigravity_parser_normalizes_observed_submit_work_body():
    body = "---AGENT-REPORT---\nresult: passed_with_findings"

    parsed = _parse_antigravity_output(json.dumps({"type": "submit_work", "body": body}))

    assert parsed == {
        "status": "completed",
        "summary": "Antigravity submit_work completed",
        "ops": [{"type": "add_comment", "body": body}],
    }


def test_antigravity_parser_normalizes_observed_text_envelope_with_preamble():
    work = {
        "status": "completed",
        "summary": "implemented",
        "ops": [
            {"type": "write_file", "path": "conversor.py", "body": "VALUE = 1\n"},
            {"type": "set_status", "status": "done"},
        ],
    }
    raw = json.dumps({"text": "I will now submit the files:\n" + json.dumps(work)})

    parsed = _parse_antigravity_output(raw)

    assert parsed == work


def test_antigravity_parser_prefers_top_level_ops_over_report_text():
    ops = [
        {"type": "write_file", "path": "conversor.py", "body": "VALUE = 1\n"},
        {"type": "set_status", "status": "done"},
    ]
    raw = json.dumps({"text": "---AGENT-REPORT---\nresult: done", "ops": ops})

    parsed = _parse_antigravity_output(raw)

    assert parsed == {
        "text": "---AGENT-REPORT---\nresult: done",
        "ops": ops,
        "status": "completed",
        "summary": "Antigravity submit_work completed",
    }


def test_antigravity_parser_normalizes_ops_envelope_with_trailing_transport_noise():
    ops = [{"type": "write_file", "path": "conversor.py", "body": "VALUE = 1\n"}]
    raw = json.dumps({"text": "report", "ops": ops}) + "\nagy transport closed"

    parsed = _parse_antigravity_output(raw)

    assert parsed["ops"] == ops
    assert parsed["status"] == "completed"
    assert parsed["summary"] == "Antigravity submit_work completed"


def test_antigravity_parser_rejects_unstructured_free_text():
    with pytest.raises(ValueError, match="submit_work JSON object not found"):
        _parse_antigravity_output("plain response without contract")


def test_quorum_auditor_system_prompt_enforces_gate_vocabulary_and_authority():
    prompt = _build_system_prompt({
        "AITEAM_AGENT_ROLE": "quorum_auditor",
        "AITEAM_AGENT_SKILL": "Revisa el plan.",
    })

    assert "result: approved|changes_requested|blocked" in prompt
    assert "NO uses accept_quorum_synthesis" in prompt
    assert "result: pass, passed o passed_with_findings NO son válidos" in prompt
    assert "---QUORUM-AUDIT---" in prompt


def test_codex_quorum_auditor_prompt_treats_lead_as_owner_and_forbids_implementation():
    prompt = _build_codex_prompt(
        {
            "AITEAM_AGENT_ROLE": "quorum_auditor",
            "AITEAM_AGENT_SKILL": "Audita el plan.",
            "AITEAM_WAKE_PAYLOAD_JSON": json.dumps({"quorum_review": {"plan": {"body": "A"}}}),
        },
        {"issue_id": "issue:q"},
    )

    assert "Lead real del proyecto" in prompt
    assert "No implementes, no edites archivos" in prompt
    assert "---QUORUM-AUDIT---" in prompt


def test_user_directives_are_presented_after_project_skill_and_override_it():
    marker = "SKILL-LOCAL: usa siempre la opción A"
    prompt = _build_codex_prompt(
        {
            "AITEAM_AGENT_ROLE": "engineer",
            "AITEAM_AGENT_SKILL": marker,
            "AITEAM_WAKE_PAYLOAD_JSON": json.dumps({
                "user_directives": [{"decision": "Usa la opción B", "user_note": "B"}]
            }),
        },
        {"issue_id": "issue:precedence"},
    )

    directive_heading = "=== Directivas del usuario (payload.user_directives) ==="
    assert prompt.index(marker) < prompt.index(directive_heading)
    assert "prevalecen sobre cualquier estándar o criterio anterior" in prompt

# ---------------------------------------------------------------------------
# _parse_codex_output
# ---------------------------------------------------------------------------


class TestParseCodexOutput:
    def test_parses_flat_json_string(self):
        raw = json.dumps({"status": "completed", "summary": "done", "add_comment": ""})
        result = _parse_codex_output(raw)
        assert result["status"] == "completed"
        assert result["summary"] == "done"

    def test_parses_dict_directly(self):
        d = {"status": "failed", "summary": "error", "add_comment": "details"}
        result = _parse_codex_output(d)
        assert result["status"] == "failed"

    def test_extracts_json_from_mixed_output(self):
        raw = 'some noise\n{"status":"skipped","summary":"nothing to do","add_comment":""}\nmore noise'
        result = _parse_codex_output(raw)
        assert result["status"] == "skipped"

    def test_accepts_full_submit_work_schema(self):
        """Full ops-based schema is also accepted (forward compat)."""
        d = {
            "status": "completed",
            "summary": "wrote file",
            "ops": [{"type": "add_comment", "body": "done"}],
        }
        result = _parse_codex_output(d)
        assert result["status"] == "completed"
        assert isinstance(result["ops"], list)

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError, match="empty codex output"):
            _parse_codex_output("")

    def test_raises_on_invalid_json(self):
        with pytest.raises(ValueError):
            _parse_codex_output("not json at all, no braces")

    def test_raises_on_dict_missing_status(self):
        with pytest.raises(ValueError):
            _parse_codex_output({"foo": "bar"})

    def test_unwraps_nested_result_key(self):
        d = {"result": {"status": "completed", "summary": "ok", "add_comment": ""}}
        result = _parse_codex_output(d)
        assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# _build_codex_command — flag correctness
# ---------------------------------------------------------------------------


def _make_runtime(*, model: str | None = None, oss: bool = False, local_provider: str | None = None, cwd: Path | None = None) -> ClaudeSubscriptionCliRuntime:
    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")
    return ClaudeSubscriptionCliRuntime(
        descriptor=descriptor,
        cli_kind="codex",
        command=["codex"],
        model=model,
        oss=oss,
        local_provider=local_provider,
        cwd=cwd,
    )


class TestReadOnlySandboxForOrchestrators:
    def test_lead_codex_run_uses_read_only_sandbox(self, tmp_path: Path, monkeypatch: Any) -> None:
        """A lead running the codex CLI must get --sandbox read-only so it can't
        edit files — forcing it to delegate."""
        from aiteam.adapters.registry import AdapterRegistry
        from aiteam.db.wakeups import enqueue_wakeup
        from aiteam.heartbeat.executor import RunExecutor
        from aiteam.heartbeat.scheduler import HeartbeatScheduler

        db = tmp_path / "aiteam.db"
        with sqlite3.connect(str(db)) as conn:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO goals (id, title) VALUES ('g1','G')")
            conn.execute(
                "INSERT INTO agents (id, role, name, adapter_type, adapter_config_json) "
                "VALUES ('role:lead','lead','Lead','subscription_cli', ?)",
                (json.dumps({"cli_kind": "codex", "command": ["codex"], "sandbox": "workspace-write"}),),
            )
            conn.execute(
                "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) "
                "VALUES ('issue:intake','g1','T','in_progress','lead','role:lead')"
            )
            conn.commit()

        captured: dict[str, Any] = {}

        def fake_run(*args: Any, **kwargs: Any):
            captured["command"] = args[0] if args else kwargs.get("args")
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = json.dumps({"status": "completed", "summary": "ok", "add_comment": "", "ops": []})
            proc.stderr = ""
            return proc

        registry = AdapterRegistry.__new__(AdapterRegistry)
        from aiteam.adapters.registry import build_default_registry
        registry = build_default_registry()
        executor = RunExecutor(db, registry)
        enqueue_wakeup(db, agent_id="role:lead", source="manual", reason="manual",
                       payload={"issue_id": "issue:intake", "wake_reason": "manual"})
        dispatch = HeartbeatScheduler(db).dispatch_next(agent_id="role:lead")
        assert dispatch is not None
        with patch("aiteam.adapters.subscription_cli_adapter.subprocess.run", side_effect=fake_run):
            executor.execute(dispatch)

        cmd = captured.get("command") or []
        assert "--sandbox" in cmd
        assert cmd[cmd.index("--sandbox") + 1] == "read-only"

    def test_solo_lead_codex_run_keeps_workspace_write_sandbox(self, tmp_path: Path) -> None:
        from aiteam.adapters.registry import build_default_registry
        from aiteam.db.wakeups import enqueue_wakeup
        from aiteam.heartbeat.executor import RunExecutor
        from aiteam.heartbeat.scheduler import HeartbeatScheduler

        db = tmp_path / "aiteam.db"
        with sqlite3.connect(str(db)) as conn:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO goals (id, title) VALUES ('g1','G')")
            conn.execute(
                "INSERT INTO agents (id, role, name, adapter_type, adapter_config_json) "
                "VALUES ('role:lead','lead','Lead','subscription_cli', ?)",
                (json.dumps({"cli_kind": "codex", "command": ["codex"], "sandbox": "workspace-write"}),),
            )
            conn.execute(
                "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id, metadata_json) "
                "VALUES ('issue:intake','g1','T','in_progress','lead','role:lead','{\"profile\":\"solo_lead\"}')"
            )
            conn.commit()

        captured: dict[str, Any] = {}

        def fake_run(*args: Any, **kwargs: Any):
            captured["command"] = args[0] if args else kwargs.get("args")
            proc = MagicMock(returncode=0, stderr="")
            proc.stdout = json.dumps({"status": "completed", "summary": "ok", "add_comment": "", "ops": []})
            return proc

        enqueue_wakeup(
            db, agent_id="role:lead", source="manual", reason="manual",
            payload={"issue_id": "issue:intake", "wake_reason": "manual", "profile": "solo_lead"},
        )
        dispatch = HeartbeatScheduler(db).dispatch_next(agent_id="role:lead")
        assert dispatch is not None
        with patch("aiteam.adapters.subscription_cli_adapter.subprocess.run", side_effect=fake_run):
            RunExecutor(db, build_default_registry()).execute(dispatch)

        cmd = captured["command"]
        assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"


class TestCodexOutputSchema:
    def test_schema_includes_ops_so_agents_can_delegate(self):
        assert "ops" in CODEX_OUTPUT_SCHEMA["properties"]
        assert "ops" in CODEX_OUTPUT_SCHEMA["required"]
        item = CODEX_OUTPUT_SCHEMA["properties"]["ops"]["items"]
        assert "create_issue" in item["properties"]["type"]["enum"]
        assert "set_status" in item["properties"]["type"]["enum"]


class TestBuildCodexPrompt:
    def _env(self, role: str, *, skill: str = "") -> dict[str, str]:
        return {
            "AITEAM_AGENT_ROLE": role,
            "AITEAM_AGENT_SKILL": skill,
            "AITEAM_WORKSPACE_ROOT": "/ws",
            "AITEAM_TASK_ID": "issue:x",
            "AITEAM_WAKE_PAYLOAD_JSON": '{"issue_id":"issue:x"}',
        }

    def test_lead_prompt_delegates_and_forbids_editing(self):
        prompt = _build_codex_prompt(self._env("lead", skill="SKILL-LEAD-MARKER"), {"issue_id": "issue:x"})
        assert "SKILL-LEAD-MARKER" in prompt          # role skill injected
        assert "ORQUESTADOR" in prompt
        assert "create_issue" in prompt
        assert "NO escribas ni edites" in prompt

    def test_engineer_prompt_keeps_implement_instructions(self):
        prompt = _build_codex_prompt(self._env("engineer"), {"issue_id": "issue:x"})
        assert "ORQUESTADOR" not in prompt
        assert "edici" in prompt.lower()  # implement by editing files

    def test_scout_prompt_is_read_only_and_closes(self):
        prompt = _build_codex_prompt(self._env("file_scout"), {"issue_id": "issue:x"})
        assert "solo lectura" in prompt.lower()
        assert "set_status" in prompt


class TestBuildCodexCommand:
    def test_no_ask_for_approval_flag(self):
        rt = _make_runtime()
        cmd = rt._build_codex_command("task", schema_path="/s.json", output_path="/o.json", effective_cwd=None)
        assert "--ask-for-approval" not in cmd

    def test_model_routed_via_config_override_for_subscription_mode(self):
        """Subscription mode: model applied via `-c model="<slug>"`, never -m/--model
        (which would route through the API-key auth path and reject the model)."""
        rt = _make_runtime(model="gpt-5.5")
        cmd = rt._build_codex_command("task", schema_path="/s.json", output_path="/o.json", effective_cwd=None)
        assert "--model" not in cmd
        assert "-m" not in cmd
        assert "-c" in cmd
        assert 'model="gpt-5.5"' in cmd

    def test_no_model_override_when_unset_for_subscription_mode(self):
        """No model configured → no -c model override; codex uses config.toml default."""
        rt = _make_runtime(model=None)
        cmd = rt._build_codex_command("task", schema_path="/s.json", output_path="/o.json", effective_cwd=None)
        assert not any(str(a).startswith("model=") for a in cmd)
        assert "--model" not in cmd

    def test_notify_hook_disabled_for_headless_runs(self):
        rt = _make_runtime()
        cmd = rt._build_codex_command("task", schema_path="/s.json", output_path="/o.json", effective_cwd=None)
        assert "notify=[]" in cmd

    def test_prompt_read_from_stdin_not_argv(self):
        """The prompt must not be an argv element (Windows command-line length
        limit); codex reads it from stdin via the "-" positional."""
        rt = _make_runtime()
        prompt = "x" * 20000
        cmd = rt._build_codex_command(prompt, schema_path="/s.json", output_path="/o.json", effective_cwd=None)
        assert prompt not in cmd
        assert cmd[-1] == "-"

    def test_model_passed_for_oss_mode(self):
        rt = _make_runtime(model="qwen2.5-coder:14b", oss=True)
        cmd = rt._build_codex_command("task", schema_path="/s.json", output_path="/o.json", effective_cwd=None)
        assert "--model" in cmd
        assert "qwen2.5-coder:14b" in cmd

    def test_model_passed_for_local_provider_mode(self):
        rt = _make_runtime(model="gemma-3-4b-it", local_provider="lmstudio")
        cmd = rt._build_codex_command("task", schema_path="/s.json", output_path="/o.json", effective_cwd=None)
        assert "--model" in cmd

    def test_cd_passed_when_effective_cwd_set(self, tmp_path):
        rt = _make_runtime()
        cmd = rt._build_codex_command("task", schema_path="/s.json", output_path="/o.json", effective_cwd=str(tmp_path))
        assert "--cd" in cmd
        assert str(tmp_path) in cmd

    def test_cd_not_passed_when_no_effective_cwd(self):
        rt = _make_runtime()
        cmd = rt._build_codex_command("task", schema_path="/s.json", output_path="/o.json", effective_cwd=None)
        assert "--cd" not in cmd

    def test_cd_uses_effective_cwd_not_self_cwd(self, tmp_path):
        """effective_cwd from env should override self.cwd=None."""
        effective = str(tmp_path / "workspace")
        rt = _make_runtime()  # self.cwd is None
        cmd = rt._build_codex_command("task", schema_path="/s.json", output_path="/o.json", effective_cwd=effective)
        assert "--cd" in cmd
        idx = cmd.index("--cd")
        assert cmd[idx + 1] == effective

    def test_ephemeral_and_skip_git_flags_present(self):
        rt = _make_runtime()
        cmd = rt._build_codex_command("task", schema_path="/s.json", output_path="/o.json", effective_cwd=None)
        assert "--ephemeral" in cmd
        assert "--skip-git-repo-check" in cmd

    def test_output_schema_and_last_message_flags(self):
        rt = _make_runtime()
        cmd = rt._build_codex_command("task", schema_path="/my/schema.json", output_path="/my/out.json", effective_cwd=None)
        assert "--output-schema" in cmd
        assert "/my/schema.json" in cmd
        assert "--output-last-message" in cmd
        assert "/my/out.json" in cmd


# ---------------------------------------------------------------------------
# execute — codex path (subprocess mocked)
# ---------------------------------------------------------------------------


class TestExecuteCodexPath:
    def _make_runtime(self) -> ClaudeSubscriptionCliRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")
        return ClaudeSubscriptionCliRuntime(
            descriptor=descriptor,
            cli_kind="codex",
            command=["codex"],
        )

    def _make_env(self, workspace: str = "") -> dict[str, str]:
        return {
            "AITEAM_RUN_ID": "run-test",
            "AITEAM_TASK_ID": "issue-1",
            "AITEAM_WAKE_REASON": "child_report",
            "AITEAM_AGENT_ROLE": "engineer",
            "AITEAM_WAKE_PAYLOAD_JSON": '{"issue_id": "issue-1", "task": "write hello.txt"}',
            "AITEAM_WORKSPACE_ROOT": workspace,
        }

    def test_successful_run_returns_completed(self, tmp_path):
        output = json.dumps({"status": "completed", "summary": "wrote file", "add_comment": "done"})
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = output
        mock_proc.stderr = ""

        rt = self._make_runtime()
        env = self._make_env(str(tmp_path))

        with patch("aiteam.adapters.subscription_cli_adapter.subprocess.run", return_value=mock_proc) as mock_run:
            # Also patch _command_context's read_output to return the output
            with patch(
                "aiteam.adapters.subscription_cli_adapter._command_context.__enter__",
                return_value={
                    "command": ["codex.cmd", "exec", "..."],
                    "read_output": lambda proc: output,
                },
            ):
                result = rt.execute({"issue_id": "issue-1"}, env)

        assert result.status == "completed"
        assert result.output == "wrote file"

    def test_nonzero_exit_returns_failed(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "some error output"
        mock_proc.stderr = ""

        rt = self._make_runtime()
        env = self._make_env(str(tmp_path))

        with patch("aiteam.adapters.subscription_cli_adapter.subprocess.run", return_value=mock_proc):
            with patch(
                "aiteam.adapters.subscription_cli_adapter._command_context.__enter__",
                return_value={
                    "command": ["codex.cmd", "exec"],
                    "read_output": lambda proc: proc.stdout,
                },
            ):
                result = rt.execute({"issue_id": "issue-1"}, env)

        assert result.status == "failed"
        assert result.error_code == "subscription_cli_nonzero_exit"

    def test_usage_limit_has_specific_error_code(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "You've hit your usage limit. Purchase more credits or try again later."
        mock_proc.stderr = ""
        rt = self._make_runtime()
        env = self._make_env(str(tmp_path))

        with patch("aiteam.adapters.subscription_cli_adapter.subprocess.run", return_value=mock_proc):
            with patch(
                "aiteam.adapters.subscription_cli_adapter._command_context.__enter__",
                return_value={
                    "command": ["codex.cmd", "exec"],
                    "read_output": lambda proc: proc.stdout,
                },
            ):
                result = rt.execute({"issue_id": "issue-1"}, env)

        assert result.status == "failed"
        assert result.error_code == "subscription_cli_usage_limit"

    def test_model_requiring_newer_cli_is_model_unavailable(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = (
            "The 'gpt-5.6-luna' model requires a newer version of Codex. "
            "Please upgrade to the latest app or CLI and try again."
        )
        mock_proc.stderr = ""
        rt = self._make_runtime()
        env = self._make_env(str(tmp_path))

        with patch("aiteam.adapters.subscription_cli_adapter.subprocess.run", return_value=mock_proc):
            with patch(
                "aiteam.adapters.subscription_cli_adapter._command_context.__enter__",
                return_value={
                    "command": ["codex.cmd", "exec"],
                    "read_output": lambda proc: proc.stdout,
                },
            ):
                result = rt.execute({"issue_id": "issue-1"}, env)

        assert result.status == "failed"
        assert result.error_code == "model_unavailable"

    def test_add_comment_synthesised_as_op(self, tmp_path):
        output = json.dumps({"status": "completed", "summary": "ok", "add_comment": "check the tests"})
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = output
        mock_proc.stderr = ""

        rt = self._make_runtime()
        env = self._make_env(str(tmp_path))

        with patch("aiteam.adapters.subscription_cli_adapter.subprocess.run", return_value=mock_proc):
            with patch(
                "aiteam.adapters.subscription_cli_adapter._command_context.__enter__",
                return_value={
                    "command": ["codex.cmd"],
                    "read_output": lambda proc: output,
                },
            ):
                result = rt.execute({"issue_id": "issue-1"}, env)

        assert result.actions is not None
        comments = result.actions.get("add_comments", [])
        assert any("check the tests" in c for c in comments)

    def test_command_not_found_returns_failed(self, tmp_path):
        rt = self._make_runtime()
        env = self._make_env(str(tmp_path))

        with patch(
            "aiteam.adapters.subscription_cli_adapter.subprocess.run",
            side_effect=FileNotFoundError("codex not found"),
        ):
            with patch(
                "aiteam.adapters.subscription_cli_adapter._command_context.__enter__",
                return_value={
                    "command": ["codex"],
                    "read_output": lambda proc: "",
                },
            ):
                result = rt.execute({"issue_id": "issue-1"}, env)

        assert result.status == "failed"
        assert result.error_code == "subscription_cli_not_found"

    def test_stdin_devnull_passed_to_subprocess(self, tmp_path):
        """Verify subprocess.DEVNULL is always passed to prevent stdin hang."""
        import subprocess as _subprocess
        output = json.dumps({"status": "completed", "summary": "ok", "add_comment": ""})
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        rt = self._make_runtime()
        env = self._make_env(str(tmp_path))

        captured_kwargs: dict[str, Any] = {}

        def fake_run(*args: Any, **kwargs: Any) -> MagicMock:
            captured_kwargs.update(kwargs)
            return mock_proc

        with patch("aiteam.adapters.subscription_cli_adapter.subprocess.run", side_effect=fake_run):
            with patch(
                "aiteam.adapters.subscription_cli_adapter._command_context.__enter__",
                return_value={"command": ["codex"], "read_output": lambda proc: output},
            ):
                rt.execute({"issue_id": "issue-1"}, env)

        assert captured_kwargs.get("stdin") == _subprocess.DEVNULL

    def test_prompt_piped_via_stdin_input(self, tmp_path):
        """When the command context provides stdin_input, it is piped to the
        subprocess via `input` (and stdin=DEVNULL is not set)."""
        import subprocess as _subprocess
        output = json.dumps({"status": "completed", "summary": "ok", "add_comment": ""})
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        rt = self._make_runtime()
        env = self._make_env(str(tmp_path))
        captured_kwargs: dict[str, Any] = {}

        def fake_run(*args: Any, **kwargs: Any) -> MagicMock:
            captured_kwargs.update(kwargs)
            return mock_proc

        big_prompt = "y" * 20000
        with patch("aiteam.adapters.subscription_cli_adapter.subprocess.run", side_effect=fake_run):
            with patch(
                "aiteam.adapters.subscription_cli_adapter._command_context.__enter__",
                return_value={"command": ["codex", "-"], "read_output": lambda proc: output, "stdin_input": big_prompt},
            ):
                rt.execute({"issue_id": "issue-1"}, env)

        assert captured_kwargs.get("input") == big_prompt
        assert captured_kwargs.get("stdin") != _subprocess.DEVNULL
        # Non-ASCII prompts require UTF-8; cp1252 (Windows default) makes codex
        # reject the stdin prompt as invalid UTF-8.
        assert captured_kwargs.get("encoding") == "utf-8"

    def test_effective_cwd_passed_to_subprocess(self, tmp_path):
        """subprocess.run must receive the resolved workspace as cwd."""
        output = json.dumps({"status": "completed", "summary": "ok", "add_comment": ""})
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        rt = self._make_runtime()
        env = self._make_env(str(tmp_path))

        captured_kwargs: dict[str, Any] = {}

        def fake_run(*args: Any, **kwargs: Any) -> MagicMock:
            captured_kwargs.update(kwargs)
            return mock_proc

        with patch("aiteam.adapters.subscription_cli_adapter.subprocess.run", side_effect=fake_run):
            with patch(
                "aiteam.adapters.subscription_cli_adapter._command_context.__enter__",
                return_value={"command": ["codex"], "read_output": lambda proc: output},
            ):
                rt.execute({"issue_id": "issue-1"}, env)

        assert captured_kwargs.get("cwd") == str(tmp_path)


# ---------------------------------------------------------------------------
# reconcile preserves explicit transport assignments
# ---------------------------------------------------------------------------


def _init_db_with_agents(db_path: Path, agents: list[tuple]) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executemany(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, adapter_config_json, capabilities_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            agents,
        )
        conn.commit()


class TestReconcilePreservesExplicitTransport:
    def test_engineer_on_openai_api_stays_on_api_when_codex_is_available(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
        record_model_health(
            "codex_subscription", "gpt-5.6-terra",
            available=True, reason="test runtime inventory",
        )
        db_path = tmp_path / "aiteam.db"
        _init_db_with_agents(db_path, [
            ("role:lead", "lead", "Lead", "lead", "openai_api", '{"profile_id":"openai_api"}', "[]"),
            ("role:engineer", "engineer", "Engineer", "standard", "openai_api", '{"profile_id":"openai_api"}', "[]"),
        ])
        # Project now includes codex_subscription
        (tmp_path / "project_config.json").write_text(
            json.dumps({"version": 1, "adapter_profile_ids": ["codex_subscription", "openai_api"]}),
            encoding="utf-8",
        )

        repaired = reconcile_project_agent_policy(db_path)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = {r["id"]: dict(r) for r in conn.execute(
                "SELECT id, adapter_type, adapter_config_json FROM agents"
            )}

        assert "role:engineer" in repaired
        assert rows["role:engineer"]["adapter_type"] == "openai_api"
        cfg = json.loads(rows["role:engineer"]["adapter_config_json"])
        assert cfg["profile_id"] == "openai_api"

    def test_lead_on_openai_api_not_upgraded_to_codex(self, tmp_path: Path) -> None:
        """Senior roles should keep openai_api even when codex_subscription is available."""
        db_path = tmp_path / "aiteam.db"
        _init_db_with_agents(db_path, [
            ("role:lead", "lead", "Lead", "lead", "openai_api", '{"profile_id":"openai_api"}', "[]"),
        ])
        (tmp_path / "project_config.json").write_text(
            json.dumps({"version": 1, "adapter_profile_ids": ["codex_subscription", "openai_api"]}),
            encoding="utf-8",
        )

        reconcile_project_agent_policy(db_path)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT adapter_type FROM agents WHERE id='role:lead'").fetchone()

        # Lead should stay on openai_api — it was explicitly set, not a placeholder
        assert row["adapter_type"] == "openai_api"

    def test_engineer_with_explicit_cli_not_overwritten(self, tmp_path: Path) -> None:
        """If engineer is already on subscription_cli, reconcile should not change it."""
        cfg = json.dumps({"profile_id": "codex_subscription", "cli_kind": "codex"})
        db_path = tmp_path / "aiteam.db"
        _init_db_with_agents(db_path, [
            ("role:lead", "lead", "Lead", "lead", "openai_api", '{"profile_id":"openai_api"}', "[]"),
            ("role:engineer", "engineer", "Engineer", "standard", "subscription_cli", cfg, '["repo_write"]'),
        ])
        (tmp_path / "project_config.json").write_text(
            json.dumps({"version": 1, "adapter_profile_ids": ["codex_subscription", "openai_api"]}),
            encoding="utf-8",
        )

        repaired = reconcile_project_agent_policy(db_path)

        # Engineer already has subscription_cli — nothing to repair for capabilities (they're set)
        # adapter_type is already correct, supervisor may be wired
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT adapter_type, adapter_config_json FROM agents WHERE id='role:engineer'").fetchone()

        assert row["adapter_type"] == "subscription_cli"
        assert json.loads(row["adapter_config_json"])["profile_id"] == "codex_subscription"


class TestCodexUsageExtraction:
    """El canal de suscripcion no registraba ni un token (usage_json={}):
    el last_message de codex nunca trae usage, vive en el stream --json."""

    def test_sums_usage_across_turn_completed_events(self):
        from aiteam.adapters.subscription_cli_adapter import _extract_codex_usage

        stdout = "\n".join([
            'CORRECTO: ruido del notify hook',
            '{"type":"item.completed","item":{"type":"agent_message"}}',
            '{"type":"turn.completed","usage":{"input_tokens":14874,"cached_input_tokens":3456,"output_tokens":17,"reasoning_output_tokens":10}}',
            '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":0,"output_tokens":3,"reasoning_output_tokens":0}}',
            'linea no-json',
        ])
        usage = _extract_codex_usage(stdout, "")

        assert usage == {
            "input_tokens": 14974,
            "output_tokens": 20,
            "cached_input_tokens": 3456,
            "reasoning_output_tokens": 10,
        }

    def test_falls_back_to_stderr_tokens_used_line(self):
        from aiteam.adapters.subscription_cli_adapter import _extract_codex_usage

        stderr = "codex\nhola\ntokens used\n12.466\n"
        usage = _extract_codex_usage("", stderr)

        assert usage == {"total_tokens": 12466}

    def test_returns_none_without_signals(self):
        from aiteam.adapters.subscription_cli_adapter import _extract_codex_usage

        assert _extract_codex_usage("solo texto", "sin cifras") is None

    def test_codex_command_includes_json_flag(self, tmp_path):
        runtime = ClaudeSubscriptionCliRuntime(
            descriptor=AdapterDescriptor(adapter_type="subscription_cli", channel="subscription"),
            command=["codex"],
            cli_kind="codex",
        )
        command = runtime._build_codex_command(
            "prompt", schema_path=str(tmp_path / "s.json"),
            output_path=str(tmp_path / "o.json"), effective_cwd=str(tmp_path),
        )
        assert "--json" in command


class TestPythonToolchainInjection:
    """El engineer de CLI Notas no pudo ejecutar pytest: el hijo de codex no
    tenia ningun Python resoluble en PATH y el cierre acabo escalando."""

    def test_prefers_workspace_venv(self, tmp_path):
        import os as _os
        from aiteam.adapters.subscription_cli_adapter import _inject_python_toolchain

        bin_dir = tmp_path / "venv" / "Scripts"
        bin_dir.mkdir(parents=True)
        (bin_dir / "python.exe").write_bytes(b"")

        env = _inject_python_toolchain({"PATH": "C:/algo"}, str(tmp_path))

        assert env["PATH"].startswith(str(bin_dir))
        assert env["AITEAM_PYTHON"] == str(bin_dir / "python.exe")
        assert "C:/algo" in env["PATH"]

    def test_falls_back_to_orchestrator_interpreter(self, tmp_path):
        import sys as _sys
        from pathlib import Path as _Path
        from aiteam.adapters.subscription_cli_adapter import _inject_python_toolchain

        env = _inject_python_toolchain({}, str(tmp_path))

        orch_bin = str(_Path(_sys.executable).parent)
        assert env["PATH"].startswith(orch_bin)
        assert env["AITEAM_PYTHON"]
        assert _Path(env["AITEAM_PYTHON"]).exists()

    def test_does_not_duplicate_path_prefix(self, tmp_path):
        from aiteam.adapters.subscription_cli_adapter import _inject_python_toolchain

        once = _inject_python_toolchain({}, str(tmp_path))
        twice = _inject_python_toolchain(dict(once), str(tmp_path))

        assert twice["PATH"] == once["PATH"]
