from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiteam.adapters.registry import AdapterDescriptor, ExecutionResult, StaticAdapterRuntime
from aiteam.adapters.work_contract import (
    SUBMIT_WORK_SCHEMA,
    build_execution_contract,
    ops_to_actions,
    parse_submit_work,
)

# Simplified output schema for Codex CLI.
# OpenAI structured output requires ALL properties in `required` when
# additionalProperties=False — the full SUBMIT_WORK_SCHEMA with nested
# OP_SCHEMA violates this because OP_SCHEMA only requires "type".
# We ask Codex for a flat object and synthesise ops on our side.
CODEX_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "failed", "skipped"]},
        "summary": {"type": "string"},
        "add_comment": {"type": "string"},
    },
    "required": ["status", "summary", "add_comment"],
    "additionalProperties": False,
}


@dataclass
class ClaudeSubscriptionCliRuntime:
    """Runs a CLI agent (codex, claude, gemini) in non-interactive mode for an AI Teams agent."""

    descriptor: AdapterDescriptor
    command: list[str] | None = None
    cli_kind: str = "claude"
    model: str | None = None
    permission_mode: str = "auto"
    sandbox: str = "workspace-write"
    approval_policy: str = "never"
    oss: bool = False
    local_provider: str | None = None
    timeout_sec: int = 600
    max_output_chars: int = 65536
    cwd: Path | None = None

    def with_config(self, config: dict[str, Any]) -> "ClaudeSubscriptionCliRuntime":
        command = config.get("command")
        if isinstance(command, str):
            command_list: list[str] | None = [command]
        elif isinstance(command, list) and all(isinstance(item, str) for item in command):
            command_list = list(command)
        else:
            command_list = self.command

        cwd_raw = str(config.get("cwd") or "").strip()
        return ClaudeSubscriptionCliRuntime(
            descriptor=self.descriptor,
            command=command_list,
            cli_kind=str(config.get("cli_kind") or self.cli_kind or "claude"),
            model=str(config.get("model") or self.model or "").strip() or None,
            permission_mode=str(config.get("permission_mode") or self.permission_mode or "auto"),
            sandbox=str(config.get("sandbox") or self.sandbox or "workspace-write"),
            approval_policy=str(config.get("approval_policy") or self.approval_policy or "never"),
            oss=bool(config.get("oss", self.oss)),
            local_provider=str(config.get("local_provider") or self.local_provider or "").strip() or None,
            timeout_sec=int(config.get("timeout_sec") or self.timeout_sec),
            max_output_chars=int(config.get("max_output_chars") or self.max_output_chars),
            cwd=Path(cwd_raw) if cwd_raw else self.cwd,
        )

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        base = StaticAdapterRuntime(self.descriptor).build_env(run_id=run_id, wake_context=wake_context)
        # Codex uses a flat simplified schema; other CLI adapters (claude, generic) use the full one.
        schema = CODEX_OUTPUT_SCHEMA if self.cli_kind == "codex" else SUBMIT_WORK_SCHEMA
        return {**base, "AITEAM_SUBMIT_WORK_SCHEMA": json.dumps(schema, ensure_ascii=False)}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        merged_env = {**os.environ, **env}

        # Resolve the effective workspace root once — used for both subprocess cwd and --cd.
        # Priority: explicit self.cwd (from config) > AITEAM_WORKSPACE_ROOT (from executor).
        if self.cwd:
            effective_cwd: str | None = str(self.cwd)
        else:
            ws = env.get("AITEAM_WORKSPACE_ROOT", "").strip()
            effective_cwd = ws or None

        try:
            with _command_context(self, env, run, effective_cwd=effective_cwd) as spec:
                proc = subprocess.run(
                    spec["command"],
                    env=merged_env,
                    cwd=effective_cwd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_sec,
                    stdin=subprocess.DEVNULL,  # prevent CLI from blocking waiting for keyboard input
                )
                raw_output = spec["read_output"](proc)
        except subprocess.TimeoutExpired as exc:
            stdout = _coerce_output(exc.stdout)
            return ExecutionResult(
                status="failed",
                output=stdout[: self.max_output_chars] or None,
                error=f"timeout after {self.timeout_sec}s",
                error_code="subscription_cli_timeout",
            )
        except FileNotFoundError as exc:
            return ExecutionResult(
                status="failed",
                error=f"command not found: {(self.command or ['claude'])[0]!r} - {exc}",
                error_code="subscription_cli_not_found",
                exit_code=127,
            )
        except Exception as exc:
            return ExecutionResult(status="failed", error=str(exc), error_code="subscription_cli_error", exit_code=1)

        raw_output = raw_output[: self.max_output_chars]
        if proc.returncode != 0:
            return ExecutionResult(
                status="failed",
                output=raw_output or None,
                exit_code=proc.returncode,
                error=f"exit code {proc.returncode}",
                error_code="subscription_cli_nonzero_exit",
            )

        try:
            if self.cli_kind == "codex":
                work = _parse_codex_output(raw_output)
            else:
                work = parse_submit_work(raw_output)
        except ValueError as exc:
            return ExecutionResult(
                status="failed",
                output=raw_output or None,
                exit_code=proc.returncode,
                error=str(exc),
                error_code="subscription_cli_parse_error",
            )

        ops = work.get("ops") or []
        if not isinstance(ops, list):
            ops = []
        # Codex simplified schema returns add_comment as a top-level string; convert to op.
        add_comment = str(work.get("add_comment") or "").strip()
        if add_comment and not ops:
            ops = [{"type": "add_comment", "body": add_comment}]
        status = str(work.get("status") or "completed")
        summary = str(work.get("summary") or "").strip()
        usage = _extract_usage(raw_output)

        return ExecutionResult(
            status=status if status in {"completed", "failed", "skipped"} else "completed",
            output=summary or None,
            exit_code=proc.returncode,
            usage=usage,
            actual_cost_cents=0,
            actions=ops_to_actions([op for op in ops if isinstance(op, dict)]),
        )

    def _build_claude_command(self, system_prompt: str, user_prompt: str) -> list[str]:
        """Build command for Claude Code CLI (non-interactive -p mode)."""
        command = list(self.command or ["claude"])
        if self.cli_kind == "generic":
            command.append(user_prompt)
            return command
        command.extend(["-p", "--output-format", "json", "--no-session-persistence"])
        command.extend(["--json-schema", json.dumps(SUBMIT_WORK_SCHEMA, ensure_ascii=False)])
        command.extend(["--append-system-prompt", system_prompt])
        if self.model:
            command.extend(["--model", self.model])
        if self.permission_mode:
            command.extend(["--permission-mode", self.permission_mode])
        command.append(user_prompt)
        return command

    def _build_codex_command(
        self,
        prompt: str,
        *,
        schema_path: str,
        output_path: str,
        effective_cwd: str | None,
    ) -> list[str]:
        """Build command for Codex CLI (exec mode with structured output schema).

        Key decisions:
        - --ask-for-approval removed (not a valid flag in codex 0.128)
        - -m/--model only passed for OSS/local_provider; ChatGPT-subscription mode
          uses ~/.codex/config.toml to select the model — passing -m triggers a
          different auth path that rejects most model names
        - --cd always set to the resolved workspace root so the sandbox boundary
          matches the directory the subprocess is started in
        """
        raw = list(self.command or ["codex"])
        # On Windows, npm global scripts install as <name>.cmd wrappers.
        if len(raw) == 1:
            raw = [_resolve_cli_cmd(raw[0])]
        command = raw
        command.extend(["exec", "--skip-git-repo-check", "--ephemeral"])
        command.extend(["--sandbox", self.sandbox])
        command.extend(["--output-schema", schema_path, "--output-last-message", output_path])
        # Only pass --model for OSS/local_provider paths.
        if self.model and (self.oss or self.local_provider):
            command.extend(["--model", self.model])
        if self.oss:
            command.append("--oss")
        if self.local_provider:
            command.extend(["--local-provider", self.local_provider])
        # Always set --cd so codex's sandbox root matches where subprocess runs.
        if effective_cwd:
            command.extend(["--cd", effective_cwd])
        command.append(prompt)
        return command


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_system_prompt(env: dict[str, str]) -> str:
    role = env.get("AITEAM_AGENT_ROLE", "").strip() or "agent"
    skill = env.get("AITEAM_AGENT_SKILL", "").strip()
    base = skill or f"Eres un agente de AI Teams con rol {role}. Completa la delegacion recibida."
    return base + build_execution_contract()


def _build_codex_prompt(env: dict[str, str], run: dict[str, Any]) -> str:
    """Single consolidated prompt for Codex CLI.

    Codex is a code-first agent: no system/user split, one task-focused prompt.
    We embed role, workspace path, wake context and output format instructions
    so the agent knows exactly what to do and where to report.
    """
    role = env.get("AITEAM_AGENT_ROLE", "").strip() or "engineer"
    workspace = env.get("AITEAM_WORKSPACE_ROOT", "").strip()
    payload = env.get("AITEAM_WAKE_PAYLOAD_JSON", "").strip()
    if not payload:
        payload = json.dumps({"issue_id": run.get("issue_id")}, ensure_ascii=False)

    parts = [
        f"Eres el agente {role.upper()} de un equipo de IA (AI Teams).",
        "Tu trabajo: implementar la tarea delegada leyendo y editando archivos en el workspace.",
        "",
        f"Workspace root: {workspace}" if workspace else "",
        f"Issue ID:       {env.get('AITEAM_TASK_ID', '')}",
        f"Wake reason:    {env.get('AITEAM_WAKE_REASON', '')}",
        "",
        "=== Contexto de delegación (AITEAM_WAKE_PAYLOAD_JSON) ===",
        payload,
        "",
        "=== Instrucciones ===",
        "1. Lee los archivos relevantes del workspace para entender el estado actual.",
        "2. Implementa los cambios necesarios usando tus herramientas nativas (escritura/edición de archivos).",
        "3. Si necesitas ejecutar comandos (instalar dependencias, tests, etc.), usa el shell.",
        "4. Cuando termines, responde EXACTAMENTE con un JSON que siga el output schema:",
        '   {"status": "completed", "summary": "<qué hiciste>", "add_comment": "<detalle para el equipo>"}',
        "   — status: completed si la tarea está hecha, failed si no fue posible, skipped si no aplica.",
        "   — summary: descripción concisa de los cambios realizados (1-3 frases).",
        "   — add_comment: información adicional para el Team Lead / Reviewer (puede ser vacío '').",
    ]
    return "\n".join(p for p in parts if p is not None)


def _build_user_prompt(env: dict[str, str], run: dict[str, Any]) -> str:
    """Prompt for Claude/generic CLI adapters (separate system + user prompt)."""
    payload = env.get("AITEAM_WAKE_PAYLOAD_JSON", "").strip()
    if not payload:
        payload = json.dumps({"issue_id": run.get("issue_id")}, ensure_ascii=False)
    workspace = env.get("AITEAM_WORKSPACE_ROOT", "").strip()
    ws_line = f"Workspace root: {workspace}\n" if workspace else ""
    return (
        "Ejecuta esta wake de AI Teams y responde solo con el JSON estructurado solicitado.\n\n"
        f"{ws_line}"
        f"Run ID: {env.get('AITEAM_RUN_ID', '')}\n"
        f"Issue ID: {env.get('AITEAM_TASK_ID', '')}\n"
        f"Wake reason: {env.get('AITEAM_WAKE_REASON', '')}\n\n"
        "AITEAM_WAKE_PAYLOAD_JSON:\n"
        f"{payload}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_usage(raw_output: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw_output)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    usage = parsed.get("usage")
    if isinstance(usage, dict):
        return usage
    result = parsed.get("result")
    if isinstance(result, dict) and isinstance(result.get("usage"), dict):
        return result["usage"]
    return None


def _coerce_output(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _resolve_cli_cmd(name: str) -> str:
    """Resolve CLI name to executable path, handling Windows .cmd npm shims.

    On Windows, npm global scripts install as ``<name>.cmd`` wrappers.
    ``shutil.which('codex')`` may not find them; we try ``codex.cmd`` first.
    """
    if os.name == "nt" and not name.lower().endswith((".exe", ".cmd", ".bat")):
        for candidate in (f"{name}.cmd", f"{name}.exe"):
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
    resolved = shutil.which(name)
    return resolved or name


def _parse_codex_output(value: Any) -> dict[str, Any]:
    """Parse Codex CLI output using the simplified CODEX_OUTPUT_SCHEMA.

    Codex writes a flat JSON object with {status, summary, add_comment}.
    Also accepts the full SUBMIT_WORK_SCHEMA format (ops list) for forward
    compatibility with future Codex versions.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty codex output")
        try:
            parsed = json.loads(text)
            return _parse_codex_output(parsed)
        except json.JSONDecodeError:
            pass
        # Try to extract JSON object from mixed text/stdout noise
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return _parse_codex_output(json.loads(text[start : end + 1]))
            except Exception:
                pass
        raise ValueError(f"codex output JSON not found in: {text[:200]!r}")
    if isinstance(value, dict):
        # Simplified schema: status + summary present → accept
        if "status" in value and "summary" in value:
            return value
        # Full submit_work schema nested inside result/content/message
        for key in ("result", "content", "message"):
            nested = value.get(key)
            if nested is not None:
                try:
                    return _parse_codex_output(nested)
                except ValueError:
                    pass
        raise ValueError(f"codex dict missing required keys: {list(value)[:10]}")
    raise ValueError(f"codex output not parseable: {str(value)[:200]!r}")


# ---------------------------------------------------------------------------
# Command context manager
# ---------------------------------------------------------------------------

class _command_context:
    """Context manager that builds the CLI command and cleans up temp files."""

    def __init__(
        self,
        runtime: ClaudeSubscriptionCliRuntime,
        env: dict[str, str],
        run: dict[str, Any],
        *,
        effective_cwd: str | None,
    ) -> None:
        self.runtime = runtime
        self.env = env
        self.run = run
        self.effective_cwd = effective_cwd
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> dict[str, Any]:
        if self.runtime.cli_kind != "codex":
            system_prompt = _build_system_prompt(self.env)
            user_prompt = _build_user_prompt(self.env, self.run)
            command = self.runtime._build_claude_command(system_prompt, user_prompt)
            return {
                "command": command,
                "read_output": lambda proc: ((proc.stdout or "") + (proc.stderr or "")),
            }

        # Codex path: write schema to temp file, capture output via --output-last-message
        self._tmpdir = tempfile.TemporaryDirectory(prefix="aiteam-codex-cli-")
        tmp_path = Path(self._tmpdir.name)
        schema_path = tmp_path / "submit_work.schema.json"
        output_path = tmp_path / "last_message.json"
        # Codex requires all properties in `required` when additionalProperties=False.
        # The simplified CODEX_OUTPUT_SCHEMA satisfies this; ops are synthesised from add_comment.
        schema_path.write_text(json.dumps(CODEX_OUTPUT_SCHEMA, ensure_ascii=False), encoding="utf-8")

        prompt = _build_codex_prompt(self.env, self.run)
        command = self.runtime._build_codex_command(
            prompt,
            schema_path=str(schema_path),
            output_path=str(output_path),
            effective_cwd=self.effective_cwd,
        )

        def read_output(proc: subprocess.CompletedProcess[str]) -> str:
            # Prefer the structured output file; fall back to stdout+stderr
            if output_path.exists():
                try:
                    return output_path.read_text(encoding="utf-8")
                except Exception:
                    pass
            return (proc.stdout or "") + (proc.stderr or "")

        return {"command": command, "read_output": read_output}

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
