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
    _parse_codex_output,
    _resolve_cli_cmd,
)
from aiteam.adapters.registry import AdapterDescriptor
from aiteam.db.migration import SCHEMA_PATH
from aiteam.project_adapters import reconcile_project_agent_policy


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

    def test_already_resolved_exe_not_re_resolved(self, monkeypatch):
        """A name that already ends with .cmd is not double-resolved."""
        monkeypatch.setattr(os, "name", "nt")
        calls: list[str] = []
        def fake_which(name: str) -> str | None:
            calls.append(name)
            return f"C:/npm/{name}"
        monkeypatch.setattr("aiteam.adapters.subscription_cli_adapter.shutil.which", fake_which)
        result = _resolve_cli_cmd("codex.cmd")
        # Should not try codex.cmd.cmd or codex.cmd.exe
        assert result == "C:/npm/codex.cmd"
        assert all(".cmd.cmd" not in c for c in calls)


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
# reconcile upgrades API-only junior to CLI
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


class TestReconcileUpgradesApiOnlyJunior:
    def test_engineer_on_openai_api_upgraded_to_codex_when_available(self, tmp_path: Path) -> None:
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
        assert rows["role:engineer"]["adapter_type"] == "subscription_cli"
        cfg = json.loads(rows["role:engineer"]["adapter_config_json"])
        assert cfg["profile_id"] == "codex_subscription"

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
