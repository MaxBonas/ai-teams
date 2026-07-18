from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiteam.adapters.registry import AdapterDescriptor, ExecutionResult, StaticAdapterRuntime
from aiteam.adapters.work_contract import (
    OPENAI_SUBMIT_WORK_SCHEMA,
    SUBMIT_WORK_SCHEMA,
    build_execution_contract,
    ops_to_actions,
    parse_submit_work,
)

# Output schema for Codex CLI. Includes an `ops` array so codex agents can
# delegate and manage work (create_issue, set_status, create_interaction, …)
# exactly like the API adapters — not just comment. Without ops an orchestrator
# role (the Lead) can only comment or edit files directly, which forces it to
# code instead of delegate.
#
# Codex passes --output-schema straight to OpenAI structured outputs, which
# demands STRICT mode: every object needs additionalProperties=false and ALL
# properties in `required` (optionals expressed as nullable). We reuse the
# already-strict ops item schema from work_contract rather than hand-rolling
# one — a non-strict schema fails the request with HTTP 400 invalid_json_schema.
CODEX_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "failed", "skipped"]},
        "summary": {"type": "string"},
        "add_comment": {"type": "string"},
        "ops": OPENAI_SUBMIT_WORK_SCHEMA["properties"]["ops"],
    },
    "required": ["status", "summary", "add_comment", "ops"],
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
        merged_env = _inject_python_toolchain(merged_env, effective_cwd)

        try:
            with _command_context(self, env, run, effective_cwd=effective_cwd) as spec:
                stdin_input = spec.get("stdin_input")
                run_kwargs: dict[str, Any] = dict(
                    env=merged_env,
                    cwd=effective_cwd,
                    capture_output=True,
                    # Force UTF-8 for stdin/stdout: the prompt carries non-ASCII
                    # (Spanish accents) and codex reads/writes UTF-8. Without this
                    # Windows uses cp1252 → codex rejects the stdin prompt
                    # ("input is not valid UTF-8") and stdout comes back mojibake.
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_sec,
                )
                if stdin_input is not None:
                    # Prompt goes through stdin to dodge the OS command-line
                    # length limit; subprocess opens a pipe for `input`.
                    run_kwargs["input"] = stdin_input
                else:
                    # No stdin payload — close it so the CLI never blocks
                    # waiting for keyboard input.
                    run_kwargs["stdin"] = subprocess.DEVNULL
                proc = subprocess.run(spec["command"], **run_kwargs)
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
            elif self.cli_kind == "antigravity":
                work = _parse_antigravity_output(raw_output)
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
        if usage is None and self.cli_kind == "codex":
            # El last_message de codex nunca trae usage: vive en el event
            # stream de stdout (--json) o, como último recurso, en la línea
            # "tokens used" del log humano de stderr.
            usage = _extract_codex_usage(
                proc.stdout if isinstance(proc.stdout, str) else "",
                proc.stderr if isinstance(proc.stderr, str) else "",
            )

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
        if len(command) == 1:
            command[0] = _resolve_cli_cmd(command[0])
        if self.cli_kind == "antigravity":
            prompt = (
                f"{system_prompt}\n\n{user_prompt}\n\n"
                "Return ONLY the submit_work JSON object required by the contract. "
                "Do not wrap it in Markdown."
            )
            command.extend([
                "--new-project", "--print", prompt, "--mode", "plan", "--sandbox",
                # Headless mode cannot ask the user for permission to read the
                # ephemeral prompt file. Plan mode plus sandbox remain active,
                # so this approves the read tool without granting an execution
                # profile or an unrestricted terminal.
                "--dangerously-skip-permissions",
            ])
            command.extend(["--print-timeout", f"{self.timeout_sec}s"])
            if self.model:
                command.extend(["--model", self.model])
            return command
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
        - Model selection depends on the auth path:
            * OSS / local_provider → pass `--model <slug>` (accepts -m directly).
            * ChatGPT subscription → pass `-c model="<slug>"` (the config-override
              syntax that shares the subscription auth path). Passing `-m`/`--model`
              here routes through an API-key auth path that rejects subscription
              model names. When no model is configured, codex falls back to the
              default in ~/.codex/config.toml.
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
        # Neutralize the user's interactive turn-ended notifier: a headless run
        # must not trigger ~/.codex/config.toml's `notify` hook, which spawns a
        # computer-use helper that kills the run's process tree mid-flight.
        command.extend(["-c", "notify=[]"])
        # --json emite eventos JSONL por stdout (turn.completed trae el usage
        # con desglose input/output/cached/reasoning). Sin esto el canal de
        # suscripción no registraba NI UN token: usage_json quedaba {} y
        # cost_events vacío, dejando ciega la economía de hiring en el canal
        # mayoritario (627/935 runs del proyecto Unity, todo CLI Notas/Gastos).
        command.append("--json")
        command.extend(["--output-schema", schema_path, "--output-last-message", output_path])
        if self.model:
            if self.oss or self.local_provider:
                command.extend(["--model", self.model])
            else:
                command.extend(["-c", f'model="{self.model}"'])
        if self.oss:
            command.append("--oss")
        if self.local_provider:
            command.extend(["--local-provider", self.local_provider])
        # Always set --cd so codex's sandbox root matches where subprocess runs.
        if effective_cwd:
            command.extend(["--cd", effective_cwd])
        # Read the prompt from stdin ("-") rather than argv. Large prompts
        # (skill + wake payload + injected workspace files) blow past the
        # Windows command-line length limit (~8191 chars via cmd.exe /
        # codex.cmd), which fails the run instantly with "command line too
        # long". _command_context pipes the prompt to the subprocess stdin.
        command.append("-")
        return command


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_system_prompt(env: dict[str, str]) -> str:
    role = env.get("AITEAM_AGENT_ROLE", "").strip() or "agent"
    skill = env.get("AITEAM_AGENT_SKILL", "").strip()
    base = skill or f"Eres un agente de AI Teams con rol {role}. Completa la delegacion recibida."
    contract = base + build_execution_contract()
    if role.lower() == "quorum_auditor":
        contract += (
            "\n\nQUORUM AUDITOR — CONTRATO ESTRICTO:\n"
            "- Eres un auditor independiente. NO sintetices el plan y NO uses accept_quorum_synthesis.\n"
            "- Devuelve únicamente ops add_comment y set_status done.\n"
            "- El add_comment debe terminar con exactamente un bloque ---AGENT-REPORT---.\n"
            "- En ese bloque usa role: quorum_auditor, result: approved|changes_requested|blocked, "
            "issue_status: done, next_owner: lead, blocker y evidence no vacía.\n"
            "- result: pass, passed o passed_with_findings NO son válidos para el gate de quorum."
        )
    return contract


# Roles that orchestrate rather than implement: they must delegate via ops
# (create_issue, update_child_issue, create_interaction, set_status) and must
# NOT edit workspace files themselves. Everything else is an executor.
_ORCHESTRATION_ROLES = frozenset({"lead", "team_lead"})
# Tier 3 scouts inspect/report only — they never edit files.
_READ_ONLY_ROLES = frozenset({"file_scout", "web_scout", "context_curator"})


def _build_codex_prompt(env: dict[str, str], run: dict[str, Any]) -> str:
    """Single consolidated prompt for Codex CLI, tailored to the agent's role.

    The agent's own skill (AITEAM_AGENT_SKILL) is injected verbatim so codex
    follows the role contract — critically, so the Lead orchestrates and
    delegates instead of coding. Orchestration roles get the ops delegation
    vocabulary and are told NOT to edit files; executor roles keep the
    implement-by-editing instructions.
    """
    role = env.get("AITEAM_AGENT_ROLE", "").strip() or "engineer"
    role_key = role.lower()
    try:
        payload_data = json.loads(env.get("AITEAM_WAKE_PAYLOAD_JSON", "") or "{}")
    except (TypeError, ValueError):
        payload_data = {}
    is_solo_direct = (
        role_key in _ORCHESTRATION_ROLES
        and str(payload_data.get("profile") or "").strip().lower() == "solo_lead"
    )
    is_orchestrator = role_key in _ORCHESTRATION_ROLES and not is_solo_direct
    is_read_only = is_orchestrator or role_key in _READ_ONLY_ROLES
    skill = env.get("AITEAM_AGENT_SKILL", "").strip()
    workspace = env.get("AITEAM_WORKSPACE_ROOT", "").strip()
    payload = env.get("AITEAM_WAKE_PAYLOAD_JSON", "").strip()
    if not payload:
        payload = json.dumps({"issue_id": run.get("issue_id")}, ensure_ascii=False)

    parts: list[str | None] = [
        f"Eres el agente {role.upper()} de un equipo de IA (AI Teams).",
    ]
    if skill:
        parts += ["", "=== Tu rol (instrucciones vinculantes) ===", skill]
    parts += [
        "",
        f"Workspace root: {workspace}" if workspace else "",
        f"Issue ID:       {env.get('AITEAM_TASK_ID', '')}",
        f"Wake reason:    {env.get('AITEAM_WAKE_REASON', '')}",
        "",
        "=== Contexto de delegación (AITEAM_WAKE_PAYLOAD_JSON) ===",
        payload,
        "",
        "=== Directivas del usuario (payload.user_directives) ===",
        "Si el payload incluye `user_directives`, son decisiones VINCULANTES del dueño del proyecto",
        "y prevalecen sobre cualquier estándar o criterio anterior que las contradiga:",
        "  - Lead: refleja cada directiva vigente en los acceptance_criteria de las issues nuevas.",
        "  - Reviewer/QA: juzga contra las directivas; NO exijas nada que una directiva haya descartado.",
        "  - Todos: si una directiva vuelve tu tarea innecesaria, ciérrala y dilo en el comentario.",
        "",
        "=== Instrucciones ===",
    ]

    if is_orchestrator:
        parts += [
            "Eres un ORQUESTADOR, no un implementador. NO escribas ni edites código ni archivos tú mismo.",
            "VISIÓN GLOBAL: `payload.project_open_issues` lista TODAS las issues abiertas del proyecto "
            "(todas las raíces, no solo los hijos de tu issue actual). Cualquier afirmación tipo "
            "'no hay issues abiertas / no queda trabajo' debe basarse en esa lista. Si tu issue está "
            "terminada o vacía pero esa lista NO está vacía, tu acción útil de este heartbeat es "
            "atender esas issues (dirigir, desbloquear o delegar allí).",
            "Tu trabajo es planificar y DELEGAR mediante `ops` en tu respuesta JSON:",
            "  - Para implementación de código → crea un sub-issue: "
            '{"type":"create_issue","title":"...","description":"<spec concreta: tecnología, archivos>","role":"engineer","complexity":"low|medium|high",'
            '"acceptance_criteria":["criterio verificable 1","criterio 2"]}'
            " — los acceptance_criteria son la vara de done: el reviewer juzgará contra esa lista.",
            "  - Para revisión → create_issue con role:reviewer.",
            "  - Para leer archivos o investigar → create_issue con role:file_scout / web_scout (NUNCA lo hagas tú).",
            "  - Para curar/comprimir contexto de un thread largo → create_issue con role:context_curator (NO role:engineer).",
            "  - Para preguntar al usuario → "
            '{"type":"create_interaction","kind":"request_confirmation","title":"...","summary":"...","payload":{"reason":"..."}}',
            "  - Para proponer una herramienta MCP (solo tú, el Lead — nunca un worker) → "
            '{"type":"create_interaction","kind":"request_confirmation","title":"Proponer MCP: <nombre>",'
            '"summary":"<qué, por qué, riesgos>","payload":{"reason":"extension_install_requested",'
            '"name":"<slug>","source":"<comando con versión pineada>","justification":"<evidencia concreta>",'
            '"applies_to_roles":["engineer","reviewer"]}}'
            " — instalar código de terceros SIEMPRE espera al owner, nunca se auto-acepta. "
            "Antes de proponer, revisa si ya existe una propuesta/investigación igual (no dupliques research).",
            "  - Para dirigir a un hijo bloqueado → update_child_issue. Para cerrar la issue → "
            '{"type":"set_status","status":"done"}.',
            "Solo escribe un comentario (add_comment) para dejar constancia; el trabajo real va en `ops`.",
        ]
    elif is_read_only:
        parts += [
            "Eres un SCOUT de solo lectura. Inspecciona y reporta; NO edites archivos.",
            "1. Lee los archivos indicados y responde con un resumen conciso en `add_comment`.",
            "2. Cierra tu tarea añadiendo el op {\"type\":\"set_status\",\"status\":\"done\"} — tu trabajo es de un solo tiro.",
        ]
    else:
        parts += [
            "Modo SOLO LEAD: eres el único agente y tienes autoridad completa sobre el workspace. "
            "No delegues ni crees sub-issues; planifica, implementa, ejecuta las verificaciones y cierra la issue tú mismo."
            if is_solo_direct else None,
            "1. Lee los archivos relevantes del workspace para entender el estado actual.",
            "2. Implementa los cambios necesarios usando tus herramientas nativas (escritura/edición de archivos).",
            "3. Si necesitas ejecutar comandos (instalar dependencias, tests, etc.), usa el shell.",
        ]

    parts += [
        "",
        "=== Formato de salida (obligatorio) ===",
        "Responde EXACTAMENTE con un JSON que siga el output schema:",
        '  {"status":"completed|failed|skipped", "summary":"<1-3 frases>", "add_comment":"<detalle para el equipo, puede ser \'\'>", "ops":[...]}',
        "  — `ops` es una lista de acciones estructuradas (vacía [] si no aplica).",
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


def _parse_antigravity_output(raw_output: str) -> dict[str, Any]:
    """Normalize the two headless envelopes emitted by ``agy`` in real runs.

    Antigravity sometimes omits the transport fields ``status``/``summary``
    while preserving the exact ops, or returns a single submit_work body. This
    adapter-level normalization keeps the work evidence verbatim and never
    manufactures an auditor report from free text.
    """
    try:
        parsed = json.loads(raw_output.strip())
    except (TypeError, ValueError):
        return parse_submit_work(raw_output)
    if not isinstance(parsed, dict):
        return parse_submit_work(parsed)
    ops = parsed.get("ops")
    if isinstance(ops, list):
        return {
            **parsed,
            "status": str(parsed.get("status") or "completed"),
            "summary": str(parsed.get("summary") or "Antigravity submit_work completed"),
        }
    if parsed.get("type") == "submit_work" and isinstance(parsed.get("body"), str):
        body = str(parsed["body"]).strip()
        if body:
            return {
                "status": "completed",
                "summary": "Antigravity submit_work completed",
                "ops": [{"type": "add_comment", "body": body}],
            }
    return parse_submit_work(parsed)


def _inject_python_toolchain(env: dict[str, str], workspace: str | None) -> dict[str, str]:
    """Garantiza que el agente CLI pueda ejecutar ``python``/``pytest``.

    Visto en vivo (CLI Notas, 2026-07-15): el engineer no pudo auto-verificar
    porque el proceso hijo de codex no tenía ningún Python resoluble en PATH,
    y el ciclo terminó escalando al usuario. Prepende el venv del workspace si
    existe (Scripts/ en Windows, bin/ en POSIX) y, en su defecto, el
    directorio del intérprete del orquestador (que siempre existe y trae
    pytest). Expone además ``AITEAM_PYTHON`` con la ruta exacta.
    """
    candidates: list[Path] = []
    if workspace:
        for venv_name in ("venv", ".venv"):
            for bin_name in ("Scripts", "bin"):
                bin_dir = Path(workspace) / venv_name / bin_name
                if (bin_dir / "python.exe").exists() or (bin_dir / "python").exists():
                    candidates.append(bin_dir)
    orchestrator_bin = Path(sys.executable).parent
    candidates.append(orchestrator_bin)

    env = dict(env)
    prefix = os.pathsep.join(str(c) for c in candidates)
    current_path = env.get("PATH", "")
    if prefix and prefix not in current_path:
        env["PATH"] = prefix + (os.pathsep + current_path if current_path else "")
    first = candidates[0]
    python_exe = first / ("python.exe" if (first / "python.exe").exists() else "python")
    env.setdefault("AITEAM_PYTHON", str(python_exe if python_exe.exists() else sys.executable))
    return env


def _extract_codex_usage(stdout: str, stderr: str) -> dict[str, Any] | None:
    """Token usage de una run de codex exec.

    Preferente: eventos JSONL de ``--json`` en stdout — cada turno emite
    ``{"type": "turn.completed", "usage": {input_tokens, cached_input_tokens,
    output_tokens, reasoning_output_tokens}}``; se suman todos los turnos.
    Fallback: el log humano de stderr termina con "tokens used" y la cifra
    total en la línea siguiente (con separador de miles según locale).
    """
    totals: dict[str, int] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if not isinstance(event, dict):
            continue
        usage = event.get("usage")
        if not (isinstance(usage, dict) and str(event.get("type") or "").endswith("completed")):
            continue
        for key, value in usage.items():
            try:
                totals[str(key)] = totals.get(str(key), 0) + int(value)
            except (TypeError, ValueError):
                continue
    if totals:
        return {
            "input_tokens": totals.get("input_tokens", 0),
            "output_tokens": totals.get("output_tokens", 0),
            "cached_input_tokens": totals.get("cached_input_tokens", 0),
            "reasoning_output_tokens": totals.get("reasoning_output_tokens", 0),
        }

    match = re.search(r"tokens used[^\d]*([\d.,  ]+)", stderr, re.IGNORECASE)
    if match:
        digits = re.sub(r"\D", "", match.group(1))
        if digits:
            return {"total_tokens": int(digits)}
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
    if resolved is None and os.name == "nt" and name.lower() == "agy":
        local_app_data = os.environ.get("LOCALAPPDATA")
        candidate = Path(local_app_data) / "agy" / "bin" / "agy.exe" if local_app_data else None
        if candidate is not None and candidate.is_file():
            return str(candidate)
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
            if self.runtime.cli_kind == "antigravity":
                # Windows cannot launch a process when the full quorum payload
                # exceeds CreateProcess' command-line limit. Unlike Codex,
                # ``agy --print`` does not consume the prompt from stdin. Keep
                # argv short and expose an ephemeral prompt file explicitly to
                # Antigravity; __exit__ removes it after the subprocess ends.
                self._tmpdir = tempfile.TemporaryDirectory(prefix="aiteam-antigravity-")
                prompt_dir = Path(self._tmpdir.name)
                prompt_path = prompt_dir / "prompt.txt"
                prompt_path.write_text(f"{system_prompt}\n\n{user_prompt}", encoding="utf-8")
                relay_prompt = (
                    f"Read the complete instructions from {prompt_path} and follow them exactly. "
                    "Return only the requested submit_work JSON object."
                )
                command = self.runtime._build_claude_command("", relay_prompt)
                command.extend(["--add-dir", str(prompt_dir)])
                return {
                    "command": command,
                    "read_output": lambda proc: ((proc.stdout or "") + (proc.stderr or "")),
                }
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

        return {"command": command, "read_output": read_output, "stdin_input": prompt}

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
