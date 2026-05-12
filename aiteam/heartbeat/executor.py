from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from aiteam.adapters.registry import AdapterRegistry, ExecutionResult
from aiteam.db.agents import create_agent
from aiteam.db.activity_log import log_activity
from aiteam.db.comments import create_comment
from aiteam.db.dependencies import resolve_blocker_wakeups, sync_default_child_dependencies
from aiteam.db.documents import DocumentConflict, get_document, put_document
from aiteam.db.issues import create_issue
from aiteam.db.finops import BudgetStatus, check_budget, current_period, record_cost
from aiteam.db.interactions import create_interaction, get_interaction, list_interactions
from aiteam.db.issues import get_issue, update_issue
from aiteam.db.runs import append_run_event, finish_run, mark_run_running
from aiteam.db.tool_access import record_tool_access
from aiteam.db.wake_payload import build_wake_payload, _parse_agent_report
from aiteam.db.wakeups import enqueue_wakeup, finish_wakeup
from aiteam.heartbeat.scheduler import DispatchResult
from aiteam.lead_intake import apply_accepted_team_proposal, build_team_proposal, format_team_proposal
from aiteam.project_adapters import choose_adapter_for_role, project_profiles, reconcile_project_agent_policy
from aiteam.run_liveness import (
    MAX_CONTINUATION_ATTEMPTS,
    LivenessResult,
    RunEvidence,
    _BUILTIN_ADAPTERS,
    classify_run_liveness,
    collect_run_evidence,
)
from aiteam.run_profiles import FULL_TEAM, normalize_run_profile
from aiteam.skills import load_skill
from aiteam.tools.catalog import check_capability, default_capabilities_for_role, get_agent_capabilities
from aiteam.adapters.subscription_cli_adapter import ClaudeSubscriptionCliRuntime
from aiteam.user_config import inject_adapter_secrets, resolve_adapter_config
from aiteam.workspace_evidence import WorkspaceDelta, diff_snapshots, snapshot_workspace, workspace_root_for_db


_TERMINAL_EXEC_STATUSES = {"completed", "failed", "skipped"}

_COMMENT_BODY_MAX = 4096  # hard limit stored in DB
_AGENT_REPORT_MARKER = "---AGENT-REPORT---"


def _safe_truncate_output(text: str, max_len: int = _COMMENT_BODY_MAX) -> str:
    """Truncate *text* to *max_len* while preserving any ``---AGENT-REPORT---`` block.

    The structured report block is the contract between child agents and the Lead.
    Losing it due to truncation causes the Lead to mis-read child state (treats as
    "no report", legacy mode) which can silently skip quality gates.

    Strategy:
    - If the text fits, return as-is.
    - If the text must be truncated, find the LAST ``---AGENT-REPORT---`` marker,
      reserve space for it, truncate the prose portion, and append the block.
    - If the block alone exceeds *max_len*, keep only the block (truncated).
    """
    if len(text) <= max_len:
        return text
    block_start = text.rfind(_AGENT_REPORT_MARKER)
    if block_start == -1:
        # No structured block — plain truncation is safe
        return text[:max_len]
    block = text[block_start:]
    separator = "\n\n[…output truncated…]\n\n"
    reserved = len(separator) + len(block)
    if reserved >= max_len:
        # Block alone exceeds limit — return just the block, possibly truncated
        return block[:max_len]
    prose_len = max_len - reserved
    return text[:prose_len] + separator + block

# Roles that need real workspace file content injected into their wake payload.
# API-only agents in these roles cannot read files themselves; without injection
# they hallucinate reviews and test results based only on the engineer's comment.
# Roles that receive full workspace_files in their wake payload.
# Reviewer/QA need files to do static analysis without hallucinating.
# file_scout's entire job is to read and summarise workspace files.
# Engineers also receive workspace_files so they can inspect existing code without
# blocking to ask the Lead for file contents — which would surface raw technical
# questions to the user, bypassing the communication chain.
_WORKSPACE_READER_ROLES = frozenset({
    "reviewer", "code_reviewer",
    "qa",
    "file_scout",
    "engineer", "software_engineer",
})


class RunExecutor:
    """Drives a DispatchResult from queued → running → finished."""

    def __init__(self, db_path: Path, registry: AdapterRegistry) -> None:
        self.db_path = Path(db_path)
        self.registry = registry

    def execute(self, dispatch: DispatchResult) -> None:
        run = dispatch.run
        wakeup = dispatch.wakeup_request
        run_id: str = run["id"]
        agent_id: str = run["agent_id"]
        wakeup_id: str = wakeup["id"]

        try:
            reconcile_project_agent_policy(self.db_path)
        except Exception:
            logger.warning("reconcile_project_agent_policy failed for db %s", self.db_path, exc_info=True)
        agent_info = self._agent_info(agent_id)
        adapter_type = (agent_info.get("adapter_type") if agent_info else None) or "manual"
        agent_role = (agent_info.get("role") if agent_info else None) or ""
        runtime = self.registry.get(adapter_type)
        # Per-agent model override via adapter_config_json
        adapter_cfg: dict[str, Any] = {}
        if runtime is not None and agent_info:
            adapter_cfg = resolve_adapter_config(adapter_type, _decode_json(agent_info.get("adapter_config_json") or "{}"))
            override_model = str(adapter_cfg.get("model") or "").strip()
            if override_model and adapter_type in {"anthropic_api", "anthropic_sonnet"}:
                from aiteam.adapters.anthropic_adapter import AnthropicApiRuntime
                if isinstance(runtime, AnthropicApiRuntime):
                    runtime = AnthropicApiRuntime(runtime.descriptor, model=override_model)
            elif override_model and adapter_type == "openai_api":
                from aiteam.adapters.openai_adapter import OpenAIResponsesRuntime
                if isinstance(runtime, OpenAIResponsesRuntime):
                    runtime = OpenAIResponsesRuntime(runtime.descriptor, model=override_model)
            elif override_model and adapter_type == "gemini_api":
                from aiteam.adapters.gemini_adapter import GeminiApiRuntime
                if isinstance(runtime, GeminiApiRuntime):
                    runtime = GeminiApiRuntime(runtime.descriptor, model=override_model)
            elif adapter_type == "subscription_cli" and isinstance(runtime, ClaudeSubscriptionCliRuntime):
                runtime = runtime.with_config(adapter_cfg)
        if runtime is None:
            record_tool_access(
                self.db_path,
                run_id=run_id,
                agent_id=agent_id,
                issue_id=str(run.get("issue_id") or "") or None,
                tool_name=f"adapter:{adapter_type}",
                decision="denied",
                reason="adapter not registered; falling back to manual",
                metadata={"requested_adapter_type": adapter_type},
            )
            runtime = self.registry.require("manual")

        ctx: dict[str, Any] = json.loads(run.get("context_snapshot_json") or "{}")
        issue_id_str = str(run.get("issue_id") or "")
        comment_id_str = str(ctx.get("wake_comment_id") or "")
        skill_content = load_skill(agent_role) if agent_role else None

        # Compute workspace_root early so reviewer/QA can get file context
        workspace_root = workspace_root_for_db(self.db_path)

        payload_json = ""
        if issue_id_str:
            try:  # noqa: SIM117  (nested try is intentional — outer catches payload build failure)
                payload = build_wake_payload(
                    self.db_path,
                    issue_id=issue_id_str,
                    comment_id=comment_id_str or None,
                    run_id=str(run.get("id") or ""),
                )
                payload["wake_context"] = ctx
                # ── Workspace files for reviewer/QA (prevents hallucinated reviews) ──
                # API-only reviewers and QA agents cannot read files themselves; inject
                # the actual workspace content into the wake payload so they work with
                # real code rather than hallucinating based on the engineer's comment.
                if agent_role in _WORKSPACE_READER_ROLES:
                    ws_files = _read_workspace_files(workspace_root)
                    if ws_files:
                        payload["workspace_files"] = ws_files
                # ── Workspace files for lead self-rescue (user approved) ─────────
                # When the user approves a lead_wants_file_read request, inject
                # real workspace content so an LLM lead can summarise files without
                # needing direct tool access (works for both api-only and CLI leads).
                if agent_role in {"lead", "team_lead"}:
                    _int_id = str(ctx.get("interaction_id") or "")
                    if (
                        ctx.get("wake_reason") == "interaction_resolved"
                        and ctx.get("action") == "accept"
                        and _int_id
                    ):
                        try:
                            _int = get_interaction(self.db_path, interaction_id=_int_id)
                            _int_payload = _decode_json((_int or {}).get("payload_json"))
                            if str(_int_payload.get("reason") or "") == "lead_wants_file_read":
                                ws_files = _read_workspace_files(workspace_root)
                                if ws_files:
                                    payload["workspace_files"] = ws_files
                        except Exception:
                            logger.warning("Failed to inject workspace_files for lead self-rescue (int_id=%r)", _int_id, exc_info=True)
                # ── Resolved interaction data for interaction_resolved wakes ──
                # When wake_reason=interaction_resolved, inject the resolved interaction
                # (including user_note) directly into the payload so the LLM lead can
                # act on it without needing to know the interaction_id to fetch it.
                wake_reason = ctx.get("wake_reason") or ""
                if wake_reason == "interaction_resolved":
                    _r_int_id = str(ctx.get("interaction_id") or "")
                    if _r_int_id:
                        try:
                            _r_int = get_interaction(self.db_path, interaction_id=_r_int_id)
                            if _r_int is None:
                                logger.warning("interaction_resolved: interaction %r not found in DB; resolved_interaction will be absent from payload", _r_int_id)
                            else:
                                _r_result = _decode_json(_r_int.get("result_json"))
                                _r_payload = _decode_json(_r_int.get("payload_json"))
                                payload["resolved_interaction"] = {
                                    "id": _r_int.get("id"),
                                    "kind": _r_int.get("kind"),
                                    "title": _r_int.get("title"),
                                    "action": ctx.get("action"),
                                    "reason": _r_payload.get("reason") if isinstance(_r_payload, dict) else None,
                                    "user_note": (_r_result or {}).get("resolution_data", {}).get("user_note"),
                                    "resolution_data": (_r_result or {}).get("resolution_data"),
                                }
                        except Exception:
                            logger.warning("Failed to inject resolved_interaction into payload (int_id=%r)", _r_int_id, exc_info=True)
                # ── Workspace listing for engineer continuation runs ──
                # On liveness_continuation the engineer already receives workspace_files
                # (full content) from the _WORKSPACE_READER_ROLES injection above.
                # workspace_listing (path + size only) is redundant in that case and
                # inflates the payload unnecessarily.  Only inject listing when full
                # content was not already provided.
                if agent_role in {"engineer", "software_engineer"} and wake_reason == "liveness_continuation":
                    if "workspace_files" not in payload:
                        ws_listing = _list_workspace_files(workspace_root)
                        if ws_listing:
                            payload["workspace_listing"] = ws_listing
                payload_json = json.dumps(payload, ensure_ascii=False)
            except Exception:
                logger.warning("Failed to build wake payload for run %r issue %r", run_id, issue_id_str, exc_info=True)

        wake_context: dict[str, object] = {
            "issue_id": issue_id_str,
            "reason": ctx.get("wake_reason") or "",
            "comment_id": comment_id_str,
            "agent_role": agent_role,
            "agent_skill": skill_content or "",
            "wake_payload_json": payload_json,
            # Interaction fields — populated when wake_reason=interaction_resolved
            "interaction_id": str(ctx.get("interaction_id") or ""),
            "interaction_action": str(ctx.get("action") or ""),
            "interaction_kind": str(ctx.get("kind") or ""),
        }

        gate = self._compliance_gate(run_id=run_id, issue_id=str(run.get("issue_id") or ""), agent_id=agent_id)
        if gate == "blocked":
            finish_wakeup(
                self.db_path,
                wakeup_id=wakeup_id,
                status="skipped",
                run_id=run_id,
                error="approval_required",
            )
            return
        if gate == "rejected":
            finish_run(
                self.db_path,
                run_id=run_id,
                status="failed",
                error="approval_rejected",
                error_code="approval_rejected",
            )
            finish_wakeup(
                self.db_path,
                wakeup_id=wakeup_id,
                status="failed",
                run_id=run_id,
                error="approval_rejected",
            )
            return
        budget_gate = self._budget_gate(run_id=run_id, issue_id=str(run.get("issue_id") or ""), agent_id=agent_id)
        if budget_gate == "blocked":
            finish_wakeup(
                self.db_path,
                wakeup_id=wakeup_id,
                status="skipped",
                run_id=run_id,
                error="budget_approval_required",
            )
            return
        if budget_gate == "rejected":
            finish_run(
                self.db_path,
                run_id=run_id,
                status="failed",
                error="budget_rejected",
                error_code="budget_rejected",
            )
            finish_wakeup(
                self.db_path,
                wakeup_id=wakeup_id,
                status="failed",
                run_id=run_id,
                error="budget_rejected",
            )
            return

        # ── Capability gate ────────────────────────────────────────────────
        # LLM / external adapters require at least one non-builtin capability.
        # Builtin roles (lead_builtin, role_builtin, manual) always pass.
        _llm_adapters = {"anthropic_api", "anthropic_sonnet", "openai_api", "gemini_api"}
        if adapter_type in _llm_adapters and agent_info:
            caps = get_agent_capabilities(agent_info)
            if not caps:
                # No capabilities set — record as denied but do NOT block execution;
                # this is informational (the agent may not have been configured yet).
                record_tool_access(
                    self.db_path,
                    run_id=run_id,
                    agent_id=agent_id,
                    issue_id=str(run.get("issue_id") or "") or None,
                    tool_name=f"adapter:{adapter_type}",
                    decision="warn",
                    reason="no capabilities configured for agent; LLM adapter running without explicit tool grants",
                    metadata={"adapter_type": adapter_type},
                )
            else:
                record_tool_access(
                    self.db_path,
                    run_id=run_id,
                    agent_id=agent_id,
                    issue_id=str(run.get("issue_id") or "") or None,
                    tool_name=f"adapter:{adapter_type}",
                    decision="allowed",
                    reason=f"capabilities: {', '.join(caps)}",
                    metadata={"adapter_type": adapter_type, "capabilities": caps},
                )

        effective_model = str(adapter_cfg.get("model") or runtime.descriptor.model or "").strip() or None
        self._record_run_adapter_metadata(
            run_id=run_id,
            adapter_type=runtime.descriptor.adapter_type,
            provider=runtime.descriptor.provider,
            model=effective_model,
            channel=runtime.descriptor.channel,
        )
        mark_run_running(self.db_path, run_id=run_id)
        record_tool_access(
            self.db_path,
            run_id=run_id,
            agent_id=agent_id,
            issue_id=str(run.get("issue_id") or "") or None,
            tool_name=f"adapter:{runtime.descriptor.adapter_type}",
            decision="allowed",
            reason="adapter selected for run execution",
            metadata={
                "requested_adapter_type": adapter_type,
                "channel": runtime.descriptor.channel,
                "provider": runtime.descriptor.provider,
                "model": effective_model,
            },
        )

        env = runtime.build_env(run_id=run_id, wake_context=wake_context)
        if adapter_cfg.get("model"):
            env = {
                **env,
                "AITEAM_OPENAI_MODEL": str(adapter_cfg["model"]),
                "AITEAM_GEMINI_MODEL": str(adapter_cfg["model"]),
            }
        env = inject_adapter_secrets(env, adapter_type, adapter_cfg)
        # workspace_root already computed above (before payload building)
        # Expose workspace root so CLI adapters (codex, claude, gemini) know
        # which directory to operate in when cwd is not explicitly configured.
        env = {**env, "AITEAM_WORKSPACE_ROOT": str(workspace_root)}
        before_workspace = snapshot_workspace(workspace_root)
        result: ExecutionResult
        try:
            _llm_adapters = {"anthropic_api", "anthropic_sonnet", "openai_api", "gemini_api"}
            if adapter_type in _llm_adapters:
                # Real LLM adapter — skip builtin path entirely
                result = runtime.execute(run, env)
            elif adapter_type == "lead_builtin" or (adapter_type == "manual" and agent_role in {"lead", "team_lead"}):
                result = self._execute_builtin_lead(run=run, agent_id=agent_id, context=ctx)
            elif adapter_type == "role_builtin":
                result = self._execute_builtin_role(run=run, agent_id=agent_id, role=agent_role)
            else:
                result = runtime.execute(run, env)
        except Exception as exc:
            result = ExecutionResult(status="failed", error=str(exc), exit_code=1)

        # ── Execute file ops from LLM result BEFORE snapshotting workspace ───
        # API-only adapters (openai_api, anthropic_api, gemini_api) return
        # write_file / append_file / delete_file ops in their structured output.
        # Materializing them here lets workspace_delta see them as real changes.
        file_ops_applied = _execute_file_ops(
            file_ops=(result.actions or {}).get("file_ops") or [],
            workspace_root=workspace_root,
        )
        if file_ops_applied:
            append_run_event(
                self.db_path,
                run_id=run_id,
                event_type="file_ops",
                stream="system",
                payload={"count": len(file_ops_applied), "paths": file_ops_applied},
            )

        workspace_delta = diff_snapshots(before_workspace, snapshot_workspace(workspace_root))

        # ── Step 1: Write output comment to DB (before evidence collection) ─
        if result.output:
            append_run_event(
                self.db_path,
                run_id=run_id,
                event_type="output",
                stream="stdout",
                payload={"text": _safe_truncate_output(result.output)},
            )
            if issue_id_str:
                comment = create_comment(
                    self.db_path,
                    issue_id=issue_id_str,
                    author_agent_id=agent_id,
                    source_run_id=run_id,
                    body=_safe_truncate_output(result.output),
                    metadata={"source": "run_executor", "event_type": "output"},
                )
                log_activity(
                    self.db_path,
                    action="comment.created",
                    target_type="comment",
                    target_id=comment["id"],
                    actor_agent_id=agent_id,
                    run_id=run_id,
                    payload={"issue_id": issue_id_str, "source": "run_executor"},
                )
                self._maybe_materialize_plan_comment(
                    issue_id=issue_id_str,
                    agent_id=agent_id,
                    run_id=run_id,
                    body=result.output,
                )

        # ── Step 2: Apply the adapter's own result actions ───────────────────
        self._apply_result_actions(run=run, agent_id=agent_id, result=result)

        # ── Step 3: Collect structured evidence from DB (post-comment) ───────
        workspace_files_changed = len(workspace_delta.created) + len(workspace_delta.modified)
        evidence = collect_run_evidence(
            self.db_path,
            run_id=run_id,
            workspace_files_changed=workspace_files_changed,
        )

        # ── Step 4: Classify liveness (pure function, no regex) ───────────────
        useful_output = bool(str(result.output or "").strip())
        has_explicit_issue_status = bool((result.actions or {}).get("issue_status"))
        exec_status = result.status if result.status in _TERMINAL_EXEC_STATUSES else "completed"
        liveness_result = classify_run_liveness(
            run_status=exec_status,
            evidence=evidence,
            adapter_type=adapter_type,
            agent_role=agent_role,
            useful_output=useful_output,
            has_explicit_issue_status=has_explicit_issue_status,
            continuation_attempt=_safe_int(ctx.get("continuation_attempt")),
            max_continuation_attempts=MAX_CONTINUATION_ATTEMPTS,
        )

        # ── Step 5: Apply liveness override actions (blocked, auto-close) ────
        if liveness_result.actions_override:
            override_result = ExecutionResult(
                status=result.status,
                output=None,
                actions=liveness_result.actions_override,
            )
            self._apply_result_actions(run=run, agent_id=agent_id, result=override_result)

        # ── Step 6: Record workspace evidence event (for blocked + advanced) ──
        is_engineering = str(agent_role or "").strip().lower() in {"engineer", "software_engineer"}
        is_builtin = adapter_type in _BUILTIN_ADAPTERS
        should_record_evidence = (
            is_engineering
            and not is_builtin
            and (workspace_delta.changed or liveness_result.state in {"blocked", "advanced"})
        )
        if should_record_evidence:
            self._record_run_evidence_event(
                run=run,
                agent_id=agent_id,
                issue_id=issue_id_str or None,
                workspace_delta=workspace_delta,
                liveness_result=liveness_result,
            )

        final_status = result.status if result.status in _TERMINAL_EXEC_STATUSES else "completed"
        finished = finish_run(
            self.db_path,
            run_id=run_id,
            status=final_status,
            exit_code=result.exit_code,
            error=result.error,
            error_code=result.error_code,
            usage=result.usage,
            actual_cost_cents=result.actual_cost_cents,
            result={"output_preview": result.output[:256]} if result.output else None,
        )
        if finished is not None:
            self._persist_liveness(
                run_id=run_id,
                liveness_state=liveness_result.state,
                liveness_reason=liveness_result.reason,
            )
        if liveness_result.needs_continuation:
            self._enqueue_liveness_continuation(
                run=run,
                agent_id=agent_id,
                source_run_id=run_id,
                liveness_state=liveness_result.state,
                liveness_reason=liveness_result.reason,
                continuation_attempt=liveness_result.continuation_attempt,
            )
        if int(result.actual_cost_cents or 0) > 0:
            record_cost(
                self.db_path,
                run_id=run_id,
                agent_id=agent_id,
                amount_cents=result.actual_cost_cents,
                metadata={"source": "run_executor"},
            )

        wakeup_terminal = "finished" if final_status in {"completed", "skipped"} else "failed"
        finish_wakeup(
            self.db_path,
            wakeup_id=wakeup_id,
            status=wakeup_terminal,
            run_id=run_id,
            error=result.error if wakeup_terminal == "failed" else None,
        )

    def _execute_builtin_lead(self, *, run: dict[str, Any], agent_id: str, context: dict[str, Any]) -> ExecutionResult:
        issue_id = str(run.get("issue_id") or "")
        if not issue_id:
            return ExecutionResult(status="skipped", output="Lead wake without issue context; no action taken.")

        if (
            context.get("wake_reason") == "interaction_resolved"
            and context.get("kind") == "suggest_tasks"
            and context.get("action") == "accept"
        ):
            interaction_id = str(context.get("interaction_id") or "")
            if not interaction_id:
                logger.warning("interaction_resolved+suggest_tasks: missing interaction_id in context; skipping")
                return ExecutionResult(status="skipped", output="Lead — interaction_resolved recibida sin interaction_id; no se puede aplicar la propuesta.")
            interaction = get_interaction(self.db_path, interaction_id=interaction_id)
            if interaction is None:
                logger.error("interaction_resolved+suggest_tasks: interaction %r not found in DB", interaction_id)
                return ExecutionResult(
                    status="failed",
                    error=f"interaction not found: {interaction_id}",
                    error_code="interaction_not_found",
                    output=f"Lead — Error interno: la interaction {interaction_id!r} no se encontró en la base de datos. No se puede aplicar la propuesta.",
                )
            payload = _decode_json(interaction.get("payload_json"))
            # User may have modified the proposal before accepting — prefer resolution_data
            result_obj = _decode_json(interaction.get("result_json"))
            if result_obj.get("resolution_data"):
                rd = result_obj["resolution_data"]
                if rd.get("proposed_team"):
                    payload = {**payload, "proposed_team": rd["proposed_team"]}
                if rd.get("suggested_issues"):
                    payload = {**payload, "suggested_issues": rd["suggested_issues"]}
            outcome = apply_accepted_team_proposal(
                self.db_path,
                parent_issue_id=issue_id,
                proposal=payload,
                source_run_id=str(run.get("id")),
            )
            created_agents = ", ".join(outcome["created_agents"]) or "sin agentes nuevos"
            created_issues = ", ".join(outcome["created_issues"]) or "sin issues nuevas"
            return ExecutionResult(
                status="completed",
                output=(
                    "Propuesta aceptada. He creado el equipo y el primer backlog estructurado.\n\n"
                    f"Agentes: {created_agents}\n"
                    f"Issues: {created_issues}"
                ),
            )
        if (
            context.get("wake_reason") == "interaction_resolved"
            and context.get("kind") == "request_confirmation"
        ):
            interaction_id = str(context.get("interaction_id") or "")
            if not interaction_id:
                logger.warning("interaction_resolved+request_confirmation: missing interaction_id in context")
                return ExecutionResult(status="skipped", output="Lead — interaction_resolved recibida sin interaction_id; no se puede procesar.")
            interaction = get_interaction(self.db_path, interaction_id=interaction_id)
            if interaction is None:
                logger.error("interaction_resolved+request_confirmation: interaction %r not found in DB", interaction_id)
                return ExecutionResult(
                    status="skipped",
                    output=(
                        f"Lead — La interaction {interaction_id!r} ya no existe en la base de datos "
                        "(posiblemente expirada o procesada por otra run). "
                        "Si esperabas una acción, vuelve a enviar tu respuesta."
                    ),
                )
            payload = _decode_json(interaction.get("payload_json"))
            reason = str(payload.get("reason") or "")
            action = str(context.get("action") or "")

            # ── Lead self-rescue: user approved direct file read ─────────────
            if reason == "lead_wants_file_read":
                if action == "accept":
                    return self._handle_lead_self_file_read(issue_id, run, agent_id)
                if action == "reject":
                    return ExecutionResult(
                        status="completed",
                        output=(
                            "Lead — Lectura directa rechazada\n\n"
                            "El usuario no ha autorizado la lectura directa de archivos. "
                            "El scout permanece bloqueado. Puedes cambiar el adapter del scout a uno "
                            "con acceso al workspace, o pegar el contenido relevante en el thread."
                        ),
                    )

            if reason == "initial_cycle_ready":
                if action == "accept":
                    return ExecutionResult(
                        status="completed",
                        output=(
                            "Ciclo inicial cerrado por el Lead\n\n"
                            "La primera ronda queda aceptada: plan, implementación inicial, revisión y QA "
                            "han reportado. Marco la issue padre como done y dejo el proyecto listo para "
                            "una nueva tarea o una segunda ronda explícita."
                        ),
                        actions={"issue_status": "done"},
                    )
                if action == "reject":
                    return ExecutionResult(
                        status="completed",
                        output=(
                            "Ciclo inicial mantenido abierto\n\n"
                            "La revisión fue rechazada. Mantengo la issue padre en progreso y espero "
                            "comentarios concretos del usuario para replanificar sin añadir ruido."
                        ),
                        actions={"issue_status": "in_progress"},
                    )

            if reason == "child_blocked_requires_action":
                # User acknowledged a blocked-child escalation; nothing more for builtin lead to do.
                # The child issue remains blocked until adapter is changed or the user resolves it.
                return ExecutionResult(
                    status="completed",
                    output=(
                        "Lead — Acuse de recibo de bloqueo\n\n"
                        f"Confirmación recibida (acción: {action}). "
                        "El issue hijo bloqueado sigue esperando intervención. "
                        "Cambia el adapter del agente bloqueado a CLI/local o cancela la delegación."
                    ),
                )

            if reason == "reviewer_fix_cycle_limit":
                return self._handle_fix_cycle_limit_resolved(
                    issue_id=issue_id,
                    action=action,
                    interaction_payload=payload,
                    run=run,
                    agent_id=agent_id,
                )

            # Unknown reason — log and return diagnostic rather than falling through to proposal logic
            if reason:
                logger.warning(
                    "interaction_resolved+request_confirmation: unrecognized reason %r (action=%r) for issue %r",
                    reason, action, issue_id,
                )
                return ExecutionResult(
                    status="completed",
                    output=(
                        f"Lead — Interaction resuelta (razón: {reason!r}, acción: {action!r})\n\n"
                        "No tengo lógica específica para esta razón. "
                        "Si esperabas una acción concreta, describe en el thread qué quieres que haga."
                    ),
                )

            # reason is empty — interaction payload missing the 'reason' field (Bug #5 / old data)
            logger.warning(
                "interaction_resolved+request_confirmation: interaction %r has no 'reason' in payload "
                "(interaction may have been created without the mandatory reason field)",
                interaction_id,
            )
            return ExecutionResult(
                status="skipped",
                output=(
                    "Lead — Interaction resuelta sin campo 'reason' en el payload. "
                    "No se puede determinar qué acción tomar. "
                    "Asegúrate de que futuras interactions incluyan el campo 'reason'."
                ),
            )

        issue = get_issue(self.db_path, issue_id=issue_id)
        if issue is None:
            return ExecutionResult(status="failed", error=f"issue not found: {issue_id}", exit_code=1)
        if context.get("wake_reason") == "child_report":
            if self._cycle_review_state(issue_id) is not None:
                return ExecutionResult(status="completed")

            # ── Tier 3 scout blocked → offer lead file-read self-rescue ──────
            # Scouts (file_scout, web_scout, context_curator) cannot self-escalate
            # to the user; the Lead must mediate.  If any scout reports blocked,
            # ask the user for permission to read files directly before falling
            # back to the generic blocked-child escalation.
            tier3_blocked = self._tier3_blocked_scouts(issue_id)
            if tier3_blocked:
                file_read_state = self._lead_file_read_interaction_state(issue_id)
                if file_read_state is None:
                    scout_names = ", ".join(
                        str(r.get("title") or r.get("role") or r.get("id")) for r in tier3_blocked
                    )
                    return ExecutionResult(
                        status="completed",
                        output=(
                            f"Lead — Scout bloqueado\n\n"
                            f"{len(tier3_blocked)} scout(s) sin acceso al workspace: {scout_names}.\n\n"
                            "¿Autorizas al Lead a leer los archivos directamente? "
                            "Usará tokens senior pero desbloqueará la planificación inmediatamente."
                        ),
                        actions={
                            "interactions": [
                                {
                                    "kind": "request_confirmation",
                                    "payload": {
                                        "version": 1,
                                        "reason": "lead_wants_file_read",
                                        "parent_issue_id": issue_id,
                                        "blocked_scouts": [
                                            {
                                                "id": r["id"],
                                                "title": r.get("title"),
                                                "role": r.get("role"),
                                                "blocker": (r.get("last_agent_report") or {}).get("blocker"),
                                            }
                                            for r in tier3_blocked
                                        ],
                                    },
                                    "title": "Scout bloqueado — ¿el Lead lee los archivos?",
                                    "summary": (
                                        f"{len(tier3_blocked)} scout(s) bloqueado(s) sin acceso a archivos. "
                                        "Acepta para que el Lead lea el workspace directamente (tokens senior). "
                                        "Rechaza para esperar a que el scout tenga acceso."
                                    ),
                                    "idempotency_key": f"lead:file-read-request:{issue_id}",
                                }
                            ]
                        },
                    )
                if file_read_state == "pending":
                    return ExecutionResult(status="skipped", error="waiting_for_file_read_approval")
                if file_read_state == "accepted":
                    return self._handle_lead_self_file_read(issue_id, run, agent_id)
                # rejected: fall through to normal escalation / supervisor summary

            # ── Escalate non-scout blocked children ───────────────────────────
            # Tier 3 scouts are handled above; filter them out of the generic
            # blocked escalation so they don't generate a second, misleading
            # "change adapter" message.
            blocked_rows = [
                r for r in self._blocked_child_rows(issue_id)
                if str(r.get("role") or "").strip().lower() not in self._TIER3_ROLES
            ]
            if blocked_rows:
                escalation_output = self._format_blocked_escalation(blocked_rows)
                return ExecutionResult(
                    status="completed",
                    output=escalation_output,
                    actions={
                        "interactions": [
                            {
                                "kind": "request_confirmation",
                                "payload": {
                                    "version": 1,
                                    "reason": "child_blocked_requires_action",
                                    "parent_issue_id": issue_id,
                                    "blocked_children": [
                                        {
                                            "id": r["id"],
                                            "title": r["title"],
                                            "assignee_agent_id": r.get("assignee_agent_id"),
                                            "liveness_reason": r.get("liveness_reason"),
                                        }
                                        for r in blocked_rows
                                    ],
                                },
                                "title": "Issue hija bloqueada — acción requerida",
                                "summary": (
                                    f"{len(blocked_rows)} issue(s) hija(s) bloqueada(s). "
                                    "Reasigna el adapter a CLI/local o cancela la delegación."
                                ),
                                "idempotency_key": f"lead:blocked-child:{issue_id}",
                            }
                        ]
                    },
                )
            # ── Auto-create fix engineer on reviewer changes_requested ────────────
            # If a reviewer completed with result=changes_requested AND there is no
            # open engineer fix issue, reset the reviewer to todo and create a new
            # engineer issue with the reviewer's findings.  sync_default_child_dependencies
            # wires the reviewer → fix_engineer dependency so the reviewer is woken
            # automatically when the engineer finishes.
            _fix_result = self._handle_reviewer_changes_requested(issue_id, agent_id, run)
            if _fix_result is not None:
                return _fix_result
            # ── Auto-spawn context curator when thread grows long ─────────────
            # Silently creates a context_curator child when the comment count
            # crosses the threshold AND no plan document AND no active curator.
            # Does not short-circuit — the rest of the child_report logic runs.
            self._maybe_spawn_context_curator(issue_id, agent_id, run)
            actions: dict[str, Any] = {}
            if self._all_children_done(issue_id):
                # Cancel any stale child_blocked_requires_action interaction —
                # children are now done so the notification is obsolete, and
                # the pending count gate would otherwise block cycle-close.
                self._cancel_stale_interaction(issue_id, reason="child_blocked_requires_action")
                try:
                    cycle_summary = self._format_cycle_close_summary(issue_id)
                except Exception:
                    logger.warning("_format_cycle_close_summary failed for issue %s", issue_id, exc_info=True)
                    cycle_summary = "Todas las delegaciones completadas. Acepta para cerrar el ciclo."
                actions["interactions"] = [
                    {
                        "kind": "request_confirmation",
                        "payload": {
                            "version": 1,
                            "reason": "initial_cycle_ready",
                            "parent_issue_id": issue_id,
                        },
                        "title": "Revisar ciclo inicial — ¿se cumplió el objetivo?",
                        "summary": cycle_summary,
                        "idempotency_key": f"lead:cycle-review:{issue_id}",
                    }
                ]
            try:
                supervisor_summary = self._format_supervisor_summary(issue_id)
            except Exception:
                logger.warning("_format_supervisor_summary failed for issue %s", issue_id, exc_info=True)
                supervisor_summary = "Resumen del Lead — error al generar el sumario. Revisa los logs."
            return ExecutionResult(
                status="completed",
                output=supervisor_summary,
                actions=actions,
            )
        cycle_review_state = self._cycle_review_state(issue_id)
        if cycle_review_state == "accepted" and str(issue.get("status") or "") != "done":
            return ExecutionResult(
                status="completed",
                output=(
                    "Ciclo recuperado por el Lead\n\n"
                    "La confirmacion de cierre ya estaba aceptada y las delegaciones hijas estan completas. "
                    "Marco la issue padre como done para reconciliar el estado del proyecto."
                ),
                actions={"issue_status": "done"},
            )
        if cycle_review_state == "rejected" and str(issue.get("status") or "") != "in_progress":
            return ExecutionResult(
                status="completed",
                output=(
                    "Ciclo mantenido abierto por el Lead\n\n"
                    "La confirmacion de cierre fue rechazada. Mantengo la issue padre en progreso y espero "
                    "instrucciones concretas en el thread."
                ),
                actions={"issue_status": "in_progress"},
            )
        proposal_st = self._proposal_state(issue_id)
        if proposal_st in {"pending", "accepted"} or self._has_progressing_children(issue_id):
            return ExecutionResult(status="skipped", error="no_pending_lead_work")
        # ── Pre-proposal: escalate blocked children (any wake reason) ─────────
        # If children exist but all are blocked, we must escalate rather than
        # re-propose a new team on top of the broken one.
        # Tier 3 scouts are excluded — their blocked state is handled separately
        # via the lead_wants_file_read flow and should not prevent re-proposals.
        blocked_rows = [
            r for r in self._blocked_child_rows(issue_id)
            if str(r.get("role") or "").strip().lower() not in self._TIER3_ROLES
        ]
        if blocked_rows:
            escalation_output = self._format_blocked_escalation(blocked_rows)
            return ExecutionResult(
                status="completed",
                output=escalation_output,
                actions={
                    "interactions": [
                        {
                            "kind": "request_confirmation",
                            "payload": {
                                "version": 1,
                                "reason": "child_blocked_requires_action",
                                "parent_issue_id": issue_id,
                                "blocked_children": [
                                    {
                                        "id": r["id"],
                                        "title": r["title"],
                                        "assignee_agent_id": r.get("assignee_agent_id"),
                                        "liveness_reason": r.get("liveness_reason"),
                                    }
                                    for r in blocked_rows
                                ],
                            },
                            "title": "Issue hija bloqueada — acción requerida",
                            "summary": (
                                f"{len(blocked_rows)} issue(s) hija(s) bloqueada(s). "
                                "Reasigna el adapter a CLI/local o cancela la delegación."
                            ),
                            "idempotency_key": f"lead:blocked-child:{issue_id}",
                        }
                    ]
                },
            )
        if issue_id.endswith(":plan"):
            plan_body = (
                "Plan detallado del Lead\n\n"
                "1. Mantener el objetivo visible en la issue padre y trabajar por issues hijas pequeñas.\n"
                "2. Delegar implementación clara al Engineer para ahorrar contexto senior.\n"
                "3. Usar Reviewer para buscar riesgos de arquitectura, regresiones y supuestos frágiles.\n"
                "4. Usar QA para evidencia suficiente, evitando gates ceremoniales o repetitivos.\n"
                "5. Cada rol reporta al Lead; el Lead conserva decisiones, prioridades y desbloqueos.\n\n"
                "Riesgos previstos: alcance ambiguo, assets/jugabilidad insuficientemente especificados, "
                "y cierre prematuro sin prueba ejecutable."
            )
            run_id_str = str(run.get("id") or "")
            if get_document(self.db_path, issue_id=issue_id, key="plan") is None:
                try:
                    put_document(
                        self.db_path,
                        issue_id=issue_id,
                        key="plan",
                        title="Plan del Lead",
                        body=plan_body,
                        format="markdown",
                        run_id=run_id_str or None,
                    )
                except DocumentConflict:
                    pass
            return ExecutionResult(
                status="completed",
                output=plan_body,
                actions={"issue_status": "done", "notify_supervisor": True},
            )
        proposal = build_team_proposal(issue, adapter_profiles=project_profiles(Path(self.db_path).parent))
        plan_body = format_team_proposal(proposal)
        run_id_str = str(run.get("id") or "")
        # Write plan as durable document — idempotent on first intake
        existing_doc = get_document(self.db_path, issue_id=issue_id, key="plan")
        if existing_doc is None:
            try:
                plan_doc = put_document(
                    self.db_path,
                    issue_id=issue_id,
                    key="plan",
                    title="Plan inicial del Lead",
                    body=plan_body,
                    format="markdown",
                    run_id=run_id_str or None,
                )
            except DocumentConflict:
                plan_doc = get_document(self.db_path, issue_id=issue_id, key="plan") or {}
        else:
            plan_doc = existing_doc
        proposal["plan_revision_id"] = plan_doc.get("current_revision_id") or ""

        # solo_lead: skip suggest_tasks — apply directly
        if proposal.get("direct_work"):
            outcome = apply_accepted_team_proposal(
                self.db_path,
                parent_issue_id=issue_id,
                proposal=proposal,
                source_run_id=run_id_str,
            )
            created_issues = ", ".join(outcome["created_issues"]) or "ninguna"
            return ExecutionResult(
                status="completed",
                output=(
                    f"{plan_body}\n\n"
                    f"Modo solo_lead: he creado las issues directamente.\n"
                    f"Issues: {created_issues}"
                ),
            )

        profile = proposal.get("profile", "full_team")
        if profile == "lead_quorum":
            title_str = "Plan y equipo de quorum propuestos"
            summary_str = (
                "El Lead propone un equorum de revisión antes de ejecutar. "
                "Aceptar crea los auditores e issues de revisión; rechazar deja el proyecto esperando ajustes."
            )
        else:
            title_str = "Plan inicial y equipo propuesto"
            summary_str = (
                "El Lead propone crear un equipo de programación y un backlog inicial. "
                "Aceptar crea agentes e issues; rechazar deja el proyecto esperando ajustes."
            )

        return ExecutionResult(
            status="completed",
            output=plan_body,
            actions={
                "interactions": [
                    {
                        "kind": "suggest_tasks",
                        "payload": proposal,
                        "title": title_str,
                        "summary": summary_str,
                        "idempotency_key": f"lead:intake-proposal:{issue_id}",
                    }
                ]
            },
        )

    def _record_run_evidence_event(
        self,
        *,
        run: dict[str, Any],
        agent_id: str,
        issue_id: str | None,
        workspace_delta: WorkspaceDelta,
        liveness_result: LivenessResult,
    ) -> None:
        """Persist a workspace_evidence run event for observability.

        Only emitted for engineering runs where something notable happened
        (workspace changes or a blocked liveness state).  Not emitted for
        normal plan_only / empty_response continuations to avoid cluttering
        the activity log.
        """
        run_id = str(run.get("id") or "")
        if not run_id:
            return
        payload = {
            "changed": workspace_delta.changed,
            "delta": workspace_delta.to_dict(),
            "liveness_state": liveness_result.state,
            "liveness_reason": liveness_result.reason,
        }
        try:
            append_run_event(
                self.db_path,
                run_id=run_id,
                event_type="workspace_evidence",
                stream="system",
                payload=payload,
            )
            # Only log to activity for blocked outcomes (keeps activity log clean)
            if liveness_result.state == "blocked":
                log_activity(
                    self.db_path,
                    action="workspace.evidence_recorded",
                    target_type="run",
                    target_id=run_id,
                    actor_agent_id=agent_id,
                    run_id=run_id,
                    payload={"issue_id": issue_id, **payload},
                )
        except Exception:
            logger.warning("_record_run_evidence_event failed for run %s", run_id, exc_info=True)

    def _persist_liveness(self, *, run_id: str, liveness_state: str, liveness_reason: str) -> None:
        with contextlib.closing(_connect(self.db_path)) as conn:
            conn.execute(
                """
                UPDATE runs
                SET liveness_state = ?,
                    liveness_reason = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (liveness_state[:80], liveness_reason[:500], run_id),
            )

    def _enqueue_liveness_continuation(
        self,
        *,
        run: dict[str, Any],
        agent_id: str,
        source_run_id: str,
        liveness_state: str,
        liveness_reason: str,
        continuation_attempt: int = 0,
    ) -> None:
        """Enqueue a bounded liveness continuation wakeup.

        Only ``plan_only`` and ``empty_response`` states are continuable.
        Max ``MAX_CONTINUATION_ATTEMPTS`` attempts before escalating to blocked.
        Idempotency key prevents duplicate wakeups for the same run+state.
        """
        if liveness_state not in {"plan_only", "empty_response"}:
            return
        issue_id = str(run.get("issue_id") or "").strip()
        if not issue_id:
            return
        next_attempt = continuation_attempt + 1
        if liveness_state == "plan_only":
            instruction = (
                "La run anterior produjo solo texto/plan sin cambios verificables en el workspace. "
                "Esta continuación debe crear o modificar archivos reales fuera de .aiteam, "
                "o declarar bloqueo explícito indicando qué adapter CLI/local es necesario."
            )
        else:
            instruction = (
                "La run anterior terminó sin output ni evidencia concreta. "
                "Produce una respuesta significativa: modifica archivos, escribe un plan detallado, "
                "o declara bloqueo explícito."
            )
        enqueue_wakeup(
            self.db_path,
            agent_id=agent_id,
            source="automation",
            reason="liveness_continuation",
            trigger_detail=f"source_run:{source_run_id}:{liveness_state}",
            payload={
                "issue_id": issue_id,
                "wake_reason": "liveness_continuation",
                "source_run_id": source_run_id,
                "liveness_state": liveness_state,
                "liveness_reason": liveness_reason,
                "continuation_attempt": next_attempt,
                "max_continuation_attempts": MAX_CONTINUATION_ATTEMPTS,
                "instruction": instruction,
            },
            idempotency_key=f"liveness_continuation:{issue_id}:{source_run_id}:{liveness_state}:{next_attempt}",
        )

    def _execute_builtin_role(self, *, run: dict[str, Any], agent_id: str, role: str) -> ExecutionResult:
        issue_id = str(run.get("issue_id") or "")
        issue = get_issue(self.db_path, issue_id=issue_id) if issue_id else None
        if issue is None:
            return ExecutionResult(status="failed", error=f"issue not found: {issue_id}", exit_code=1)

        title = str(issue.get("title") or issue_id)
        if role == "engineer":
            output = (
                f"Engineer intake para: {title}\n\n"
                "He recibido la delegación. Antes de implementar necesito concretar el primer vertical jugable: "
                "loop principal, tecnología objetivo, assets mínimos y criterio de aceptación. "
                "Puedo absorber la lectura y tareas mecánicas para reservar el contexto del Lead."
            )
        elif role == "reviewer":
            output = (
                f"Reviewer intake para: {title}\n\n"
                "Riesgos principales a vigilar: plan demasiado amplio, dependencias no ordenadas, falta de una demo "
                "ejecutable, y revisiones que bloqueen sin evidencia. Revisaré decisiones y qué puede romper la "
                "siguiente run."
            )
        elif role == "qa":
            output = (
                f"QA intake para: {title}\n\n"
                "Propongo una verificación ligera: arrancar la app/juego, validar flujo principal, capturar errores "
                "visibles y registrar evidencia mínima. Sin gates fuertes salvo riesgo alto."
            )
        else:
            output = f"{role or agent_id} intake para: {title}\n\nDelegación recibida y lista para ejecutar."
        return ExecutionResult(
            status="completed",
            output=output,
            actions={"issue_status": "done", "notify_supervisor": True},
        )

    def _apply_result_actions(self, *, run: dict[str, Any], agent_id: str, result: ExecutionResult) -> None:
        actions = result.actions or {}
        issue_id = str(run.get("issue_id") or "")
        if not issue_id:
            return
        # ── Interaction gate: max 1 pending interaction per issue at a time ─────
        # Presenting multiple confirmation popups simultaneously confuses users:
        # accepting one triggers a new run before the others are answered,
        # creating cascading duplicate wakes.  We count all non-terminal
        # interactions already in the DB and skip new ones if any are pending.
        # We also allow at most one *new* creation per run so that a single
        # agent heartbeat cannot flood the user with back-to-back popups.
        _TERMINAL_INTERACTION_STATUSES = {"accepted", "rejected", "answered", "cancelled", "expired"}
        try:
            _existing_interactions = list_interactions(self.db_path, issue_id=issue_id)
            _pending_count = sum(
                1 for _i in _existing_interactions
                if str(_i.get("status") or "") not in _TERMINAL_INTERACTION_STATUSES
            )
        except Exception:
            logger.warning("Could not count pending interactions for issue %s", issue_id, exc_info=True)
            _pending_count = 0
        _created_this_run = 0

        for interaction in actions.get("interactions") or []:
            # If there is already a pending interaction (pre-existing or just created
            # in this same run), skip the rest — the user can only answer one at a time.
            if _pending_count + _created_this_run > 0:
                logger.warning(
                    "Skipping interaction creation — %d pending already exist for issue %s (title=%r). "
                    "Will be retried in a future heartbeat if still needed.",
                    _pending_count + _created_this_run,
                    issue_id,
                    interaction.get("title"),
                )
                continue
            created = create_interaction(
                self.db_path,
                issue_id=issue_id,
                kind=interaction["kind"],
                payload=interaction.get("payload") or {},
                continuation_policy=interaction.get("continuation_policy") or "wake_assignee",
                idempotency_key=interaction.get("idempotency_key"),
                source_run_id=str(run.get("id")),
                created_by_agent_id=agent_id,
                title=interaction.get("title"),
                summary=interaction.get("summary"),
            )
            _created_this_run += 1
            log_activity(
                self.db_path,
                action="interaction.created",
                target_type="interaction",
                target_id=created["id"],
                actor_agent_id=agent_id,
                run_id=str(run.get("id")),
                payload={"issue_id": issue_id, "kind": interaction["kind"], "title": interaction.get("title")},
            )
        issue_status = actions.get("issue_status")
        if isinstance(issue_status, str) and issue_status:
            update_issue(self.db_path, issue_id=issue_id, status=issue_status)
            log_activity(
                self.db_path,
                action="issue.updated",
                target_type="issue",
                target_id=issue_id,
                actor_agent_id=agent_id,
                run_id=str(run.get("id")),
                payload={"status": issue_status, "source": "run_executor"},
            )
        if issue_status in ("done", "cancelled") and issue_id:
            try:
                resolve_blocker_wakeups(self.db_path, resolved_issue_id=issue_id, source_run_id=str(run.get("id") or ""))
            except Exception:
                logger.warning("resolve_blocker_wakeups failed for issue %s", issue_id, exc_info=True)
        if actions.get("notify_supervisor"):
            self._enqueue_supervisor_report(
                issue_id=issue_id,
                reporting_agent_id=agent_id,
                source_run_id=str(run.get("id")),
                liveness_state=actions.get("_liveness_state"),
                liveness_reason=actions.get("_liveness_reason"),
            )

        # add_comments: extra comments emitted by the LLM adapter (beyond result.output)
        for body in actions.get("add_comments") or []:
            if not isinstance(body, str) or not body.strip():
                continue
            try:
                comment = create_comment(
                    self.db_path,
                    issue_id=issue_id,
                    author_agent_id=agent_id,
                    source_run_id=str(run.get("id")),
                    body=_safe_truncate_output(body.strip()),
                    metadata={"source": "run_executor_action"},
                )
                log_activity(
                    self.db_path,
                    action="comment.created",
                    target_type="comment",
                    target_id=comment["id"],
                    actor_agent_id=agent_id,
                    run_id=str(run.get("id")),
                    payload={"issue_id": issue_id, "source": "action:add_comment"},
                )
                self._maybe_materialize_plan_comment(
                    issue_id=issue_id,
                    agent_id=agent_id,
                    run_id=str(run.get("id") or ""),
                    body=body,
                )
            except Exception:
                logger.warning("add_comment action failed for issue %s", issue_id, exc_info=True)

        # update_plan: LLM-written plan document
        plan_action = actions.get("update_plan")
        if isinstance(plan_action, dict) and plan_action.get("body"):
            try:
                existing = get_document(self.db_path, issue_id=issue_id, key="plan")
                put_document(
                    self.db_path,
                    issue_id=issue_id,
                    key="plan",
                    title=str(plan_action.get("title") or "Plan"),
                    body=str(plan_action["body"]),
                    format="markdown",
                    run_id=str(run.get("id") or "") or None,
                    base_revision_id=str(existing.get("current_revision_id") or "") or None
                    if existing else None,
                )
            except DocumentConflict:
                pass
            except Exception:
                logger.warning("update_plan action failed for issue %s", issue_id, exc_info=True)

        # create_issues: sub-issues delegated by the LLM
        created_child_roles: list[str] = []
        for spec in actions.get("create_issues") or []:
            if not isinstance(spec, dict):
                continue
            created = self._create_delegated_issue(
                issue_id=issue_id,
                agent_id=agent_id,
                run=run,
                spec=spec,
                metadata_source="llm_adapter",
                activity_source="action:create_issue",
            )
            if created is not None:
                created_child_roles.append(str(created.get("role") or "").strip().lower())

        self._maybe_add_full_team_review_guardrail(
            issue_id=issue_id,
            agent_id=agent_id,
            run=run,
            created_child_roles=created_child_roles,
        )
        try:
            sync_default_child_dependencies(self.db_path, parent_issue_id=issue_id)
        except Exception:
            logger.warning("sync_default_child_dependencies failed for issue %s", issue_id, exc_info=True)

    def _create_delegated_issue(
        self,
        *,
        issue_id: str,
        agent_id: str,
        run: dict[str, Any],
        spec: dict[str, Any],
        metadata_source: str,
        activity_source: str,
    ) -> dict[str, Any] | None:
        title_val = str(spec.get("title") or "").strip()
        if not title_val:
            return None
        try:
            role_for_issue = str(spec.get("role") or "engineer")
            # Idempotency: don't create if a non-terminal child with same role already exists
            with contextlib.closing(_connect(self.db_path)) as _conn:
                _existing = _conn.execute(
                    """
                    SELECT id FROM issues
                    WHERE parent_id = ?
                      AND lower(role) = lower(?)
                      AND status NOT IN ('done', 'cancelled')
                    LIMIT 1
                    """,
                    (issue_id, role_for_issue),
                ).fetchone()
            if _existing is not None:
                return get_issue(self.db_path, issue_id=str(_existing["id"]))
            assignee_agent_id = self._ensure_role_agent(
                role=role_for_issue,
                supervisor_agent_id=agent_id,
                source_run_id=str(run.get("id") or ""),
            )
            parent_issue = get_issue(self.db_path, issue_id=issue_id)
            new_issue = create_issue(
                self.db_path,
                title=title_val,
                description=str(spec.get("description") or "") or None,
                status="todo",
                parent_id=issue_id,
                goal_id=str((parent_issue or {}).get("goal_id") or "") or None,
                role=role_for_issue,
                complexity=str(spec.get("complexity") or "medium") or None,
                assignee_agent_id=assignee_agent_id,
                metadata={"source": metadata_source, "parent_issue_id": issue_id},
            )
            log_activity(
                self.db_path,
                action="issue.created",
                target_type="issue",
                target_id=new_issue["id"],
                actor_agent_id=agent_id,
                run_id=str(run.get("id")),
                payload={
                    "parent_issue_id": issue_id,
                    "role": role_for_issue,
                    "source": activity_source,
                    "assignee_agent_id": assignee_agent_id,
                },
            )
            if assignee_agent_id:
                enqueue_wakeup(
                    self.db_path,
                    agent_id=assignee_agent_id,
                    source="assignment",
                    reason="new_issue",
                    payload={
                        "issue_id": new_issue["id"],
                        "parent_issue_id": issue_id,
                        "wake_reason": "new_issue",
                    },
                    idempotency_key=f"assignment:{new_issue['id']}:{assignee_agent_id}",
                )
            return new_issue
        except Exception:
            logger.warning("_create_delegated_issue failed for issue %s title=%r", issue_id, title_val, exc_info=True)
            return None

    def _maybe_add_full_team_review_guardrail(
        self,
        *,
        issue_id: str,
        agent_id: str,
        run: dict[str, Any],
        created_child_roles: list[str],
    ) -> None:
        if "reviewer" in created_child_roles or "code_reviewer" in created_child_roles:
            return
        if not {"engineer", "qa"} & set(created_child_roles):
            return
        agent = self._agent_info(agent_id) or {}
        if str(agent.get("role") or "").strip().lower() not in {"lead", "team_lead"}:
            return
        parent_issue = get_issue(self.db_path, issue_id=issue_id) or {}
        metadata = _decode_json(parent_issue.get("metadata_json") or "{}")
        if normalize_run_profile(metadata.get("profile") or "") != FULL_TEAM:
            return
        with contextlib.closing(_connect(self.db_path)) as conn:
            existing = conn.execute(
                """
                SELECT id
                FROM issues
                WHERE parent_id = ?
                  AND lower(role) IN ('reviewer', 'code_reviewer')
                LIMIT 1
                """,
                (issue_id,),
            ).fetchone()
        if existing is not None:
            return
        self._create_delegated_issue(
            issue_id=issue_id,
            agent_id=agent_id,
            run=run,
            spec={
                "title": "Revisar entrega antes de cierre",
                "description": (
                    "Revisión ligera añadida por AI Teams porque el perfil full_team requiere "
                    "mirada independiente antes de cerrar trabajo de implementación o QA."
                ),
                "role": "reviewer",
                "complexity": "medium",
            },
            metadata_source="full_team_review_guardrail",
            activity_source="guardrail:full_team_reviewer",
        )

    # Tier 3 cheap specialists — always prefer budget/local adapters.
    _TIER3_ROLES = frozenset({"file_scout", "web_scout", "context_curator"})

    def _ensure_role_agent(self, *, role: str, supervisor_agent_id: str, source_run_id: str) -> str | None:
        role_key = role.strip().lower().replace(" ", "_").replace("-", "_")
        if not role_key:
            return None
        if role_key in {"lead", "team_lead"}:
            return supervisor_agent_id or "role:lead"
        agent_id = f"role:{role_key}"
        existing = self._agent_info(agent_id)
        if existing is not None:
            if str(existing.get("adapter_type") or "").strip() in {"", "manual", "role_builtin", "lead_builtin"}:
                try:
                    reconcile_project_agent_policy(self.db_path)
                except Exception:
                    logger.warning("reconcile_project_agent_policy failed in _ensure_role_agent role=%r", role_key, exc_info=True)
            return agent_id
        try:
            # Tier 3 scouts use "cheap" seniority so choose_adapter_for_role
            # selects the most economical available adapter (local > flash > groq)
            # instead of defaulting to an expensive senior model.
            effective_seniority = "cheap" if role_key in self._TIER3_ROLES else "standard"
            selection = choose_adapter_for_role(role_key, effective_seniority, project_profiles(Path(self.db_path).parent))
            row = create_agent(
                self.db_path,
                agent_id=agent_id,
                role=role_key,
                name=role_key.replace("_", " ").title(),
                seniority="standard",
                adapter_type=str((selection or {}).get("adapter_type") or "role_builtin"),
                adapter_config=(selection or {}).get("adapter_config") or {},
                capabilities=default_capabilities_for_role(role_key),
                supervisor_agent_id=supervisor_agent_id or None,
                metadata={"source": "llm_create_issue", "source_run_id": source_run_id},
            )
            log_activity(
                self.db_path,
                action="agent.created",
                target_type="agent",
                target_id=row["id"],
                actor_agent_id=supervisor_agent_id or None,
                run_id=source_run_id or None,
                payload={"role": role_key, "source": "llm_create_issue"},
            )
            return str(row["id"])
        except Exception:
            return None

    def _record_run_adapter_metadata(
        self,
        *,
        run_id: str,
        adapter_type: str | None,
        provider: str | None,
        model: str | None,
        channel: str | None,
    ) -> None:
        normalized_channel = channel if channel in {"subscription", "api", "local"} else None
        with contextlib.closing(_connect(self.db_path)) as conn:
            conn.execute(
                """
                UPDATE runs
                SET adapter_type = ?,
                    provider = ?,
                    model = ?,
                    channel = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (adapter_type, provider, model, normalized_channel, run_id),
            )

    def _maybe_materialize_plan_comment(
        self,
        *,
        issue_id: str,
        agent_id: str,
        run_id: str,
        body: str,
    ) -> None:
        if not issue_id or not _looks_like_plan(body):
            return
        agent = self._agent_info(agent_id) or {}
        role = str(agent.get("role") or "").strip().lower()
        if role not in {"lead", "team_lead"}:
            return
        if get_document(self.db_path, issue_id=issue_id, key="plan") is not None:
            return
        try:
            put_document(
                self.db_path,
                issue_id=issue_id,
                key="plan",
                title="Plan recuperado del Lead",
                body=body.strip(),
                format="markdown",
                run_id=run_id or None,
                metadata={"source": "materialized_from_lead_comment"},
            )
        except DocumentConflict:
            pass
        except Exception:
            logger.warning("_maybe_materialize_plan_comment failed for issue %s", issue_id, exc_info=True)

    def _proposal_state(self, issue_id: str) -> str | None:
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT status
                FROM issue_thread_interactions
                WHERE issue_id = ?
                  AND kind = 'suggest_tasks'
                  AND idempotency_key = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (issue_id, f"lead:intake-proposal:{issue_id}"),
            ).fetchone()
        return str(row["status"]) if row else None

    def _cycle_review_state(self, issue_id: str) -> str | None:
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT status
                FROM issue_thread_interactions
                WHERE issue_id = ?
                  AND kind = 'request_confirmation'
                  AND idempotency_key = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (issue_id, f"lead:cycle-review:{issue_id}"),
            ).fetchone()
        return str(row["status"]) if row else None

    def _enqueue_supervisor_report(
        self,
        *,
        issue_id: str,
        reporting_agent_id: str,
        source_run_id: str,
        liveness_state: str | None = None,
        liveness_reason: str | None = None,
    ) -> None:
        issue = get_issue(self.db_path, issue_id=issue_id)
        if issue is None:
            return
        parent_issue_id = str(issue.get("parent_id") or "").strip()
        if not parent_issue_id:
            return
        parent_issue = get_issue(self.db_path, issue_id=parent_issue_id)
        agent_info = self._agent_info(reporting_agent_id) or {}
        supervisor_agent_id = str(
            agent_info.get("supervisor_agent_id")
            or (parent_issue or {}).get("assignee_agent_id")
            or ""
        ).strip()
        if not supervisor_agent_id:
            return

        # Read the child issue's current status from DB (already updated by liveness override)
        child_status = str(issue.get("status") or "unknown")
        child_adapter = str(issue.get("checkout_run_id") or "")  # not adapter, but we get it below
        # Re-fetch to get updated status after liveness override was applied
        fresh_issue = get_issue(self.db_path, issue_id=issue_id)
        if fresh_issue:
            child_status = str(fresh_issue.get("status") or child_status)

        payload: dict[str, Any] = {
            "issue_id": parent_issue_id,
            "child_issue_id": issue_id,
            "child_issue_status": child_status,
            "reporting_agent_id": reporting_agent_id,
            "source_run_id": source_run_id,
            "wake_reason": "child_report",
        }
        if liveness_state:
            payload["child_liveness_state"] = liveness_state
        if liveness_reason:
            payload["child_liveness_reason"] = liveness_reason

        # Idempotency: coalesce all child reports for the same parent+supervisor, but
        # keep blocked notifications separate from done/advanced so the supervisor
        # gets a distinct wakeup when an issue transitions between terminal states.
        terminal_bucket = child_status if child_status in ("blocked", "done", "cancelled") else "progress"
        enqueue_wakeup(
            self.db_path,
            agent_id=supervisor_agent_id,
            source="delegation",
            reason="child_report",
            trigger_detail=f"{reporting_agent_id}:{issue_id}:{child_status}",
            payload=payload,
            idempotency_key=f"child_report:{parent_issue_id}:{supervisor_agent_id}:{terminal_bucket}",
        )

    def _format_supervisor_summary(self, issue_id: str) -> str:
        """Produce a rich supervisor summary that surfaces agent report signals.

        Each child line shows: icon, title, assignee, status, and key agent
        report fields (result, evidence, blocker) so the Lead and the user can
        immediately see whether child output is adequate without reading comments.

        Icons:
          ✓  done + report result ok (or no report)
          ⚠  done but report result is partial/blocked
          ✗  blocked status
          ·  any other in-progress state
        """
        rows = self._child_issue_rows(issue_id)
        if not rows:
            return "Resumen del Lead\n\nNo hay issues hijas que consolidar todavia."
        lines = ["Resumen del Lead", "", "Estado de delegaciones:"]
        warnings: list[str] = []
        reviewer_roles = {"reviewer", "code_reviewer"}
        for row in rows:
            status = str(row.get("status") or "unknown")
            role = str(row.get("role") or "").strip().lower()
            report = row.get("last_agent_report") or {}
            report_result = str(report.get("result") or "").strip().lower() if report else ""
            report_blocker = str(report.get("blocker") or "").strip() if report else ""
            report_evidence = str(report.get("evidence") or "").strip() if report else ""
            # Determine icon
            if status == "blocked" or report_result == "blocked":
                icon = "✗"
            elif status == "done" and report_result in {"partial", "changes_requested"}:
                icon = "⚠"
            elif status == "done":
                icon = "✓"
            else:
                icon = "·"
            # Build detail suffix
            detail_parts: list[str] = [f"({status})"]
            if report_result:
                detail_parts.append(f"resultado: {report_result}")
            if report_evidence:
                detail_parts.append(f"evidencia: {report_evidence[:60]}")
            if report_blocker and report_blocker.lower() != "none":
                detail_parts.append(f"bloqueado: {report_blocker[:80]}")
            detail = " | ".join(detail_parts)
            lines.append(
                f"- [{icon}] {row['title']} → {row['assignee_agent_id'] or 'sin owner'} {detail}"
            )
            # Collect named warnings
            if role in reviewer_roles and status == "done" and report_result in {"blocked", "partial", "changes_requested"}:
                warnings.append(
                    f"⚠ Reviewer completó con resultado '{report_result}' — "
                    + ("hay cambios pendientes que el Engineer debe corregir." if report_result == "changes_requested"
                       else "sin evidencia suficiente. Revisa su comentario antes de cerrar el ciclo.")
                )
            if role in self._TIER3_ROLES and (status == "blocked" or report_result == "blocked"):
                blocker_hint = f": {report_blocker}" if report_blocker and report_blocker.lower() != "none" else ""
                warnings.append(
                    f"⚠ Scout bloqueado ({role}){blocker_hint}. "
                    "Acepta la solicitud de lectura directa o cambia el adapter del scout."
                )
        # Collect structural warnings before the footer
        all_done = all(str(row["status"]) == "done" for row in rows)
        has_reviewer = any(str(row.get("role") or "").strip().lower() in reviewer_roles for row in rows)
        if all_done and not has_reviewer:
            warnings.append(
                "⚠ Ningún reviewer encontrado entre las issues hijas — el ciclo no puede cerrarse automáticamente. "
                "Crea una issue con role='reviewer' para completar el gate de calidad."
            )
        # Footer
        if warnings:
            lines.extend(["", "Alertas:"])
            for w in warnings:
                lines.append(f"  {w}")
        if all_done and has_reviewer and not warnings:
            lines.extend(
                [
                    "",
                    "La primera ronda del equipo esta completa. No cierro automaticamente: dejo una confirmacion ligera para que puedas pedir ajustes o continuar.",
                ]
            )
        elif all_done and (not has_reviewer or warnings):
            lines.extend(
                [
                    "",
                    "Todas las issues hijas estan en 'done' pero hay alertas pendientes (ver arriba). Resuelve las alertas antes de confirmar el cierre.",
                ]
            )
        else:
            lines.extend(["", "Aun hay delegaciones abiertas; espero nuevos reportes antes de pedir cierre."])
        return "\n".join(lines)

    def _all_children_done(self, issue_id: str) -> bool:
        """Return True only when every child is done AND quality gates pass.

        Quality gates:

        Reviewer gate (required):
        - At least one child with role ``reviewer`` or ``code_reviewer`` must be ``done``.
        - If the reviewer wrote an ``---AGENT-REPORT---`` block its ``result`` field must
          NOT be ``blocked``, ``partial``, or ``changes_requested``.
          A missing report (e.g. role_builtin) is treated as acceptable so legacy runs
          still close normally.

        QA gate (optional — only enforced when a QA child exists):
        - If any child with role ``qa`` or ``quality_assurance`` is ``done``, its
          ``result`` must NOT be ``blocked`` or ``partial``.  A missing report is accepted.
          (QA is optional but if it ran and found failures, cycle-close must wait.)
        """
        rows = self._child_issue_rows(issue_id)
        if not rows:
            return False
        if not all(str(row["status"]) == "done" for row in rows):
            return False
        reviewer_roles = {"reviewer", "code_reviewer"}
        qa_roles = {"qa", "quality_assurance"}
        reviewer_rows = [
            r for r in rows
            if str(r.get("role") or "").strip().lower() in reviewer_roles
        ]
        if not reviewer_rows:
            return False  # reviewer required before cycle-close
        # Reviewer quality gate: blocked/partial/changes_requested block cycle-close.
        for rev_row in reviewer_rows:
            report = rev_row.get("last_agent_report") or {}
            if report:
                result = str(report.get("result") or "").strip().lower()
                if result in {"blocked", "partial", "changes_requested"}:
                    return False
        # QA quality gate (optional): if QA ran and reported bad results, block cycle-close.
        # QA is not required for cycle-close but if present and failing, we must not close.
        for qa_row in rows:
            if str(qa_row.get("role") or "").strip().lower() not in qa_roles:
                continue
            report = qa_row.get("last_agent_report") or {}
            if report:
                result = str(report.get("result") or "").strip().lower()
                if result in {"blocked", "partial"}:
                    return False
        return True

    def _has_progressing_children(self, issue_id: str) -> bool:
        """Return True if any child issue is actively progressing (non-terminal AND non-blocked).

        Intentionally excludes 'blocked' children so that a manual lead wake-up on an
        all-blocked subtree triggers escalation instead of silently skipping.
        """
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM issues
                WHERE parent_id = ?
                  AND status NOT IN ('done', 'cancelled', 'blocked')
                """,
                (issue_id,),
            ).fetchone()
        return bool(row and row["cnt"] > 0)

    def _blocked_child_rows(self, issue_id: str) -> list[dict[str, Any]]:
        """Return child issues in 'blocked' status, enriched with latest liveness_reason."""
        with contextlib.closing(_connect(self.db_path)) as conn:
            rows = conn.execute(
                """
                SELECT i.id, i.title, i.role, i.assignee_agent_id,
                       r.liveness_reason
                FROM issues i
                LEFT JOIN runs r ON r.id = (
                    SELECT id FROM runs
                    WHERE issue_id = i.id
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 1
                )
                WHERE i.parent_id = ?
                  AND i.status = 'blocked'
                ORDER BY i.priority DESC, i.created_at ASC
                """,
                (issue_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _tier3_blocked_scouts(self, issue_id: str) -> list[dict[str, Any]]:
        """Return Tier 3 child rows that are blocked (by status OR by agent report result)."""
        rows = self._child_issue_rows(issue_id)
        blocked: list[dict[str, Any]] = []
        for row in rows:
            role = str(row.get("role") or "").strip().lower()
            if role not in self._TIER3_ROLES:
                continue
            status = str(row.get("status") or "").strip().lower()
            report = row.get("last_agent_report") or {}
            report_result = str(report.get("result") or "").strip().lower()
            if status == "blocked" or report_result == "blocked":
                blocked.append(row)
        return blocked

    def _format_cycle_close_summary(self, issue_id: str) -> str:
        """Build a cycle-close confirmation summary that shows objective vs evidence.

        The goal is to give the user enough information to decide whether the
        objective was actually met — not just whether all issues are done.
        Surfaces: original objective, tech delivered, reviewer evidence, and gaps.
        """
        # ── Fetch original objective ──────────────────────────────────────────
        issue = get_issue(self.db_path, issue_id=issue_id)
        objective = str((issue or {}).get("title") or "").strip()
        description = str((issue or {}).get("description") or "").strip()
        if description and len(description) < 300:
            objective_text = f"{objective}\n{description}"
        else:
            objective_text = objective or "(sin título)"

        rows = self._child_issue_rows(issue_id)
        reviewer_roles = {"reviewer", "code_reviewer"}
        engineer_roles = {"engineer"}

        # ── Collect evidence from each role ───────────────────────────────────
        reviewer_evidence: list[str] = []
        reviewer_tech: list[str] = []
        reviewer_verdict: list[str] = []
        engineer_evidence: list[str] = []
        engineer_tech: list[str] = []
        tech_mismatches: list[str] = []

        for row in rows:
            role = str(row.get("role") or "").strip().lower()
            report = row.get("last_agent_report") or {}
            evidence = str(report.get("evidence") or "").strip()
            tech_match = str(report.get("tech_match") or "").strip().lower()
            result = str(report.get("result") or "").strip().lower()
            title = str(row.get("title") or "")

            if role in reviewer_roles:
                if evidence and evidence.lower() != "none":
                    reviewer_evidence.append(evidence)
                if tech_match:
                    reviewer_tech.append(tech_match)
                if result:
                    reviewer_verdict.append(result)
            elif role in engineer_roles:
                if evidence and evidence.lower() != "none":
                    engineer_evidence.append(evidence)
                if tech_match:
                    engineer_tech.append(tech_match)

            # Flag explicit tech mismatches
            if tech_match == "no":
                tech_mismatches.append(f"  ⚠ {title} → tech_match: no")

        # ── Build the summary ─────────────────────────────────────────────────
        parts: list[str] = []
        parts.append(f"**Objetivo original:** {objective_text}")
        parts.append("")

        if reviewer_evidence:
            parts.append(f"**Evidencia del Reviewer:** {'; '.join(reviewer_evidence)}")
        else:
            parts.append("**Evidencia del Reviewer:** (ninguna registrada)")

        if reviewer_verdict:
            verdict_str = ", ".join(reviewer_verdict)
            parts.append(f"**Veredicto del Reviewer:** {verdict_str}")

        if engineer_evidence:
            parts.append(f"**Evidencia del Engineer:** {'; '.join(engineer_evidence)}")

        if tech_mismatches:
            parts.append("")
            parts.append("**⚠ Alertas de tecnología:**")
            parts.extend(tech_mismatches)

        parts.append("")
        parts.append(
            "Acepta si el objetivo está cumplido. Rechaza (o escribe en el thread) "
            "si falta algo — el equipo reabrirá el ciclo con instrucciones concretas."
        )

        return "\n".join(parts)

    def _lead_file_read_interaction_state(self, issue_id: str) -> str | None:
        """Return the status of the lead_wants_file_read interaction, or None if absent."""
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT status FROM issue_thread_interactions
                WHERE issue_id = ?
                  AND kind = 'request_confirmation'
                  AND idempotency_key = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (issue_id, f"lead:file-read-request:{issue_id}"),
            ).fetchone()
        return str(row["status"]) if row else None

    def _cancel_stale_interaction(self, issue_id: str, *, reason: str) -> None:
        """Cancel any pending interaction whose payload.reason matches *reason*.

        Used to clean up superseded interactions before creating a new one that
        would otherwise be blocked by the pending-interaction gate.  Only touches
        non-terminal rows so resolved/accepted interactions are never modified.
        """
        _terminal = {"accepted", "rejected", "answered", "cancelled", "expired"}
        try:
            existing = list_interactions(self.db_path, issue_id=issue_id)
            for row in existing:
                if str(row.get("status") or "") in _terminal:
                    continue
                _pl = _decode_json(row.get("payload_json") or "{}")
                if str(_pl.get("reason") or "") != reason:
                    continue
                with contextlib.closing(_connect(self.db_path)) as conn:
                    conn.execute(
                        """
                        UPDATE issue_thread_interactions
                        SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                          AND status NOT IN ('accepted', 'rejected', 'answered', 'cancelled', 'expired')
                        """,
                        (str(row["id"]),),
                    )
                logger.info(
                    "Cancelled stale interaction %r (reason=%r) for issue %s — superseded by new project state",
                    row["id"], reason, issue_id,
                )
        except Exception:
            logger.warning("_cancel_stale_interaction failed for issue %s reason=%r", issue_id, reason, exc_info=True)

    # Hard cap on automatic fix cycles.  After this many reviewer→engineer rounds
    # the system escalates to the user instead of spawning another fix issue.
    # Prevents runaway loops when the engineer repeatedly delivers wrong output.
    _MAX_FIX_CYCLES: int = 3

    # Comment threshold for auto-spawning a context_curator child.  When the
    # parent issue accumulates this many comments and has no plan document and no
    # active curator child, the Lead silently creates a context_curator to
    # synthesise the thread before the next round of delegation.
    _CONTEXT_CURATOR_COMMENT_THRESHOLD: int = 8

    def _maybe_spawn_context_curator(
        self, issue_id: str, agent_id: str, run: dict[str, Any]
    ) -> None:
        """Silently spawn a context_curator child when the issue thread grows long.

        Triggered in the ``child_report`` branch when ALL three conditions hold:
        1. The parent issue has ≥ ``_CONTEXT_CURATOR_COMMENT_THRESHOLD`` comments.
        2. No plan document exists for the issue (curator adds the most value before
           a plan is written; once a plan exists the thread is already summarised).
        3. No non-cancelled ``context_curator`` child already exists.
           - Active (todo/in_progress/blocked): wait for it to finish.
           - Done: the thread has already been curated; do NOT re-spawn (prevents
             an infinite loop where each curator finish immediately triggers
             another spawn on the next child_report wake).
           - Cancelled: the curator was abandoned; a fresh one is allowed.

        The method never raises — any failure is logged and silently swallowed so
        the calling ``child_report`` path continues unaffected.
        """
        try:
            with contextlib.closing(_connect(self.db_path)) as conn:
                comment_count = conn.execute(
                    "SELECT COUNT(*) FROM issue_comments WHERE issue_id = ?",
                    (issue_id,),
                ).fetchone()[0]
            if comment_count < self._CONTEXT_CURATOR_COMMENT_THRESHOLD:
                return

            if get_document(self.db_path, issue_id=issue_id, key="plan") is not None:
                return

            with contextlib.closing(_connect(self.db_path)) as conn:
                existing_curator = conn.execute(
                    """
                    SELECT id FROM issues
                    WHERE parent_id = ? AND lower(role) = 'context_curator'
                      AND status != 'cancelled'
                    LIMIT 1
                    """,
                    (issue_id,),
                ).fetchone()
            if existing_curator is not None:
                return

            logger.info(
                "Spawning context_curator for issue %s (comment_count=%d, threshold=%d)",
                issue_id, comment_count, self._CONTEXT_CURATOR_COMMENT_THRESHOLD,
            )
            self._create_delegated_issue(
                issue_id=issue_id,
                agent_id=agent_id,
                run=run,
                spec={
                    "title": "Context curator — sintetizar hilo del proyecto",
                    "description": (
                        f"Target issue: {issue_id}\n\n"
                        "El hilo de la issue padre ha acumulado suficientes comentarios para "
                        "dificultar la navegación. Sigue tu protocolo estándar de context_curator:\n"
                        "1. Lee el hilo completo del Target issue (via API o wake payload).\n"
                        "2. Escribe un documento de plan comprimido en el Target issue "
                        "(PUT /api/issues/{target_id}/documents/plan). Si la API no está "
                        "disponible, usa add_comment en el Target issue como fallback.\n"
                        "3. Usa set_status: done cuando hayas terminado.\n\n"
                        "No crees sub-issues ni interacciones."
                    ),
                    "role": "context_curator",
                    "complexity": "low",
                },
                metadata_source="context_curator_auto_trigger",
                activity_source="context_curator_auto_trigger",
            )
        except Exception:
            logger.warning(
                "_maybe_spawn_context_curator failed silently for issue %s", issue_id,
                exc_info=True,
            )

    def _handle_reviewer_changes_requested(
        self, issue_id: str, agent_id: str, run: dict[str, Any]
    ) -> "ExecutionResult | None":
        """Detect reviewer changes_requested and auto-create a fix engineer issue.

        When a reviewer finishes with ``result=changes_requested`` in its
        ``---AGENT-REPORT---`` block and there is no currently open (non-terminal)
        engineer child, this method:

        1. Checks the fix cycle count; if the cap (_MAX_FIX_CYCLES) is reached,
           escalates to the user with a request_confirmation instead of creating
           another fix issue — preventing infinite retry loops.
        2. Cancels any stale ``initial_cycle_ready`` interaction so a premature
           cycle-close prompt cannot coexist with an active fix cycle.
        3. Resets the reviewer(s) back to ``todo`` so they can re-run after fix.
        4. Creates a numbered engineer child issue (Fix #N) whose description
           surfaces the reviewer's evidence and blocker fields.
        5. Calls ``sync_default_child_dependencies`` which wires a
           reviewer → fix_engineer dependency — the reviewer is automatically
           woken by ``resolve_blocker_wakeups`` when the engineer finishes.

        Returns an ``ExecutionResult`` (caller should return it immediately) if a
        fix cycle was started or escalated, or ``None`` if no action was needed.
        """
        rows = self._child_issue_rows(issue_id)
        reviewer_roles = {"reviewer", "code_reviewer"}
        engineer_roles = {"engineer", "software_engineer"}

        # Identify reviewers that are done with changes_requested
        changes_requested_reviewers = [
            r for r in rows
            if str(r.get("role") or "").strip().lower() in reviewer_roles
            and str(r.get("status") or "").strip().lower() == "done"
            and str((r.get("last_agent_report") or {}).get("result") or "").strip().lower()
            == "changes_requested"
        ]
        if not changes_requested_reviewers:
            return None

        # If there is already an open engineer fix issue, wait for it to complete
        open_engineers = [
            r for r in rows
            if str(r.get("role") or "").strip().lower() in engineer_roles
            and str(r.get("status") or "").strip().lower() not in {"done", "cancelled"}
        ]
        if open_engineers:
            return None

        # Count non-cancelled engineer children to determine which fix cycle this is.
        # Cycle #1 = original engineer done + reviewer says changes_requested for the first time.
        # Cycle #N = N-1 fix engineers done + reviewer still unhappy.
        non_cancelled_engineers = [
            r for r in rows
            if str(r.get("role") or "").strip().lower() in engineer_roles
            and str(r.get("status") or "").strip().lower() != "cancelled"
        ]
        fix_cycle_number = len(non_cancelled_engineers)  # 1-based: 1 = first fix, 2 = second…

        # ── Fix cycle hard cap ────────────────────────────────────────────────
        # After _MAX_FIX_CYCLES rounds the system can no longer self-recover.
        # Escalate to the user rather than create yet another doomed fix issue.
        if fix_cycle_number > self._MAX_FIX_CYCLES:
            rev_row = changes_requested_reviewers[0]
            report = rev_row.get("last_agent_report") or {}
            evidence = str(report.get("evidence") or "").strip()
            blocker = str(report.get("blocker") or "").strip()
            logger.warning(
                "Fix cycle limit reached (%d/%d) for parent issue %s — escalating to user",
                fix_cycle_number, self._MAX_FIX_CYCLES, issue_id,
            )
            escalation_lines = [
                f"Lead — Límite de ciclos de corrección alcanzado ({fix_cycle_number - 1}/{self._MAX_FIX_CYCLES})",
                "",
                f"El Reviewer ha solicitado `changes_requested` {fix_cycle_number - 1} veces consecutivas "
                "y el Engineer no ha logrado entregar una implementación aprobada.",
                "Se necesita intervención manual para desbloquear el proyecto.",
                "",
            ]
            if evidence:
                escalation_lines.append(f"**Evidencia más reciente del Reviewer:** {evidence}")
            if blocker:
                escalation_lines.append(f"**Problema persistente:** {blocker}")
            escalation_lines += [
                "",
                "Opciones para el usuario:",
                "- Acepta para que el Lead intente un nuevo Engineer con instrucciones más detalladas.",
                "- Rechaza para mantener el estado actual y diagnosticar manualmente.",
            ]
            return ExecutionResult(
                status="completed",
                output="\n".join(escalation_lines),
                actions={
                    "interactions": [
                        {
                            "kind": "request_confirmation",
                            "payload": {
                                "version": 1,
                                "reason": "reviewer_fix_cycle_limit",
                                "parent_issue_id": issue_id,
                                "fix_cycle_count": fix_cycle_number - 1,
                                "last_blocker": blocker,
                                "last_evidence": evidence,
                            },
                            "title": f"Ciclos de corrección agotados — intervención requerida",
                            "summary": (
                                f"El Reviewer ha rechazado {fix_cycle_number - 1} implementaciones. "
                                "Acepta para intentar un ciclo final con instrucciones ampliadas; "
                                "rechaza para diagnosticar manualmente."
                            ),
                            "idempotency_key": f"lead:fix-cycle-limit:{issue_id}",
                        }
                    ]
                },
            )

        # Cancel any stale initial_cycle_ready interaction — it would be misleading
        # (telling the user "all done") while an active fix cycle is about to start.
        self._cancel_stale_interaction(issue_id, reason="initial_cycle_ready")

        # Gather reviewer findings for the fix issue description
        rev_row = changes_requested_reviewers[0]
        report = rev_row.get("last_agent_report") or {}
        reviewer_title = str(rev_row.get("title") or "Reviewer").strip()
        evidence = str(report.get("evidence") or "").strip()
        blocker = str(report.get("blocker") or "").strip()

        if fix_cycle_number > 1:
            logger.warning(
                "Fix cycle #%d starting for parent issue %s — reviewer still unhappy after %d prior attempt(s)",
                fix_cycle_number, issue_id, fix_cycle_number - 1,
            )

        desc_parts = [
            f"**Corrección #{fix_cycle_number} solicitada por el Reviewer** ({reviewer_title})",
            "",
            "El Reviewer completó la revisión y encontró problemas que deben corregirse "
            "antes de que el ciclo pueda cerrarse.",
        ]
        if fix_cycle_number > 1:
            desc_parts += [
                "",
                f"⚠ Este es el ciclo de corrección #{fix_cycle_number}. "
                f"Los {fix_cycle_number - 1} intento(s) anteriores no resolvieron los problemas. "
                "Lee el último comentario del Reviewer con atención antes de implementar.",
            ]
        if evidence:
            desc_parts += ["", f"**Evidencia del Reviewer:** {evidence}"]
        if blocker:
            desc_parts += ["", f"**Problema identificado:** {blocker}"]
        desc_parts += [
            "",
            "Implementa las correcciones necesarias y reporta al Lead via "
            "notify_supervisor cuando termines. El Reviewer se volverá a ejecutar "
            "automáticamente cuando esta issue esté completada.",
        ]
        fix_description = "\n".join(desc_parts)

        # Reset all changes_requested reviewers back to todo
        for rev in changes_requested_reviewers:
            rev_id = str(rev.get("id") or "").strip()
            if not rev_id:
                continue
            try:
                update_issue(self.db_path, issue_id=rev_id, status="todo")
                log_activity(
                    self.db_path,
                    action="issue.updated",
                    target_type="issue",
                    target_id=rev_id,
                    actor_agent_id=agent_id,
                    run_id=str(run.get("id")),
                    payload={
                        "status": "todo",
                        "source": "reviewer_changes_requested_cycle",
                        "fix_cycle_number": fix_cycle_number,
                        "reason": "reviewer reported changes_requested; reset to await fix engineer",
                    },
                )
                logger.info(
                    "Reviewer %s reset to todo for fix cycle #%d (parent=%s)",
                    rev_id, fix_cycle_number, issue_id,
                )
            except Exception:
                logger.warning(
                    "Failed to reset reviewer %s to todo for changes_requested cycle #%d",
                    rev_id, fix_cycle_number,
                    exc_info=True,
                )

        # Create the numbered fix engineer issue (idempotent via _create_delegated_issue)
        fix_title = (
            f"Fix #{fix_cycle_number}: correcciones solicitadas por Reviewer"
        )
        fix_issue = self._create_delegated_issue(
            issue_id=issue_id,
            agent_id=agent_id,
            run=run,
            spec={
                "title": fix_title,
                "description": fix_description,
                "role": "engineer",
                "complexity": "medium",
            },
            metadata_source="reviewer_changes_requested_fix",
            activity_source="reviewer_changes_requested_cycle",
        )

        # Wire reviewer → fix_engineer dependency so reviewer auto-wakes when done
        try:
            sync_default_child_dependencies(self.db_path, parent_issue_id=issue_id)
        except Exception:
            logger.warning(
                "sync_default_child_dependencies failed after changes_requested cycle #%d for %s",
                fix_cycle_number, issue_id,
                exc_info=True,
            )

        fix_id = str((fix_issue or {}).get("id") or "desconocido")
        cycles_remaining = self._MAX_FIX_CYCLES - fix_cycle_number
        output_lines = [
            f"Lead — Ciclo de corrección #{fix_cycle_number} iniciado automáticamente",
            "",
            "El Reviewer reportó `changes_requested`. Se ha iniciado un ciclo de corrección:",
            "",
            "1. El Reviewer ha sido restablecido a `todo` — se volverá a ejecutar automáticamente "
            "cuando las correcciones estén listas.",
            f"2. Issue de corrección creada: `{fix_id}` ({fix_title})",
            "3. La dependencia Reviewer → Engineer ha sido configurada — no se requiere "
            "intervención manual.",
            f"4. Ciclos de corrección restantes antes de escalar: {cycles_remaining}",
            "",
        ]
        if evidence:
            output_lines.append(f"**Evidencia del Reviewer:** {evidence}")
        if blocker:
            output_lines.append(f"**Problema identificado:** {blocker}")
        if fix_cycle_number > 1:
            output_lines += [
                "",
                f"⚠ Este es el intento de corrección #{fix_cycle_number}. "
                "Si el Reviewer vuelve a rechazar, considera revisar la especificación "
                "o cambiar el adapter del Engineer.",
            ]

        return ExecutionResult(
            status="completed",
            output="\n".join(output_lines),
        )

    def _handle_fix_cycle_limit_resolved(
        self,
        *,
        issue_id: str,
        action: str,
        interaction_payload: dict[str, Any],
        run: dict[str, Any],
        agent_id: str,
    ) -> ExecutionResult:
        """Handle user response to a reviewer_fix_cycle_limit escalation.

        Called when the user accepts or rejects the "fix cycle limit reached"
        request_confirmation interaction.

        accept:
          Create one final engineer issue with a comprehensive description that
          surfaces the full rejection history (cycle count, last blocker, last
          evidence) and reset the reviewer to todo.  This is a last-resort attempt
          with maximum context — the Lead should not create further fix cycles after
          this one.

        reject:
          The user has decided to stop.  Cancel the reviewer (and any todo/in_progress
          children), set the parent issue to cancelled, and post a diagnostic summary.
        """
        fix_cycle_count = int(interaction_payload.get("fix_cycle_count") or 0)
        last_blocker = str(interaction_payload.get("last_blocker") or "").strip()
        last_evidence = str(interaction_payload.get("last_evidence") or "").strip()

        if action == "reject":
            # ── Cancel active children and close parent ───────────────────────
            rows = self._child_issue_rows(issue_id)
            cancelled: list[str] = []
            for row in rows:
                child_id = str(row.get("id") or "").strip()
                child_status = str(row.get("status") or "").strip().lower()
                if child_status in {"done", "cancelled"}:
                    continue
                try:
                    update_issue(self.db_path, issue_id=child_id, status="cancelled")
                    log_activity(
                        self.db_path,
                        action="issue.updated",
                        target_type="issue",
                        target_id=child_id,
                        actor_agent_id=agent_id,
                        run_id=str(run.get("id")),
                        payload={"status": "cancelled", "source": "fix_cycle_limit_rejected"},
                    )
                    cancelled.append(child_id)
                except Exception:
                    logger.warning(
                        "Failed to cancel child %s during fix_cycle_limit rejection for %s",
                        child_id, issue_id, exc_info=True,
                    )
            output_parts = [
                "Lead — Ciclo de corrección cancelado por el usuario",
                "",
                f"El usuario ha rechazado continuar tras {fix_cycle_count} ciclo(s) de corrección. "
                "El proyecto ha sido marcado como cancelado.",
                "",
            ]
            if last_blocker:
                output_parts.append(f"**Último problema detectado por el Reviewer:** {last_blocker}")
            if last_evidence:
                output_parts.append(f"**Evidencia más reciente:** {last_evidence}")
            if cancelled:
                output_parts += [
                    "",
                    f"Issues hijas canceladas: {', '.join(cancelled)}",
                ]
            output_parts += [
                "",
                "Puedes reabrir el proyecto en cualquier momento creando una nueva issue "
                "con una especificación más detallada o con un adapter CLI asignado al Engineer.",
            ]
            return ExecutionResult(
                status="completed",
                output="\n".join(output_parts),
                actions={"issue_status": "cancelled"},
            )

        # accept (or any other value — treat as accept for safety)
        # ── Create one final engineer issue with full rejection history ────────
        rows = self._child_issue_rows(issue_id)
        reviewer_roles = {"reviewer", "code_reviewer"}
        engineer_roles = {"engineer", "software_engineer"}

        # Collect all reviewer findings for the final spec
        reviewer_findings: list[str] = []
        for row in rows:
            if str(row.get("role") or "").strip().lower() not in reviewer_roles:
                continue
            report = row.get("last_agent_report") or {}
            r_evidence = str(report.get("evidence") or "").strip()
            r_blocker = str(report.get("blocker") or "").strip()
            if r_evidence or r_blocker:
                entry = f"- Revisión de '{row.get('title') or 'Reviewer'}'"
                if r_blocker and r_blocker.lower() != "none":
                    entry += f": {r_blocker}"
                if r_evidence and r_evidence.lower() != "none":
                    entry += f" (evidencia: {r_evidence})"
                reviewer_findings.append(entry)

        desc_parts = [
            f"**Fix final (intervención humana) — tras {fix_cycle_count} ciclo(s) fallidos**",
            "",
            "El Reviewer ha rechazado las implementaciones anteriores varias veces. "
            "Este es el intento final autorizado por el usuario.",
            "",
            "## Historial de rechazos del Reviewer",
        ]
        if reviewer_findings:
            desc_parts += reviewer_findings
        else:
            if last_blocker:
                desc_parts.append(f"- Problema persistente: {last_blocker}")
            if last_evidence:
                desc_parts.append(f"- Evidencia: {last_evidence}")
        desc_parts += [
            "",
            "## Instrucciones especiales para este intento final",
            "- Lee **todos** los comentarios del Reviewer antes de escribir una sola línea.",
            "- Confirma que entiendes el problema específico al inicio de tu comentario.",
            "- Si el problema requiere cambiar el adapter o acceder a herramientas externas, "
            "declara bloqueado inmediatamente con `next_owner: lead`. No intentes simular lo que no puedes hacer.",
            "- Implementa solo lo que el Reviewer especifica. Sin features extra.",
            "",
            "El Reviewer se ejecutará automáticamente cuando esta issue esté completada.",
        ]
        final_description = "\n".join(desc_parts)

        # Reset reviewer to todo
        for row in rows:
            if str(row.get("role") or "").strip().lower() not in reviewer_roles:
                continue
            if str(row.get("status") or "").strip().lower() != "done":
                continue
            rev_id = str(row.get("id") or "").strip()
            if not rev_id:
                continue
            try:
                update_issue(self.db_path, issue_id=rev_id, status="todo")
                log_activity(
                    self.db_path,
                    action="issue.updated",
                    target_type="issue",
                    target_id=rev_id,
                    actor_agent_id=agent_id,
                    run_id=str(run.get("id")),
                    payload={
                        "status": "todo",
                        "source": "fix_cycle_limit_accepted",
                        "reason": "user authorised final fix attempt",
                    },
                )
            except Exception:
                logger.warning(
                    "Failed to reset reviewer %s to todo for final fix attempt (issue %s)",
                    rev_id, issue_id, exc_info=True,
                )

        fix_issue = self._create_delegated_issue(
            issue_id=issue_id,
            agent_id=agent_id,
            run=run,
            spec={
                "title": f"Fix final: intento autorizado por usuario tras {fix_cycle_count} ciclos",
                "description": final_description,
                "role": "engineer",
                "complexity": "high",  # this is a hard case; give it the high-complexity signal
            },
            metadata_source="reviewer_fix_cycle_limit_final",
            activity_source="fix_cycle_limit_accepted",
        )
        try:
            sync_default_child_dependencies(self.db_path, parent_issue_id=issue_id)
        except Exception:
            logger.warning(
                "sync_default_child_dependencies failed for final fix attempt on %s",
                issue_id, exc_info=True,
            )

        fix_id = str((fix_issue or {}).get("id") or "desconocido")
        output_lines = [
            "Lead — Intento final autorizado por el usuario",
            "",
            f"Tras {fix_cycle_count} ciclo(s) de corrección, el usuario ha autorizado un último intento.",
            "",
            f"Issue de corrección final creada: `{fix_id}`",
            "El Reviewer ha sido restablecido a `todo` y se ejecutará automáticamente cuando el Engineer termine.",
            "",
            "⚠ Si este intento también falla, cancela el proyecto manualmente o rediseña la especificación "
            "desde cero — el framework no creará más ciclos automáticos.",
        ]
        if last_blocker:
            output_lines += ["", f"**Problema a resolver:** {last_blocker}"]
        if last_evidence:
            output_lines += [f"**Evidencia del Reviewer:** {last_evidence}"]

        return ExecutionResult(
            status="completed",
            output="\n".join(output_lines),
        )

    def _handle_lead_self_file_read(
        self, issue_id: str, run: dict[str, Any], agent_id: str
    ) -> ExecutionResult:
        """Read workspace files directly (after user approval) and post a summary.

        Called when:
        1. A Tier 3 scout reported blocked and the user accepted the
           lead_wants_file_read request_confirmation.
        2. An LLM lead is woken with interaction_resolved + lead_wants_file_read
           + accept AND workspace_files are already injected in the payload.

        For lead_builtin the files are read here on the Python side.  For an LLM
        lead the files arrive via AITEAM_WAKE_PAYLOAD_JSON.workspace_files which
        the executor injects in execute() when the interaction is accepted.
        """
        workspace_root = workspace_root_for_db(self.db_path)
        ws_files = _read_workspace_files(workspace_root)
        if not ws_files:
            return ExecutionResult(
                status="completed",
                output=(
                    "Lead — Lectura directa autorizada\n\n"
                    "El workspace está vacío o no es accesible. No se encontraron archivos legibles.\n"
                    "Puedes pegar el contenido relevante directamente en el thread para continuar."
                ),
            )
        lines = [
            "Lead — Resumen de archivos (lectura directa autorizada por el usuario)",
            "",
            f"{len(ws_files)} archivo(s) en el workspace:",
            "",
        ]
        shown = 0
        for f in ws_files:
            if shown >= 15:
                remaining = len(ws_files) - shown
                lines.append(f"… y {remaining} archivo(s) más (límite de resumen alcanzado).")
                break
            path = f["path"]
            size = f["size_bytes"]
            content = str(f.get("content") or "").strip()
            lines.append(f"### `{path}` ({size} bytes)")
            if content:
                preview = content[:400]
                lines.append("```")
                lines.append(preview)
                if len(content) > 400:
                    lines.append(f"… [{size} bytes total, truncado]")
                lines.append("```")
            lines.append("")
            shown += 1
        lines.append(
            "El Lead ha completado la lectura directa. "
            "Usa esta información para continuar la planificación sin necesitar el scout."
        )
        return ExecutionResult(
            status="completed",
            output="\n".join(lines),
        )

    def _format_blocked_escalation(self, blocked_rows: list[dict[str, Any]]) -> str:
        lines = [
            "Escalación del Lead — Hijos bloqueados",
            "",
            f"Hay {len(blocked_rows)} issue(s) hija(s) en estado bloqueado que requieren tu intervención:",
            "",
        ]
        for r in blocked_rows:
            reason = str(r.get("liveness_reason") or "desconocido")
            assignee = str(r.get("assignee_agent_id") or "sin asignado")
            lines.append(f"- **{r['title']}** (`{r['id']}`)")
            lines.append(f"  Asignado: {assignee}")
            lines.append(f"  Razón: {reason}")
            if "api_only" in reason or "no_workspace_changes" in reason:
                lines.append(
                    "  → Acción sugerida: verificar que el agente usa ops write_file/append_file "
                    "en su respuesta, o reasignar a un adapter CLI/local "
                    "(subscription_cli, Codex CLI, Gemini CLI u Ollama)."
                )
            lines.append("")
        lines.append(
            "Acepta esta notificación para confirmar que has revisado el bloqueo, "
            "o rechaza para mantener el issue padre en espera mientras actúas."
        )
        return "\n".join(lines)

    def _child_issue_rows(self, issue_id: str) -> list[dict[str, Any]]:
        """Return direct child issues enriched with the last comment's parsed agent report.

        The ``last_agent_report`` key is a dict extracted from the ``---AGENT-REPORT---``
        block in the child's most recent comment, or ``None`` if absent.  This lets the
        Lead inspect child quality signals (result, blocker, evidence) without extra
        API calls to the comments endpoint.
        """
        with contextlib.closing(_connect(self.db_path)) as conn:
            rows = conn.execute(
                """
                SELECT i.id, i.title, i.status, i.role, i.assignee_agent_id, i.priority,
                       (SELECT body FROM issue_comments
                        WHERE issue_id = i.id
                        ORDER BY created_at DESC, rowid DESC
                        LIMIT 1) AS last_comment_body
                FROM issues i
                WHERE i.parent_id = ?
                ORDER BY i.priority DESC, i.created_at ASC, i.id ASC
                """,
                (issue_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            d["last_agent_report"] = _parse_agent_report(str(d.get("last_comment_body") or ""))
            result.append(d)
        return result

    def _agent_info(self, agent_id: str) -> dict[str, Any] | None:
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT adapter_type, role, supervisor_agent_id, adapter_config_json, capabilities_json FROM agents WHERE id = ?",
                (agent_id,),
            ).fetchone()
            return dict(row) if row else None

    def _compliance_gate(self, *, run_id: str, issue_id: str, agent_id: str) -> str:
        """Return allowed, blocked, or rejected for high-risk issues.

        High/critical issues require an accepted request_confirmation before
        starting the adapter process. Pending approvals block without starting
        the run; rejected approvals fail the run explicitly.
        """

        if not issue_id:
            return "allowed"
        issue = self._issue_info(issue_id)
        criticality = str((issue or {}).get("criticality") or "").strip().lower()
        if criticality not in {"high", "critical"}:
            return "allowed"

        interaction = self._latest_compliance_interaction(issue_id)
        if interaction is None:
            create_interaction(
                self.db_path,
                issue_id=issue_id,
                kind="request_confirmation",
                continuation_policy="wake_assignee",
                payload={
                    "version": 1,
                    "reason": "criticality_requires_approval",
                    "criticality": criticality,
                    "run_id": run_id,
                    "agent_id": agent_id,
                },
                source_run_id=run_id,
                created_by_agent_id=agent_id,
                title="Approval required",
                summary=f"Issue criticality is {criticality}; confirm before execution.",
                idempotency_key=f"compliance:{issue_id}:criticality",
            )
            return "blocked"

        status = str(interaction.get("status") or "").strip().lower()
        if status == "accepted":
            return "allowed"
        if status == "rejected":
            return "rejected"
        return "blocked"

    def _budget_gate(self, *, run_id: str, issue_id: str, agent_id: str) -> str:
        if not issue_id:
            return "allowed"
        status = check_budget(self.db_path, agent_id=agent_id)
        if status.allowed:
            if status.near_limit:
                self._emit_budget_soft_warning(
                    run_id=run_id, issue_id=issue_id, agent_id=agent_id, status=status
                )
            return "allowed"

        period = status.period
        interaction = self._latest_budget_interaction(issue_id=issue_id, agent_id=agent_id, period=period)
        if interaction is None:
            create_interaction(
                self.db_path,
                issue_id=issue_id,
                kind="request_confirmation",
                continuation_policy="wake_assignee",
                payload={
                    "version": 1,
                    "reason": "budget_exceeded",
                    "run_id": run_id,
                    **status.to_dict(),
                },
                source_run_id=run_id,
                created_by_agent_id=agent_id,
                title="Budget exceeded",
                summary=(
                    f"Agent {agent_id} spent {status.spent_cents} cents in {period}; "
                    f"monthly budget is {status.budget_monthly_cents} cents."
                ),
                idempotency_key=self._budget_idempotency_key(issue_id=issue_id, agent_id=agent_id, period=period),
            )
            return "blocked"

        interaction_status = str(interaction.get("status") or "").strip().lower()
        if interaction_status == "accepted":
            return "allowed"
        if interaction_status == "rejected":
            return "rejected"
        return "blocked"

    def _emit_budget_soft_warning(
        self, *, run_id: str, issue_id: str, agent_id: str, status: BudgetStatus
    ) -> None:
        """Log a one-time-per-period activity event when an agent crosses the soft budget threshold.

        Idempotent: if a budget.soft_threshold_crossed event already exists for this
        agent+period, no new event is written.
        """
        try:
            period = status.period
            with contextlib.closing(_connect(self.db_path)) as conn:
                already = conn.execute(
                    """
                    SELECT 1 FROM activity_log
                    WHERE actor_agent_id = ?
                      AND action = 'budget.soft_threshold_crossed'
                      AND JSON_EXTRACT(payload_json, '$.period') = ?
                    LIMIT 1
                    """,
                    (agent_id, period),
                ).fetchone()
            if already:
                return
            # Use run_id=None so we don't create a FK dependency on the
            # current run — the soft warning is a budget-level event, not
            # a run-level event.  If the run exists it is included in the
            # payload for traceability.
            log_activity(
                self.db_path,
                action="budget.soft_threshold_crossed",
                target_type="agent",
                target_id=agent_id,
                actor_agent_id=agent_id,
                run_id=None,
                payload={
                    "period": period,
                    "spent_cents": status.spent_cents,
                    "budget_monthly_cents": status.budget_monthly_cents,
                    "remaining_cents": status.remaining_cents,
                    "issue_id": issue_id,
                    "source_run_id": run_id,
                },
            )
        except Exception:
            pass  # soft warning must never fail a run

    def _issue_info(self, issue_id: str) -> dict[str, Any] | None:
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT id, criticality FROM issues WHERE id = ?",
                (issue_id,),
            ).fetchone()
            return dict(row) if row else None

    def _latest_budget_interaction(self, *, issue_id: str, agent_id: str, period: str) -> dict[str, Any] | None:
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM issue_thread_interactions
                WHERE issue_id = ?
                  AND kind = 'request_confirmation'
                  AND idempotency_key = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (issue_id, self._budget_idempotency_key(issue_id=issue_id, agent_id=agent_id, period=period)),
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _budget_idempotency_key(*, issue_id: str, agent_id: str, period: str | None = None) -> str:
        return f"budget:{issue_id}:{agent_id}:{period or current_period()}"

    def _latest_compliance_interaction(self, issue_id: str) -> dict[str, Any] | None:
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM issue_thread_interactions
                WHERE issue_id = ?
                  AND kind = 'request_confirmation'
                  AND idempotency_key = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (issue_id, f"compliance:{issue_id}:criticality"),
            ).fetchone()
            return dict(row) if row else None


_SKIP_DIRS: frozenset[str] = frozenset({".aiteam", ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"})


def _read_workspace_files(
    workspace_root: Path,
    *,
    max_per_file_bytes: int = 8192,
    max_total_bytes: int = 32768,
) -> list[dict[str, Any]]:
    """Return a list of ``{path, content, size_bytes}`` for workspace files.

    Used to inject real file contents into the wake payload for reviewer/QA
    runs so they can perform genuine reviews rather than hallucinating based
    on the engineer's description alone.

    Files are read up to *max_per_file_bytes* each; collection stops when
    *max_total_bytes* of content has been gathered.  Binary files and files
    inside hidden/noisy directories are skipped.  Results are sorted by path
    for reproducibility.
    """
    workspace_root = workspace_root.resolve()
    result: list[dict[str, Any]] = []
    total_bytes = 0

    try:
        all_files = sorted(
            (p for p in workspace_root.rglob("*") if p.is_file()),
            key=lambda p: str(p),
        )
    except Exception:
        return result

    for entry in all_files:
        if total_bytes >= max_total_bytes:
            break
        try:
            rel = entry.relative_to(workspace_root)
        except ValueError:
            continue
        # Skip files inside hidden or noisy directories
        parts = rel.parts
        if any(part.startswith(".") or part in _SKIP_DIRS for part in parts[:-1]):
            continue
        # Skip hidden files themselves
        if parts[-1].startswith("."):
            continue
        try:
            raw = entry.read_bytes()
        except Exception:
            continue
        # Heuristic binary check: > 15% non-text bytes in first 512
        sample = raw[:512]
        if sample:
            non_text = sum(1 for b in sample if b < 9 or (13 < b < 32) or b == 127)
            if non_text / len(sample) > 0.15:
                continue
        truncated = len(raw) > max_per_file_bytes
        content = raw[:max_per_file_bytes].decode("utf-8", errors="replace")
        if truncated:
            content += f"\n… [truncated — {len(raw)} bytes total]"
        path_str = str(rel).replace("\\", "/")
        result.append({"path": path_str, "content": content, "size_bytes": len(raw)})
        total_bytes += len(content.encode("utf-8", errors="replace"))

    return result


def _list_workspace_files(workspace_root: Path) -> list[dict[str, Any]]:
    """Return a lightweight listing of workspace files: ``{path, size_bytes}``.

    Used to give continuation engineer runs awareness of what files already
    exist on disk, without inflating the payload with full file contents.
    Binary / hidden files and noisy directories are excluded (same rules as
    :func:`_read_workspace_files`).
    """
    workspace_root = workspace_root.resolve()
    result: list[dict[str, Any]] = []
    try:
        all_files = sorted(
            (p for p in workspace_root.rglob("*") if p.is_file()),
            key=lambda p: str(p),
        )
    except Exception:
        return result
    for entry in all_files:
        try:
            rel = entry.relative_to(workspace_root)
        except ValueError:
            continue
        parts = rel.parts
        if any(part.startswith(".") or part in _SKIP_DIRS for part in parts[:-1]):
            continue
        if parts[-1].startswith("."):
            continue
        try:
            size = entry.stat().st_size
        except Exception:
            continue
        result.append({"path": str(rel).replace("\\", "/"), "size_bytes": size})
    return result


def _execute_file_ops(
    file_ops: list[dict[str, Any]],
    workspace_root: Path,
) -> list[str]:
    """Materialize write_file / append_file / delete_file ops on disk.

    Paths are resolved relative to *workspace_root*.  Any op that would
    escape the workspace (path traversal) is silently skipped.  Returns
    a list of relative paths that were actually touched.

    This runs BEFORE :func:`diff_snapshots` so the resulting workspace
    changes are counted as real evidence — enabling API-only adapters
    (openai_api, anthropic_api, gemini_api) to produce workspace artifacts
    via structured output rather than requiring a CLI subprocess.
    """
    touched: list[str] = []
    workspace_root = workspace_root.resolve()

    for op in file_ops:
        if not isinstance(op, dict):
            continue
        op_type = str(op.get("op") or "")
        rel_path = str(op.get("path") or "").strip()
        if not rel_path or op_type not in ("write_file", "append_file", "delete_file"):
            continue

        # Strip leading slashes / drive letters to force relative resolution
        rel_path = rel_path.lstrip("/\\")
        # Remove any Windows-style drive prefix (e.g. "C:")
        if len(rel_path) >= 2 and rel_path[1] == ":":
            rel_path = rel_path[2:].lstrip("/\\")

        target = (workspace_root / rel_path).resolve()

        # Security: ensure target stays inside workspace_root
        try:
            target.relative_to(workspace_root)
        except ValueError:
            logger.warning("file_op skipped — path escapes workspace: %s", rel_path)
            continue

        try:
            body = str(op.get("body") or "")
            if op_type == "write_file":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")
                touched.append(rel_path)
            elif op_type == "append_file":
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as fh:
                    fh.write(body)
                touched.append(rel_path)
            elif op_type == "delete_file":
                if target.exists():
                    target.unlink()
                    touched.append(rel_path)
        except Exception:
            logger.exception("file_op failed for path: %s", rel_path)

    return touched


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _decode_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _looks_like_plan(body: str) -> bool:
    text = str(body or "").strip().lower()
    if not text:
        return False
    if text.startswith(("plan inicial", "plan detallado", "# plan", "## plan")):
        return True
    markers = ("objetivo", "objective", "sub-issue", "delegacion", "delegación", "riesgo", "criterio")
    return text.startswith("plan") and sum(1 for marker in markers if marker in text) >= 2
