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
from aiteam.quorum_quality import quorum_audit_contract_instruction

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

_CODEX_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})


def _normalize_codex_reasoning_effort(value: Any, *, fallback: str | None = None) -> str | None:
    candidate = str(value or fallback or "").strip().lower()
    return candidate if candidate in _CODEX_REASONING_EFFORTS else None


@dataclass
class ClaudeSubscriptionCliRuntime:
    """Runs a CLI agent (codex, claude, gemini) in non-interactive mode for an AI Teams agent."""

    descriptor: AdapterDescriptor
    command: list[str] | None = None
    cli_kind: str = "claude"
    model: str | None = None
    model_reasoning_effort: str | None = None
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
            model_reasoning_effort=_normalize_codex_reasoning_effort(
                config.get("model_reasoning_effort"),
                fallback=self.model_reasoning_effort,
            ),
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
                    env={**merged_env, **spec.get("env_updates", {})},
                    cwd=spec.get("cwd", effective_cwd),
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
            lowered_output = raw_output.lower()
            if any(marker in lowered_output for marker in (
                "you've hit your usage limit",
                "purchase more credits",
                "rate limit",
                "rate_limit",
                "too many requests",
                "quota exceeded",
                "http 429",
            )):
                error_code = "subscription_cli_usage_limit"
            elif (
                "model requires a newer version" in lowered_output
                or "requires a newer version of codex" in lowered_output
                or "unknown model" in lowered_output
                or "model not found" in lowered_output
            ):
                error_code = "model_unavailable"
            else:
                error_code = "subscription_cli_nonzero_exit"
            return ExecutionResult(
                status="failed",
                output=raw_output or None,
                exit_code=proc.returncode,
                error=f"exit code {proc.returncode}",
                error_code=error_code,
            )

        try:
            if self.cli_kind == "codex":
                work = _parse_codex_output(raw_output)
            elif self.cli_kind == "antigravity":
                work = _parse_antigravity_output(raw_output)
            elif self.cli_kind == "opencode":
                work = _parse_opencode_output(raw_output)
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
        elif usage is None and self.cli_kind == "opencode":
            # OpenCode emits usage per step-finish event, not in the final
            # submit_work payload. Keep the gateway at zero marginal cost but
            # retain tokens and its explicit session ID for pressure analysis.
            usage = _extract_opencode_usage(raw_output)

        return ExecutionResult(
            status=status if status in {"completed", "failed", "skipped"} else "completed",
            output=summary or None,
            exit_code=proc.returncode,
            usage=usage,
            actual_cost_cents=0,
            actions=ops_to_actions([op for op in ops if isinstance(op, dict)]),
        )

    def _build_claude_command(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        mcp_config_path: str | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
    ) -> list[str]:
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
        if self.cli_kind == "opencode":
            # `opencode run` is non-interactive and rejects unresolved asks.
            # Never pass --auto: an omitted deny rule would otherwise become an
            # implicit approval in a headless process.
            command.extend(["run", "--format", "json"])
            if self.model:
                model = self.model if "/" in self.model else f"opencode/{self.model}"
                command.extend(["--model", model])
            command.append(user_prompt)
            return command
        command.extend(["-p", "--output-format", "json", "--no-session-persistence"])
        if mcp_config_path:
            # Ignore user/project MCP configuration: this run may see only the
            # grants selected by AI Teams for its role and capability set.
            command.extend(["--strict-mcp-config", "--mcp-config", mcp_config_path])
            denied = [
                f"mcp__{server.get('name')}__{tool}"
                for server in mcp_servers or []
                for tool in server.get("denied_tools") or []
            ]
            if denied:
                command.extend(["--disallowedTools", *denied])
        command.extend(["--json-schema", json.dumps(SUBMIT_WORK_SCHEMA, ensure_ascii=False)])
        command.extend(["--append-system-prompt", system_prompt])
        if self.model:
            command.extend(["--model", self.model])
        if self.permission_mode:
            command.extend(["--permission-mode", self.permission_mode])
        if user_prompt:
            command.append(user_prompt)
        return command

    def _build_codex_command(
        self,
        prompt: str,
        *,
        schema_path: str,
        output_path: str,
        effective_cwd: str | None,
        mcp_servers: list[dict[str, Any]] | None = None,
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
        # Project MCP grants are injected only for this ephemeral process. The
        # config contains executable paths/args but never secret values.
        for server in mcp_servers or []:
            name = str(server.get("name") or "").strip()
            executable = str(server.get("command") or "").strip()
            args = server.get("args") or []
            if not name or not executable or not isinstance(args, list):
                continue
            command.extend(["-c", f"mcp_servers.{name}.command={json.dumps(executable)}"])
            command.extend(["-c", f"mcp_servers.{name}.args={json.dumps(args)}"])
            env_required = server.get("env_required") or []
            if isinstance(env_required, list) and env_required:
                command.extend([
                    "-c",
                    f"mcp_servers.{name}.env_vars={json.dumps(env_required)}",
                ])
            enabled_tools = server.get("enabled_tools") or []
            if isinstance(enabled_tools, list):
                command.extend([
                    "-c",
                    f"mcp_servers.{name}.enabled_tools={json.dumps(enabled_tools)}",
                ])
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
        if self.model_reasoning_effort:
            command.extend(
                ["-c", f'model_reasoning_effort="{self.model_reasoning_effort}"']
            )
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
            "\n" + quorum_audit_contract_instruction()
        )
    return contract


# Roles that orchestrate rather than implement: they must delegate via ops
# (create_issue, update_child_issue, create_interaction, set_status) and must
# NOT edit workspace files themselves. Everything else is an executor.
_ORCHESTRATION_ROLES = frozenset({"lead", "team_lead"})
# Tier 3 scouts inspect/report only — they never edit files.
_READ_ONLY_ROLES = frozenset({"worker", "file_scout", "web_scout", "context_curator"})


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
    is_quorum_auditor = role_key in {"quorum_auditor", "quorum_senior"}
    is_orchestrator = role_key in _ORCHESTRATION_ROLES and not is_solo_direct
    is_read_only = is_orchestrator or is_quorum_auditor or role_key in _READ_ONLY_ROLES
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
            '"catalog_id":"github-readonly|playwright-browser|filesystem-workspace",'
            '"justification":"<evidencia concreta>"}}'
            " — usa catalog_id cuando encaje; el sistema rellena el contrato revisado sin instalar nada. "
            "Para un descriptor ad-hoc envía name, source ejecutable local, version exacta y roles. "
            "Ejecutar código de terceros SIEMPRE espera al owner, nunca se auto-acepta. "
            "Antes de proponer, revisa si ya existe una propuesta/investigación igual (no dupliques research).",
            "  - Para dirigir a un hijo bloqueado → update_child_issue. Para cerrar la issue → "
            '{"type":"set_status","status":"done"}.',
            "Solo escribe un comentario (add_comment) para dejar constancia; el trabajo real va en `ops`.",
        ]
    elif is_quorum_auditor:
        parts += [
            "Eres un AUDITOR SENIOR independiente y reportas al Lead real del proyecto.",
            "No implementes, no edites archivos, no delegues y no dialogues con otros auditores.",
            "Audita únicamente el objetivo congelado y el Plan A incluidos en payload.quorum_review.",
            "Argumenta fortalezas, supuestos cuestionados, consecuencias, alternativas y trade-offs.",
            quorum_audit_contract_instruction(),
            "Finaliza con add_comment y set_status done; nunca uses accept_quorum_synthesis.",
        ]
    elif role.lower() == "context_curator":
        parts += [
            "Eres un CONTEXT CURATOR de solo lectura. No edites archivos ni delegues.",
            "Lee exclusivamente payload.context_curation_target y conserva decisiones, restricciones, riesgos, evidencia, owners y escalados.",
            "Checklist antes de responder: cada owner debe quedar unido explícitamente a su próximo entregable; "
            "cada reviewer/aceptador a su criterio o evidencia pendiente; cada umbral debe conservar métrica, valor, ventana y acción. "
            "Haz además una pasada de cobertura sobre todo el slice: conserva cada límite de alcance, regla de cohorte/rollout, "
            "regla de retry/recovery y resultado de verificación distintos. No omitas uno porque ya exista otro de la misma clase. "
            "No sustituyas esas relaciones por estados vagos como 'pendiente de revisión'.",
            "Tu artefacto obligatorio NO es add_comment: emite un op append_context_summary con path=target_issue_id, "
            "body=<síntesis causal>, start_comment_id, end_comment_id, char_count_original, start_char_offset y end_char_offset copiados exactamente del payload.",
            "Incluye causal_units compactas con id, kind, statement, source_comment_ids y links relation:value. "
            "Usa owner/deliverable/accepted_by para accountability; metric/threshold/window/action para escalados; "
            "reason para opciones descartadas. No inventes una unidad si el slice no contiene esa clase de información.",
            "Usa en source_comment_ids el conjunto mínimo de comentarios que demuestra cada unidad; no repitas todos los IDs del slice.",
            "Mantén body <= 30% de char_count_original y causal_units <= 4096 caracteres serializados. Después emite set_status done en la misma respuesta.",
            "Puedes usar add_comment solo como recibo breve adicional; nunca pongas la síntesis únicamente allí.",
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
        # stdout can contain a valid agy envelope followed by transport logs on
        # stderr. Extract JSON objects one by one, but route each recovered
        # object back through this adapter-specific normalizer so top-level ops
        # without status/summary receive the same treatment as clean stdout.
        text = str(raw_output).strip()
        decoder = json.JSONDecoder()
        start = text.find("{")
        while start >= 0:
            try:
                recovered, _ = decoder.raw_decode(text[start:])
                return _parse_antigravity_output(json.dumps(recovered, ensure_ascii=False))
            except (json.JSONDecodeError, ValueError):
                start = text.find("{", start + 1)
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
    # Observed with Claude Sonnet 4.6 through agy 1.1.5: the CLI may wrap the
    # complete assistant response in {"text": "..."}. This is a fallback only:
    # some envelopes contain both text and valid top-level ops, and the ops are
    # the authoritative structured work.
    if isinstance(parsed.get("text"), str):
        return _parse_antigravity_output(str(parsed["text"]))
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


def _extract_opencode_usage(jsonl: str) -> dict[str, Any] | None:
    """Aggregate OpenCode ``step_finish`` JSONL events.

    Cache and reasoning counters are dimensions of input/output rather than
    extra billable tokens, so they are reported separately and never added to
    the fallback total. When OpenCode provides ``tokens.total`` it remains the
    authoritative total for that step.
    """

    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
    }
    saw_step = False
    saw_explicit_total = False
    session_id: str | None = None
    reported_cost = 0.0
    for raw_line in str(jsonl or "").splitlines():
        try:
            event = json.loads(raw_line.strip())
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict) or event.get("type") != "step_finish":
            continue
        part = event.get("part")
        if not isinstance(part, dict) or part.get("type") != "step-finish":
            continue
        tokens = part.get("tokens")
        if not isinstance(tokens, dict):
            continue
        saw_step = True
        candidate_session = str(event.get("sessionID") or part.get("sessionID") or "").strip()
        if candidate_session:
            session_id = candidate_session
        totals["input_tokens"] += _nonnegative_int(tokens.get("input"))
        totals["output_tokens"] += _nonnegative_int(tokens.get("output"))
        totals["reasoning_output_tokens"] += _nonnegative_int(tokens.get("reasoning"))
        cache = tokens.get("cache")
        if isinstance(cache, dict):
            totals["cached_input_tokens"] += _nonnegative_int(cache.get("read"))
            totals["cache_write_tokens"] += _nonnegative_int(cache.get("write"))
        if tokens.get("total") is not None:
            saw_explicit_total = True
            totals["total_tokens"] += _nonnegative_int(tokens.get("total"))
        try:
            reported_cost += max(0.0, float(part.get("cost") or 0))
        except (TypeError, ValueError):
            pass
    if not saw_step:
        return None
    if not saw_explicit_total:
        totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    usage: dict[str, Any] = totals
    if session_id:
        usage["provider_session_id"] = session_id
    if reported_cost:
        # Diagnostic only. ExecutionResult.actual_cost_cents stays zero for a
        # free gateway; this field lets us detect a provider contract change.
        usage["provider_reported_cost"] = reported_cost
    return usage


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


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


def _parse_opencode_output(raw_output: str) -> dict[str, Any]:
    """Recover the final submit_work object from OpenCode's JSON event stream."""
    candidates: list[str] = []
    for line in str(raw_output or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            candidates.append(line)
            continue
        stack: list[Any] = [event]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if "status" in value and ("summary" in value or "ops" in value):
                    return value
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
            elif isinstance(value, str) and "{" in value:
                candidates.append(value)
    for candidate in reversed(candidates):
        try:
            return _parse_codex_output(candidate)
        except ValueError:
            continue
    raise ValueError("OpenCode event stream did not contain a submit_work object")


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
                    # Antigravity's --sandbox is not a read-only workspace
                    # guarantee. Non-editing roles receive the relevant files
                    # in AITEAM_WAKE_PAYLOAD_JSON and execute from the
                    # ephemeral prompt directory so direct writes cannot touch
                    # the project. Structured file ops remain subject to RBAC.
                    "cwd": str(prompt_dir) if self.runtime.sandbox == "read-only" else self.effective_cwd,
                    "read_output": lambda proc: ((proc.stdout or "") + (proc.stderr or "")),
                }
            if self.runtime.cli_kind == "opencode":
                # Keep the large AI Teams contract out of argv on Windows. The
                # attachment is read by the CLI before tool permissions apply;
                # the inline policy then limits the model to repository reads.
                self._tmpdir = tempfile.TemporaryDirectory(prefix="aiteam-opencode-")
                prompt_path = Path(self._tmpdir.name) / "prompt.txt"
                prompt_path.write_text(f"{system_prompt}\n\n{user_prompt}", encoding="utf-8")
                command = self.runtime._build_claude_command(
                    "",
                    "Follow the attached AI Teams contract. Return one JSON object with exactly "
                    "the top-level keys status, summary, and ops; return no Markdown or other text.",
                )
                # ``--file`` accepts multiple values. If it precedes the
                # positional message, OpenCode consumes that message as a
                # second path and fails before inference. Keep the message
                # first and append the attachment afterwards.
                command.extend(["--file", str(prompt_path)])
                policy = _opencode_inline_config(_mcp_servers_from_env(self.env))
                return {
                    "command": command,
                    "env_updates": {"OPENCODE_CONFIG_CONTENT": json.dumps(policy)},
                    "read_output": lambda proc: ((proc.stdout or "") + (proc.stderr or "")),
                }
            mcp_servers = _mcp_servers_from_env(self.env)
            mcp_config_path: str | None = None
            if self.runtime.cli_kind == "claude" and mcp_servers:
                self._tmpdir = tempfile.TemporaryDirectory(prefix="aiteam-claude-cli-")
                config_path = Path(self._tmpdir.name) / "mcp.json"
                config_path.write_text(
                    json.dumps(_claude_mcp_config(mcp_servers), ensure_ascii=False),
                    encoding="utf-8",
                )
                mcp_config_path = str(config_path)
            # Claude ``-p`` accepts the task prompt from stdin. Keeping the
            # changing wake payload out of argv avoids Windows CreateProcess
            # failures as the structured contract grows.
            prompt_via_stdin = self.runtime.cli_kind == "claude"
            command = self.runtime._build_claude_command(
                system_prompt,
                "" if prompt_via_stdin else user_prompt,
                mcp_config_path=mcp_config_path,
                mcp_servers=mcp_servers,
            )
            return {
                "command": command,
                "stdin_input": user_prompt if prompt_via_stdin else None,
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
            mcp_servers=_mcp_servers_from_env(self.env),
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


def _mcp_servers_from_env(env: dict[str, str]) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(env.get("AITEAM_MCP_SERVERS_JSON", "") or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _opencode_inline_config(servers: list[dict[str, Any]]) -> dict[str, Any]:
    """Translate AI Teams' per-tool MCP grant to an isolated OpenCode config."""
    permission: dict[str, Any] = {
        "*": "deny",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "lsp": "allow",
        "external_directory": "deny",
        "question": "deny",
        "task": "deny",
        "edit": "deny",
        "bash": "deny",
    }
    mcp: dict[str, Any] = {}
    for server in servers:
        name = str(server.get("name") or "").strip()
        command = str(server.get("command") or "").strip()
        args = server.get("args") or []
        enabled_tools = server.get("enabled_tools") or []
        if not name or not command or not isinstance(args, list) or not isinstance(enabled_tools, list):
            continue
        # Deny the entire namespace first; exact owner-approved tools override
        # it afterwards because OpenCode applies the last matching rule.
        permission[f"{name}_*"] = "deny"
        for tool in enabled_tools:
            tool_name = str(tool or "").strip()
            if tool_name:
                permission[f"{name}_{tool_name}"] = "allow"
        required = [str(key) for key in server.get("env_required") or [] if str(key).strip()]
        mcp[name] = {
            "type": "local",
            "command": [command, *[str(arg) for arg in args]],
            "enabled": True,
            "timeout": 5000,
            "environment": {key: "{env:" + key + "}" for key in required},
        }
    config: dict[str, Any] = {"share": "disabled", "permission": permission}
    if mcp:
        config["mcp"] = mcp
    return config


def _claude_mcp_config(servers: list[dict[str, Any]]) -> dict[str, Any]:
    configured: dict[str, Any] = {}
    for server in servers:
        name = str(server.get("name") or "").strip()
        command = str(server.get("command") or "").strip()
        args = server.get("args") or []
        if not name or not command or not isinstance(args, list):
            continue
        required = [str(key) for key in server.get("env_required") or []]
        configured[name] = {
            "type": "stdio",
            "command": command,
            "args": args,
            # Claude expands ${VAR} from the parent process. Values never enter
            # extensions.json, the prompt or this ephemeral file.
            "env": {key: "${" + key + "}" for key in required},
        }
    return {"mcpServers": configured}
