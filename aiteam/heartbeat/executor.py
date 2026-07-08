from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from aiteam.adapters.registry import AdapterRegistry, ExecutionResult
from aiteam.adapters.work_contract import filter_forbidden_ops_for_role
from aiteam.db.agents import create_agent
from aiteam.db.activity_log import log_activity
from aiteam.db.comments import create_comment
from aiteam.db.dependencies import resolve_blocker_wakeups, sync_default_child_dependencies
from aiteam.db.documents import DocumentConflict, get_document, get_context_summary, put_document
from aiteam.db.issues import create_issue
from aiteam.db.finops import BudgetStatus, check_budget, current_period, record_cost
from aiteam.db.interactions import create_interaction, get_interaction, list_interactions
from aiteam.db.issues import get_issue, update_issue
from aiteam.db.runs import append_run_event, finish_run, mark_run_running
from aiteam.db.tool_access import record_tool_access
from aiteam.db.agent_reports import latest_agent_report, record_agent_report
from aiteam.db.wake_payload import build_wake_payload, _parse_agent_report
from aiteam.db.wakeups import enqueue_wakeup, finish_wakeup
from aiteam.heartbeat.scheduler import DispatchResult
from aiteam.lead_intake import apply_accepted_team_proposal, build_team_proposal, format_team_proposal
from aiteam.hiring_economics import log_hiring_decision
from aiteam.project_adapters import choose_adapter_for_role, project_profiles, reconcile_project_agent_policy
from aiteam.provider_governor import GOVERNOR
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
from aiteam.user_config import inject_adapter_secrets, profile_is_connected, resolve_adapter_config
from aiteam.workspace_evidence import WorkspaceDelta, diff_snapshots, snapshot_workspace, workspace_root_for_db


_TERMINAL_EXEC_STATUSES = {"completed", "failed", "skipped"}

_LLM_ADAPTER_TYPES = {"anthropic_api", "anthropic_sonnet", "openai_api", "gemini_api"}

# Roles that must not edit workspace files when running a coding CLI: the Lead
# delegates via ops, scouts inspect and report. Execution roles (engineer,
# lead_executor) are intentionally excluded so they keep workspace-write.
_NON_EDITING_ROLES = {"lead", "team_lead", "file_scout", "web_scout", "context_curator"}


def _is_rate_limit_error(error: str | None) -> bool:
    text = str(error or "").lower()
    return "429" in text or "rate limit" in text or "rate_limit" in text


def _cost_breaker_threshold_cents() -> int:
    """Spend allowed per subtree without workspace progress before escalating.

    Configurable via AITEAM_COST_BREAKER_CENTS; 0 (or negative) disables.
    """
    raw = os.environ.get("AITEAM_COST_BREAKER_CENTS", "").strip()
    if not raw:
        return 300
    try:
        return int(raw)
    except ValueError:
        return 300


def _delegation_churn_limit() -> int:
    """Same-role children allowed under one parent per window before escalating.

    Configurable via AITEAM_DELEGATION_CHURN_LIMIT; 0 (or negative) disables.
    """
    raw = os.environ.get("AITEAM_DELEGATION_CHURN_LIMIT", "").strip()
    if not raw:
        return 8
    try:
        return int(raw)
    except ValueError:
        return 8

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
    "file_scout",
    "test_runner",
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
                # Orchestration/scout roles run the coding CLI read-only so they
                # cannot edit files — they must delegate (Lead) or report
                # (scouts) via structured ops instead of implementing directly.
                if agent_role.lower() in _NON_EDITING_ROLES:
                    adapter_cfg = {**adapter_cfg, "sandbox": "read-only"}
                runtime = runtime.with_config(adapter_cfg)
        # ── Provider degradation fallback (opt-in) ───────────────────────────
        # When the provider is degraded (repeated 429s) and the operator has
        # configured a fallback adapter, worker roles switch to it for this run
        # instead of hammering a saturated provider. Lead/senior keep their
        # adapter — quality of top-level decisions must not silently degrade.
        _fallback_adapter_type = os.environ.get("AITEAM_PROVIDER_FALLBACK_ADAPTER", "").strip()
        if (
            _fallback_adapter_type
            and runtime is not None
            and adapter_type in _LLM_ADAPTER_TYPES
            and agent_role not in {"lead", "team_lead"}
            and GOVERNOR.is_degraded(runtime.descriptor.provider)
        ):
            _fallback_runtime = self.registry.get(_fallback_adapter_type)
            if _fallback_runtime is not None:
                record_tool_access(
                    self.db_path,
                    run_id=run_id,
                    agent_id=agent_id,
                    issue_id=str(run.get("issue_id") or "") or None,
                    tool_name=f"adapter:{_fallback_adapter_type}",
                    decision="allowed",
                    reason=f"provider {runtime.descriptor.provider} degraded — fallback adapter engaged",
                    metadata={"original_adapter_type": adapter_type, "fallback_adapter_type": _fallback_adapter_type},
                )
                runtime = _fallback_runtime
                adapter_type = _fallback_adapter_type
                adapter_cfg = {}
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
        # When the previous run timed out, we re-enqueue with prompt_budget_hint=reduced
        # to halve the comment window and avoid re-timing out on the same large context.
        _prompt_budget_hint = str(ctx.get("prompt_budget_hint") or "").strip().lower()
        _max_comments = 4 if _prompt_budget_hint == "reduced" else 10
        if issue_id_str:
            try:  # noqa: SIM117  (nested try is intentional — outer catches payload build failure)
                payload = build_wake_payload(
                    self.db_path,
                    issue_id=issue_id_str,
                    comment_id=comment_id_str or None,
                    run_id=str(run.get("id") or ""),
                    max_comments=_max_comments,
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
                # ── Blocked-child mandatory action injection (LLM Lead only) ─────
                # When a Lead wakes because a child reported blocked, add an explicit
                # unblock_action_required field and a mandatory instruction so the
                # LLM cannot claim it didn't notice.  Also include the skip_count so
                # the Lead knows how urgent resolution is.
                if (
                    agent_role in {"lead", "team_lead"}
                    and wake_reason == "child_report"
                    and str(ctx.get("child_issue_status") or "") == "blocked"
                ):
                    _blocked_child_id = str(ctx.get("child_issue_id") or "").strip()
                    if _blocked_child_id:
                        try:
                            _bc = get_issue(self.db_path, issue_id=_blocked_child_id)
                            _bc_skip_count = self._count_unblock_skipped(child_issue_id=_blocked_child_id)
                            payload["unblock_action_required"] = [
                                {
                                    "child_issue_id": _blocked_child_id,
                                    "child_title": (_bc or {}).get("title"),
                                    "child_role": (_bc or {}).get("role"),
                                    "previous_failed_attempts": _bc_skip_count,
                                }
                            ]
                            payload["mandatory_instruction"] = (
                                "⚠ ACCIÓN OBLIGATORIA ESTA RUN: "
                                f"El hijo {_blocked_child_id!r} está BLOCKED. "
                                "Debes emitir UNA de estas dos opciones:\n"
                                f"  (a) update_child_issue: {{\"type\": \"update_child_issue\", "
                                f"\"path\": \"{_blocked_child_id}\", \"body\": \"<directiva concreta>\", "
                                "\"status\": \"todo\"}\n"
                                "  (b) create_interaction para preguntar al usuario si la decisión "
                                "es de producto/negocio.\n"
                                "Cualquier otra respuesta (comentar en tu propia issue, escribir 'desbloqueado' "
                                "en el summary, crear un issue nuevo) se registra como no-op y acerca "
                                "el escalado automático al usuario."
                            )
                        except Exception:
                            logger.warning("blocked-child payload injection failed for child %r", _blocked_child_id, exc_info=True)
                # ── Blocked-child reminder for non-child_report wakes ─────────
                # When the Lead is woken manually (or via interaction/chat) and
                # has blocked children, surface the same mandatory instruction
                # so it cannot do a status-check no-op while children are stuck.
                elif (
                    agent_role in {"lead", "team_lead"}
                    and wake_reason in {"manual", "interaction_resolved", "chat_message", "user_chat_message"}
                ):
                    try:
                        _bc_list = self._get_blocked_children(issue_id=issue_id_str)
                        if _bc_list:
                            payload["unblock_action_required"] = _bc_list
                            _bc_ids_str = ", ".join(
                                str(e["child_issue_id"])[-8:] for e in _bc_list
                            )
                            payload["mandatory_instruction"] = (
                                f"⚠ ATENCIÓN: Tienes {len(_bc_list)} hijo(s) BLOCKED sin resolver "
                                f"({_bc_ids_str}). "
                                "Esta run DEBES actuar sobre cada uno con update_child_issue "
                                "(directiva concreta + status:todo) o create_interaction para escalar. "
                                "Escribir comentarios en tu propia issue no desbloquea a nadie."
                            )
                    except Exception:
                        logger.warning(
                            "blocked-child injection for manual wake failed, issue %r",
                            issue_id_str, exc_info=True,
                        )
                # ── Thin-delegation rejection feedback ───────────────────────
                # If the Lead's most recent delegation was hard-rejected for having
                # an empty description, surface that fact prominently in the next
                # wake payload so the Lead cannot keep retrying with empty ops.
                if agent_role in {"lead", "team_lead"} and issue_id_str:
                    try:
                        with contextlib.closing(_connect(self.db_path)) as _conn:
                            _rej = _conn.execute(
                                """
                                SELECT payload_json, created_at FROM activity_log
                                WHERE action = 'delegation.thin_description'
                                  AND target_id = ?
                                ORDER BY created_at DESC LIMIT 1
                                """,
                                (issue_id_str,),
                            ).fetchone()
                        if _rej:
                            import datetime as _dt
                            _rej_time = _rej[0] if isinstance(_rej, tuple) else _rej["created_at"]
                            _rej_payload = json.loads((_rej[0] if isinstance(_rej, tuple) else _rej["payload_json"]) or "{}")
                            _rej_title = str(_rej_payload.get("title") or "")
                            _rej_chars = int(_rej_payload.get("description_chars") or 0)
                            _rejection_note = (
                                "🚫 DELEGACIÓN RECHAZADA: tu último create_issue "
                                f"('{_rej_title[:40]}') fue rechazado porque el campo "
                                f"`description` estaba vacío ({_rej_chars} chars). "
                                "La especificación escrita en tu comentario NO llega al engineer — "
                                "DEBES copiarla dentro del campo `description` del op create_issue. "
                                "Ejemplo mínimo: "
                                '{"type":"create_issue","title":"...","role":"engineer",'
                                '"description":"Tecnología: X. Archivos: Y. Objetivo: Z. Aceptación: W."}'
                            )
                            existing_mi = payload.get("mandatory_instruction", "")
                            payload["mandatory_instruction"] = (
                                _rejection_note + ("\n\n" + existing_mi if existing_mi else "")
                            )
                            payload["delegation_rejection"] = {
                                "title": _rej_title,
                                "description_chars": _rej_chars,
                            }
                    except Exception:
                        logger.warning(
                            "thin-delegation feedback injection failed for issue %r",
                            issue_id_str, exc_info=True,
                        )
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
        # Auto-advance the issue todo → in_progress before the LLM runs.
        # This prevents the control-plane from re-dispatching the same issue
        # while this run is still active (control-plane only requeues 'todo').
        if issue_id_str:
            try:
                _issue_at_start = get_issue(self.db_path, issue_id=issue_id_str)
                _issue_status_now = str(_issue_at_start.get("status") or "") if _issue_at_start else ""
                _run_wake_reason = str(ctx.get("wake_reason") or "")
                # todo → in_progress: prevents control-plane re-dispatch mid-run.
                if _issue_at_start and _issue_status_now == "todo":
                    update_issue(self.db_path, issue_id=issue_id_str, status="in_progress")
                    log_activity(
                        self.db_path,
                        action="issue.auto_in_progress",
                        target_type="issue",
                        target_id=issue_id_str,
                        actor_agent_id=agent_id,
                        run_id=run_id,
                        payload={"source": "run_start"},
                    )
                # done/cancelled → in_progress when the user sends a new chat message.
                # Chat messages always carry new work — reopen the issue automatically
                # so the Lead can delegate new child issues without being stuck in done.
                elif (
                    _issue_at_start
                    and _issue_status_now in {"done", "cancelled"}
                    and _run_wake_reason == "user_chat_message"
                    and agent_role in {"lead", "team_lead"}
                ):
                    update_issue(self.db_path, issue_id=issue_id_str, status="in_progress")
                    log_activity(
                        self.db_path,
                        action="issue.reopened_for_chat",
                        target_type="issue",
                        target_id=issue_id_str,
                        actor_agent_id=agent_id,
                        run_id=run_id,
                        payload={"source": "user_chat_message", "previous_status": _issue_status_now},
                    )
            except Exception:
                logger.warning("auto in_progress failed for issue %s", issue_id_str)
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
        # ── Provider pacing gate ─────────────────────────────────────────────
        # Runs execute sequentially and share each provider's TPM budget; wait
        # out any active cooldown (learned from 429s) before spending tokens.
        if adapter_type in _LLM_ADAPTER_TYPES:
            _paced_seconds = GOVERNOR.acquire(runtime.descriptor.provider)
            if _paced_seconds > 0.5:
                log_activity(
                    self.db_path,
                    action="run.provider_paced",
                    target_type="run",
                    target_id=run_id,
                    actor_agent_id=agent_id,
                    run_id=run_id,
                    payload={
                        "provider": runtime.descriptor.provider,
                        "waited_seconds": round(_paced_seconds, 2),
                    },
                )
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

        # Feed provider health: adapters that bypass http_retry (Anthropic SDK)
        # still surface rate limits here through the error text.
        if (
            adapter_type in _LLM_ADAPTER_TYPES
            and result.status == "failed"
            and _is_rate_limit_error(result.error)
        ):
            GOVERNOR.record_rate_limit(runtime.descriptor.provider)

        # ── Execute file ops from LLM result BEFORE snapshotting workspace ───
        # API-only adapters (openai_api, anthropic_api, gemini_api) return
        # write_file / append_file / delete_file ops in their structured output.
        # Materializing them here lets workspace_delta see them as real changes.
        # Preventive role gate: non-editing roles (Lead, scouts) must never
        # materialize file ops. The CLI sandbox already blocks them for codex
        # runs; this covers API adapters, which have no sandbox. Detection via
        # role.violation stays as the backstop — this makes it preventive.
        _requested_file_ops = (result.actions or {}).get("file_ops") or []
        if _requested_file_ops and str(agent_role or "").strip().lower() in _NON_EDITING_ROLES:
            logger.warning(
                "role.op_denied: non-editing role %r (%s) emitted %d file op(s) — dropped",
                agent_role, agent_id, len(_requested_file_ops),
            )
            try:
                log_activity(
                    self.db_path,
                    action="role.op_denied",
                    target_type="run",
                    target_id=run_id,
                    actor_agent_id=agent_id,
                    run_id=run_id,
                    payload={
                        "role": agent_role,
                        "action_group": "file_ops",
                        "count": len(_requested_file_ops),
                    },
                )
            except Exception:
                pass
            _requested_file_ops = []
        file_ops_applied = _execute_file_ops(
            file_ops=_requested_file_ops,
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
        # A failed run's `output` is raw stdout (for CLI adapters, the echoed
        # prompt) — keep it as a run event for debugging in the Runs tab, but
        # never post it as a chat comment: it spams the user with the full
        # prompt on every failure. The failure is surfaced by the run status
        # and the timeline instead.
        if result.output:
            append_run_event(
                self.db_path,
                run_id=run_id,
                event_type="output",
                stream="stdout",
                payload={"text": _safe_truncate_output(result.output)},
            )
            if issue_id_str and result.status != "failed":
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
                # ── Persist the AGENT-REPORT as a validated artifact ──────────
                # Provenance is captured at the source (this run, this agent)
                # so downstream gates read a trustworthy record instead of
                # re-parsing whatever comment happens to be last on the thread.
                _parsed_report = _parse_agent_report(result.output)
                if _parsed_report:
                    try:
                        record_agent_report(
                            self.db_path,
                            issue_id=issue_id_str,
                            agent_id=agent_id,
                            run_id=run_id,
                            agent_role=agent_role,
                            parsed=_parsed_report,
                        )
                    except Exception:
                        logger.warning("agent report persistence failed for run %s", run_id, exc_info=True)

        # ── Step 2: Apply the adapter's own result actions ───────────────────
        self._apply_result_actions(run=run, agent_id=agent_id, agent_role=agent_role, result=result)

        # ── Step 2.5: Lead unblock audit + circuit breaker ───────────────────
        # When a non-builtin LLM Lead wakes because a child reported blocked,
        # verify it emitted update_child_issue (or a user interaction).  If it
        # did neither, log lead.unblock_skipped and post a system warning.
        # After 3 consecutive skips for the same child, auto-escalate to user.
        _wake_reason_for_audit = str(ctx.get("wake_reason") or "")
        _blocked_child_id_audit = str(ctx.get("child_issue_id") or "").strip()
        _is_llm_lead = (
            agent_role in {"lead", "team_lead"}
            and adapter_type not in _BUILTIN_ADAPTERS
        )
        _is_unblock_wake = (
            _is_llm_lead
            and _wake_reason_for_audit == "child_report"
            and _blocked_child_id_audit
            and str(ctx.get("child_issue_status") or "") == "blocked"
        )
        # A failed run is not a Lead decision: the model never got to speak
        # (rate limit, timeout, transport error). Counting it as a skip blames
        # the Lead for infra problems and trips the circuit breaker with noise.
        if _is_unblock_wake and result.status != "completed":
            log_activity(
                self.db_path,
                action="lead.unblock_run_failed",
                target_type="issue",
                target_id=_blocked_child_id_audit,
                actor_agent_id=agent_id,
                run_id=run_id,
                payload={
                    "parent_issue_id": issue_id_str,
                    "blocked_child_id": _blocked_child_id_audit,
                    "run_status": result.status,
                    "error_code": str(result.error_code or ""),
                },
            )
        elif _is_unblock_wake:
            _acted_on_child = any(
                str(u.get("child_issue_id") or "") == _blocked_child_id_audit
                for u in (result.actions or {}).get("update_child_issues") or []
            )
            _created_interaction = bool((result.actions or {}).get("interactions"))
            if not _acted_on_child and not _created_interaction:
                log_activity(
                    self.db_path,
                    action="lead.unblock_skipped",
                    target_type="issue",
                    target_id=_blocked_child_id_audit,
                    actor_agent_id=agent_id,
                    run_id=run_id,
                    payload={
                        "parent_issue_id": issue_id_str,
                        "blocked_child_id": _blocked_child_id_audit,
                    },
                )
                _skip_count = self._count_unblock_skipped(child_issue_id=_blocked_child_id_audit)
                try:
                    create_comment(
                        self.db_path,
                        issue_id=issue_id_str,
                        author_agent_id=agent_id,
                        source_run_id=run_id,
                        body=(
                            f"⚙ Sistema: el hijo `{_blocked_child_id_audit}` sigue BLOCKED "
                            f"y esta run no emitió `update_child_issue` ni una interacción "
                            f"(intento fallido #{_skip_count}). "
                            "Usa `update_child_issue` con instrucción concreta o crea una interacción "
                            "para preguntar al usuario."
                        ),
                        metadata={"source": "loop_circuit_breaker", "skip_count": _skip_count},
                    )
                except Exception:
                    logger.warning("circuit_breaker: failed to post system comment", exc_info=True)
                # Circuit breaker: 3 skips → escalate to user
                _CIRCUIT_BREAKER_THRESHOLD = 3
                if _skip_count >= _CIRCUIT_BREAKER_THRESHOLD:
                    log_activity(
                        self.db_path,
                        action="loop.detected",
                        target_type="issue",
                        target_id=_blocked_child_id_audit,
                        actor_agent_id=agent_id,
                        run_id=run_id,
                        payload={
                            "parent_issue_id": issue_id_str,
                            "blocked_child_id": _blocked_child_id_audit,
                            "skip_count": _skip_count,
                        },
                    )
                    logger.warning(
                        "loop.detected: Lead %s has skipped unblocking child %s %d times — escalating to user",
                        agent_id, _blocked_child_id_audit, _skip_count,
                    )
                    try:
                        create_interaction(
                            self.db_path,
                            issue_id=issue_id_str,
                            kind="request_confirmation",
                            payload={
                                "version": 1,
                                "reason": "lead_engineer_loop_detected",
                                "blocked_child_id": _blocked_child_id_audit,
                                "skip_count": _skip_count,
                            },
                            continuation_policy="wake_assignee",
                            idempotency_key=f"loop_circuit_breaker:{_blocked_child_id_audit}",
                            source_run_id=run_id,
                            created_by_agent_id=agent_id,
                            title=f"Bucle detectado — engineer bloqueado sin resolución ({_skip_count} intentos)",
                            summary=(
                                f"El engineer en `{_blocked_child_id_audit}` lleva "
                                f"{_skip_count} runs bloqueado y el Lead no ha podido resolverlo. "
                                "Acepta para dar al Lead un último intento con instrucciones completas. "
                                "Rechaza para cancelar la issue del engineer y reasignar la tarea manualmente."
                            ),
                        )
                    except Exception:
                        logger.warning("circuit_breaker: failed to create escalation interaction", exc_info=True)
            else:
                if _acted_on_child:
                    log_activity(
                        self.db_path,
                        action="lead.unblock_attempted",
                        target_type="issue",
                        target_id=_blocked_child_id_audit,
                        actor_agent_id=agent_id,
                        run_id=run_id,
                        payload={"parent_issue_id": issue_id_str, "blocked_child_id": _blocked_child_id_audit},
                    )

        # ── Step 3: Collect structured evidence from DB (post-comment) ───────
        workspace_files_changed = len(workspace_delta.created) + len(workspace_delta.modified)
        evidence = collect_run_evidence(
            self.db_path,
            run_id=run_id,
            workspace_files_changed=workspace_files_changed,
        )

        # ── Step 4: Classify liveness (pure function, no regex) ───────────────
        useful_output = bool(str(result.output or "").strip())
        explicit_issue_status = str((result.actions or {}).get("issue_status") or "").strip()
        has_explicit_issue_status = bool(explicit_issue_status)
        # Only treat blocked/cancelled as a deliberate terminal declaration that
        # should bypass the plan_only continuation loop.  A 'done' claim without
        # workspace evidence should still go through plan_only so the engineer is
        # nudged to provide real file output.
        explicit_blocking_declared = explicit_issue_status in {"blocked", "cancelled"}
        exec_status = result.status if result.status in _TERMINAL_EXEC_STATUSES else "completed"
        liveness_result = classify_run_liveness(
            run_status=exec_status,
            evidence=evidence,
            adapter_type=adapter_type,
            agent_role=agent_role,
            useful_output=useful_output,
            has_explicit_issue_status=has_explicit_issue_status,
            explicit_blocking_declared=explicit_blocking_declared,
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
            self._apply_result_actions(run=run, agent_id=agent_id, agent_role=agent_role, result=override_result)

        # ── Step 5.5: Adapter recovery on continuation exhaustion (RUN-003) ──
        # An agent that exhausted its continuations without workspace evidence
        # is blocked with a correct diagnosis but no recovery route. If the
        # project has another connected adapter, swap the agent to it and
        # reopen the issue once — mechanical decision, the Lead need not think.
        if (
            liveness_result.state == "blocked"
            and "_exhausted_at_attempt_" in str(liveness_result.reason or "")
            and agent_role not in {"lead", "team_lead"}
            and issue_id_str
        ):
            try:
                self._attempt_adapter_recovery(
                    issue_id=issue_id_str,
                    agent_id=agent_id,
                    run_id=run_id,
                    failed_adapter_type=adapter_type,
                    agent_role=agent_role,
                    liveness_reason=str(liveness_result.reason or ""),
                )
            except Exception:
                logger.warning("adapter recovery failed for issue %s", issue_id_str, exc_info=True)

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

        # ── Role-contract audit ──────────────────────────────────────────────
        # Orchestrator/scout roles must delegate/report, never edit files. They
        # run the coding CLI read-only, but a misconfigured adapter (or a future
        # regression) could let them write. Record any workspace change by a
        # non-editing role as an auditable violation so role adherence is
        # observable, not just prompted.
        if workspace_delta.changed and str(agent_role or "").strip().lower() in _NON_EDITING_ROLES:
            logger.warning(
                "role.violation: non-editing role %r (%s) produced workspace changes: %s",
                agent_role, agent_id, workspace_delta.to_dict(),
            )
            try:
                log_activity(
                    self.db_path,
                    action="role.violation",
                    target_type="run",
                    target_id=run_id,
                    actor_agent_id=agent_id,
                    run_id=run_id,
                    payload={
                        "role": agent_role,
                        "reason": "non_editing_role_edited_workspace",
                        "delta": workspace_delta.to_dict(),
                        "issue_id": issue_id_str or None,
                    },
                )
            except Exception:
                logger.warning("role.violation log failed for run %s", run_id, exc_info=True)

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
        # ── Cost circuit breaker ──────────────────────────────────────────────
        # Spend without workspace progress is the economic twin of a loop:
        # escalate before the subtree silently burns the budget.
        if issue_id_str:
            try:
                self._check_cost_breaker(issue_id=issue_id_str, run_id=run_id, agent_id=agent_id)
            except Exception:
                logger.warning("cost breaker check failed for issue %s", issue_id_str, exc_info=True)

        wakeup_terminal = "finished" if final_status in {"completed", "skipped"} else "failed"
        finish_wakeup(
            self.db_path,
            wakeup_id=wakeup_id,
            status=wakeup_terminal,
            run_id=run_id,
            error=result.error if wakeup_terminal == "failed" else None,
        )

        # ── Timeout auto-retry ────────────────────────────────────────────────
        # When a run fails because the adapter timed out, re-enqueue the agent
        # with prompt_budget_hint=reduced (halves the comment window).
        # After _MAX_TIMEOUT_RETRIES attempts the issue is marked blocked and the
        # Lead is woken to intervene — prevents silent infinite timeout loops.
        _is_timeout_failure = (
            final_status == "failed"
            and (
                str(result.error_code or "") in {"subscription_cli_timeout", "liveness_timeout"}
                or "timeout" in str(result.error or "").lower()
            )
        )
        if _is_timeout_failure and issue_id_str:
            try:
                _MAX_TIMEOUT_RETRIES = 2
                _retry_count = self._count_timeout_retries(agent_id=agent_id, issue_id=issue_id_str)
                if _retry_count < _MAX_TIMEOUT_RETRIES:
                    _next_attempt = _retry_count + 1
                    enqueue_wakeup(
                        self.db_path,
                        agent_id=agent_id,
                        source="timeout_retry",
                        reason="timeout_retry",
                        payload={
                            "issue_id": issue_id_str,
                            "wake_reason": str(ctx.get("wake_reason") or ""),
                            "prompt_budget_hint": "reduced",
                            "timeout_retry_attempt": _next_attempt,
                            "source_run_id": run_id,
                        },
                        idempotency_key=f"timeout_retry:{issue_id_str}:{agent_id}:{run_id}",
                    )
                    log_activity(
                        self.db_path,
                        action="run.timeout_retry",
                        target_type="run",
                        target_id=run_id,
                        actor_agent_id=agent_id,
                        run_id=run_id,
                        payload={
                            "issue_id": issue_id_str,
                            "attempt": _next_attempt,
                            "max_attempts": _MAX_TIMEOUT_RETRIES,
                            "prompt_budget_hint": "reduced",
                        },
                    )
                    logger.info(
                        "timeout_retry: re-enqueued %s for issue %s (attempt %d/%d, prompt_budget=reduced)",
                        agent_id, issue_id_str, _next_attempt, _MAX_TIMEOUT_RETRIES,
                    )
                else:
                    # Retry cap reached — block the issue and wake the Lead.
                    logger.warning(
                        "timeout_retry: cap reached (%d) for agent %s issue %s — marking blocked",
                        _retry_count, agent_id, issue_id_str,
                    )
                    try:
                        update_issue(self.db_path, issue_id=issue_id_str, status="blocked")
                        log_activity(
                            self.db_path,
                            action="issue.updated",
                            target_type="issue",
                            target_id=issue_id_str,
                            actor_agent_id=agent_id,
                            run_id=run_id,
                            payload={"status": "blocked", "source": "timeout_retry_cap"},
                        )
                        create_comment(
                            self.db_path,
                            issue_id=issue_id_str,
                            body=(
                                f"⚙ Sistema: {_retry_count} timeout(s) consecutivos — issue marcada como blocked. "
                                "El Lead debe simplificar el alcance, reducir el contexto, o usar un adapter con "
                                "mayor límite de tiempo."
                            ),
                            author_agent_id="system",
                        )
                    except Exception:
                        logger.warning("timeout_retry: failed to block issue %s", issue_id_str, exc_info=True)
                    self._enqueue_supervisor_report(
                        issue_id=issue_id_str,
                        reporting_agent_id=agent_id,
                        source_run_id=run_id,
                    )
            except Exception:
                logger.warning("timeout_retry: failed for run %s issue %s", run_id, issue_id_str, exc_info=True)

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

            if reason == "lead_engineer_loop_detected":
                _cb_child_id = str(payload.get("blocked_child_id") or "")
                _cb_skips = int(payload.get("skip_count") or 0)
                if action == "reject":
                    # Cancel the stuck engineer issue
                    cancel_actions: dict[str, Any] = {}
                    if _cb_child_id:
                        try:
                            update_issue(self.db_path, issue_id=_cb_child_id, status="cancelled")
                            log_activity(
                                self.db_path,
                                action="issue.updated",
                                target_type="issue",
                                target_id=_cb_child_id,
                                actor_agent_id=agent_id,
                                run_id=str(run.get("id")),
                                payload={"status": "cancelled", "source": "loop_circuit_breaker_reject"},
                            )
                        except Exception:
                            logger.warning("loop_circuit_breaker: failed to cancel child %s", _cb_child_id, exc_info=True)
                    return ExecutionResult(
                        status="completed",
                        output=(
                            f"Lead — Bucle cortado (rechazado)\n\n"
                            f"La issue `{_cb_child_id}` ha sido cancelada tras {_cb_skips} intentos fallidos. "
                            "Reasigna la tarea manualmente si es necesario."
                        ),
                    )
                # action == "accept": give Lead one more attempt with full context
                return ExecutionResult(
                    status="completed",
                    output=(
                        f"Lead — Último intento autorizado\n\n"
                        f"El usuario ha autorizado un intento más para desbloquear `{_cb_child_id}`. "
                        "Usa `update_child_issue` con una directiva completa y detallada para resolverlo."
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
                "CONTINUACION OBLIGATORIA: La run anterior produjo texto pero CERO cambios en el workspace. "
                "Esta run DEBE incluir write_file ops con contenido real. "
                "Si necesitas audio → usa Web Audio API en JavaScript (AudioContext + OscillatorNode). "
                "Si necesitas imagenes → usa SVG o canvas. "
                "Para cualquier binario → crea un archivo stub con texto explicativo. "
                "Para declarar bloqueo real: incluye ops {type:set_status, status:blocked} + {type:notify_supervisor}. "
                "ATTENCION: escribir 'blocked' solo en el summary NO tiene efecto — el sistema te seguira despertando "
                "hasta que uses write_file ops o los ops de bloqueo correctos."
            )
        else:
            instruction = (
                "CONTINUACION OBLIGATORIA: La run anterior termino sin output ni evidencia concreta. "
                "Debes producir write_file ops con archivos reales, o usar ops {set_status:blocked} + {notify_supervisor} "
                "para declarar bloqueo. Un summary vacio o solo texto no cuenta como progreso."
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

    def _apply_result_actions(
        self,
        *,
        run: dict[str, Any],
        agent_id: str,
        agent_role: str = "",
        result: ExecutionResult,
    ) -> None:
        actions = result.actions or {}
        issue_id = str(run.get("issue_id") or "")
        if not issue_id:
            return

        # ── Role permission matrix (op filter) ───────────────────────────────
        # Drop action groups outside the role's vocabulary, per the declarative
        # matrix in work_contract (Tier 3: read-and-report only; Tier 2: work
        # and report, but never hire/direct/replan — those are Lead levers).
        # Enforced in code so a prompt drift or model whim cannot collapse the
        # hierarchy. Dropped groups are logged as auditable role violations.
        _OP_TYPE_TO_ACTION_KEY = {
            "create_issue": "create_issues",
            "create_interaction": "interactions",
            "update_plan": "update_plan",
            "update_child_issue": "update_child_issues",
            "write_file": "file_ops",
            "append_file": "file_ops",
            "delete_file": "file_ops",
        }
        from aiteam.adapters.work_contract import forbidden_ops_for_role
        _forbidden_action_keys = {
            _OP_TYPE_TO_ACTION_KEY[op_type]
            for op_type in forbidden_ops_for_role(agent_role)
            if op_type in _OP_TYPE_TO_ACTION_KEY
        }
        for _forbidden_key in _forbidden_action_keys:
            if _forbidden_key in actions:
                logger.warning(
                    "Dropped forbidden action group %r for role %r (issue %s)",
                    _forbidden_key,
                    agent_role,
                    issue_id,
                )
                try:
                    log_activity(
                        self.db_path,
                        action="role.op_denied",
                        target_type="issue",
                        target_id=issue_id,
                        actor_agent_id=agent_id,
                        run_id=str(run.get("id")),
                        payload={"role": agent_role, "action_group": _forbidden_key},
                    )
                except Exception:
                    pass
                actions = {k: v for k, v in actions.items() if k != _forbidden_key}

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
            _summary = interaction.get("summary")
            # ── Cycle-close verification (non-negotiable) ─────────────────────
            # When a Lead proposes closing the cycle, append the machine-computed
            # verification (structured reviewer verdict + workspace stub scan) so
            # the user decides on objective data, not on the Lead's re-narration.
            _payload_reason = str(((interaction.get("payload") or {}) or {}).get("reason") or "")
            _creator_adapter = str((self._agent_info(agent_id) or {}).get("adapter_type") or "")
            if (
                _payload_reason == "initial_cycle_ready"
                and agent_role.lower() in {"lead", "team_lead"}
                # Builtin lead summaries already embed the verification block.
                and _creator_adapter not in _BUILTIN_ADAPTERS
            ):
                try:
                    _verification = self._machine_close_verification(issue_id)
                    if _verification:
                        _summary = (
                            str(_summary or "").strip()
                            + "\n\n**Verificación automática del sistema:**\n"
                            + _verification
                        )
                except Exception:
                    logger.warning("machine close verification failed for issue %s", issue_id, exc_info=True)
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
                summary=_summary,
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
            # ── Issue state machine per role (fase 4) ────────────────────────
            # Workers may progress/close their OWN issue but not re-queue it
            # (todo/backlog fuels self-continuation loops) nor resurrect a
            # terminal one. The Lead tier keeps full transition authority.
            _denied_reason = self._issue_status_transition_denied(
                issue_id=issue_id, agent_role=agent_role, new_status=issue_status
            )
            if _denied_reason:
                logger.warning(
                    "role.op_denied (issue_status): role=%r issue=%s target=%r reason=%s",
                    agent_role, issue_id, issue_status, _denied_reason,
                )
                try:
                    log_activity(
                        self.db_path,
                        action="role.op_denied",
                        target_type="issue",
                        target_id=issue_id,
                        actor_agent_id=agent_id,
                        run_id=str(run.get("id")),
                        payload={
                            "role": agent_role,
                            "action_group": "issue_status",
                            "target_status": issue_status,
                            "reason": _denied_reason,
                        },
                    )
                except Exception:
                    pass
                issue_status = None
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
        # ── Auto-supervisor report for terminal statuses ───────────────────────
        # LLM agents don't always emit notify_supervisor when they complete.
        # Reviewers in particular are API-only (no workspace changes) so the
        # liveness system never fires the workspace-based notify_supervisor.
        # Auto-report on any terminal issue_status so the Lead is always woken
        # without requiring a manual wakeup between the reviewer and the Lead.
        # Idempotency in _enqueue_supervisor_report prevents double-wakeups when
        # notify_supervisor was already emitted above.
        _AUTO_REPORT_ROLES = {
            "reviewer", "code_reviewer",
            "engineer", "software_engineer",
            "qa", "lead_executor",
        }
        if (
            isinstance(issue_status, str)
            and issue_status in {"done", "blocked", "cancelled"}
            and agent_role.lower() in _AUTO_REPORT_ROLES
            and not actions.get("notify_supervisor")
        ):
            try:
                self._enqueue_supervisor_report(
                    issue_id=issue_id,
                    reporting_agent_id=agent_id,
                    source_run_id=str(run.get("id")),
                )
            except Exception:
                logger.warning(
                    "auto-supervisor-report failed for issue %s role %s", issue_id, agent_role, exc_info=True
                )

        # add_comments: extra comments emitted by the LLM adapter (beyond result.output)
        for body in actions.get("add_comments") or []:
            if not isinstance(body, str) or not body.strip():
                continue
            # Codex-style adapters put the AGENT-REPORT block in add_comment
            # (result.output is just the short summary) — capture it here too.
            _comment_report = _parse_agent_report(body)
            if _comment_report:
                try:
                    record_agent_report(
                        self.db_path,
                        issue_id=issue_id,
                        agent_id=agent_id,
                        run_id=str(run.get("id")),
                        agent_role=agent_role,
                        parsed=_comment_report,
                    )
                except Exception:
                    logger.warning("agent report persistence (add_comment) failed for issue %s", issue_id, exc_info=True)
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

        # update_child_issues: Lead posts a directive and/or requeues a child issue
        for child_update in actions.get("update_child_issues") or []:
            if not isinstance(child_update, dict):
                continue
            child_issue_id = str(child_update.get("child_issue_id") or "").strip()
            if not child_issue_id:
                continue
            try:
                child_issue = get_issue(self.db_path, issue_id=child_issue_id)
                if child_issue is None:
                    logger.warning(
                        "update_child_issue: child %s not found (from issue %s)", child_issue_id, issue_id
                    )
                    continue
                # Safety: only allow updating direct children of the current issue
                if str(child_issue.get("parent_id") or "") != issue_id:
                    logger.warning(
                        "update_child_issue: issue %s is not a child of %s — skipped",
                        child_issue_id,
                        issue_id,
                    )
                    continue
                new_status = str(child_update.get("status") or "").strip()
                directive_body = str(child_update.get("body") or "").strip()
                _active_requeue_statuses = {"todo", "in_progress"}
                # Validate: requeuing without a directive is a no-op for the engineer.
                # Drop the op and log a warning so the Lead is informed next run.
                if new_status in _active_requeue_statuses and not directive_body:
                    logger.warning(
                        "update_child_issue: child %s requeued to %r with no directive body "
                        "— engineer will have no new information. Body is required. Op dropped.",
                        child_issue_id, new_status,
                    )
                    try:
                        create_comment(
                            self.db_path,
                            issue_id=issue_id,
                            author_agent_id=agent_id,
                            source_run_id=str(run.get("id")),
                            body=(
                                f"⚙ Sistema: update_child_issue para `{child_issue_id}` rechazado — "
                                f"status={new_status!r} pero body vacío. "
                                "El engineer necesita una instrucción concreta. Incluye un `body` con la directiva."
                            ),
                            metadata={"source": "update_child_issue_validation"},
                        )
                    except Exception:
                        pass
                    continue
                if new_status:
                    update_issue(self.db_path, issue_id=child_issue_id, status=new_status)
                    log_activity(
                        self.db_path,
                        action="issue.updated",
                        target_type="issue",
                        target_id=child_issue_id,
                        actor_agent_id=agent_id,
                        run_id=str(run.get("id")),
                        payload={"status": new_status, "source": "update_child_issue"},
                    )
                if directive_body:
                    try:
                        dir_comment = create_comment(
                            self.db_path,
                            issue_id=child_issue_id,
                            author_agent_id=agent_id,
                            source_run_id=str(run.get("id")),
                            body=_safe_truncate_output(directive_body),
                            metadata={"source": "lead_directive"},
                        )
                        log_activity(
                            self.db_path,
                            action="comment.created",
                            target_type="comment",
                            target_id=dir_comment["id"],
                            actor_agent_id=agent_id,
                            run_id=str(run.get("id")),
                            payload={"issue_id": child_issue_id, "source": "action:update_child_issue"},
                        )
                    except Exception:
                        logger.warning(
                            "update_child_issue: failed to post directive comment on %s", child_issue_id, exc_info=True
                        )
                # If the Lead set the child to an active status, enqueue a wakeup
                if new_status in _active_requeue_statuses:
                    child_assignee = str(child_issue.get("assignee_agent_id") or "").strip()
                    if child_assignee:
                        enqueue_wakeup(
                            self.db_path,
                            agent_id=child_assignee,
                            source="unblock",
                            reason="lead_directive",
                            payload={
                                "issue_id": child_issue_id,
                                "parent_issue_id": issue_id,
                                "wake_reason": "lead_directive",
                            },
                            idempotency_key=f"lead_directive:{child_issue_id}:{str(run.get('id') or '')}",
                        )
                        log_activity(
                            self.db_path,
                            action="lead.unblock_attempted",
                            target_type="issue",
                            target_id=child_issue_id,
                            actor_agent_id=agent_id,
                            run_id=str(run.get("id")),
                            payload={
                                "parent_issue_id": issue_id,
                                "new_status": new_status,
                                "has_directive": bool(directive_body),
                            },
                        )
                        logger.info(
                            "update_child_issue: requeued child %s (status=%r) with directive from issue %s",
                            child_issue_id,
                            new_status,
                            issue_id,
                        )
            except Exception:
                logger.warning(
                    "update_child_issue failed for child %s (from issue %s)", child_issue_id, issue_id, exc_info=True
                )

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

            # ── Delegation churn breaker ──────────────────────────────────────
            # The automatic fix-cycle cap only counts the auto-created path; a
            # Lead that cancels reviewers and hand-creates new engineer/reviewer
            # pairs bypasses it (observed: 13+ cycles in one evening). Cap the
            # rate of same-role children under one parent and escalate instead
            # of letting the churn run forever.
            if self._delegation_churn_blocked(
                parent_issue_id=issue_id,
                role_for_issue=role_for_issue,
                agent_id=agent_id,
                run_id=str(run.get("id")),
                title=title_val,
            ):
                return None

            # ── Pre-flight delegation quality check ───────────────────────────
            # Sparse descriptions produce blocked or wrong engineer runs.
            # Log a structured warning so it's visible in activity and metrics.
            _desc_val = str(spec.get("description") or "").strip()
            _MIN_DESCRIPTION_CHARS = 120
            _HARD_BLOCK_CHARS = 20  # truly empty — engineer will block immediately
            if len(_desc_val) < _MIN_DESCRIPTION_CHARS and role_for_issue in {"engineer", "software_engineer"}:
                logger.warning(
                    "delegation_quality: issue %r for role=%r has short description (%d chars < %d). "
                    "Engineers need technology, file list, and acceptance criteria to avoid blocking.",
                    title_val, role_for_issue, len(_desc_val), _MIN_DESCRIPTION_CHARS,
                )
                log_activity(
                    self.db_path,
                    action="delegation.thin_description",
                    target_type="issue",
                    target_id=issue_id,
                    actor_agent_id=agent_id,
                    run_id=str(run.get("id")),
                    payload={
                        "title": title_val,
                        "role": role_for_issue,
                        "description_chars": len(_desc_val),
                        "minimum_chars": _MIN_DESCRIPTION_CHARS,
                    },
                )
                # Hard rejection: a truly empty description guarantees engineer blockage.
                # Post a system comment on the Lead's issue so it knows to re-delegate
                # with proper specs, and skip creating the child issue entirely.
                if len(_desc_val) < _HARD_BLOCK_CHARS:
                    _rejection_body = (
                        f"⚙ Sistema: delegación rechazada para «{title_val}» — "
                        f"descripción vacía ({len(_desc_val)} chars, mínimo: {_HARD_BLOCK_CHARS}). "
                        "Vuelve a delegar incluyendo: tecnología, archivos a modificar y criterios de aceptación."
                    )
                    try:
                        create_comment(
                            self.db_path,
                            issue_id=issue_id,
                            body=_rejection_body,
                            author_agent_id="system",
                        )
                    except Exception:
                        logger.warning("failed to post thin-delegation rejection comment on %s", issue_id)
                    return None

            # ── Action routing override ───────────────────────────────────────
            # If the spec includes criticality + action_type, apply route_action()
            # to determine the effective role regardless of what the LLM proposed.
            # This enforces tier discipline even when the LLM mis-assigns a role.
            _criticality = str(spec.get("criticality") or "").strip().lower()
            _complexity = str(spec.get("complexity") or "").strip().lower()
            _action_type = str(spec.get("action_type") or "").strip().lower()
            if _criticality and _action_type:
                from aiteam.action_routing import route_action, pick_role_for_routing  # noqa: PLC0415
                _routing = route_action(
                    criticality=_criticality or "medium",
                    complexity=_complexity or "medium",
                    action_type=_action_type,
                )
                _effective_role = pick_role_for_routing(_routing, _action_type)
                if _effective_role != role_for_issue:
                    logger.info(
                        "action.routed: issue %s proposed role=%r overridden to %r "
                        "(criticality=%r complexity=%r action_type=%r routing=%r)",
                        issue_id, role_for_issue, _effective_role,
                        _criticality, _complexity, _action_type, _routing.value,
                    )
                    log_activity(
                        self.db_path,
                        action="action.routed",
                        target_type="issue",
                        target_id=issue_id,
                        actor_agent_id=agent_id,
                        run_id=str(run.get("id")),
                        payload={
                            "proposed_role": role_for_issue,
                            "effective_role": _effective_role,
                            "criticality": _criticality,
                            "complexity": _complexity,
                            "action_type": _action_type,
                            "routing": _routing.value,
                        },
                    )
                role_for_issue = _effective_role

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
            # Structured acceptance criteria travel with the issue so the
            # engineer knows the done-bar and the reviewer can judge against
            # an explicit list instead of re-deriving it from prose.
            _criteria = [
                str(item).strip()
                for item in (spec.get("acceptance_criteria") or [])
                if isinstance(item, str) and str(item).strip()
            ]
            _issue_metadata: dict[str, Any] = {"source": metadata_source, "parent_issue_id": issue_id}
            if _criteria:
                _issue_metadata["acceptance_criteria"] = _criteria[:12]
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
                metadata=_issue_metadata,
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
        if not {"engineer", "lead_executor"} & set(created_child_roles):
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
    _TIER3_ROLES = frozenset({"file_scout", "web_scout", "context_curator", "test_runner"})

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
            # lead_executor uses "senior" seniority and inherits the Lead's adapter
            # so it runs the same senior LLM as the Lead's planning runs.
            if role_key == "lead_executor":
                effective_seniority = "senior"
                lead_info = self._agent_info(supervisor_agent_id or "role:lead") or {}
                lead_adapter_type = str(lead_info.get("adapter_type") or "").strip()
                lead_adapter_config = _decode_json(lead_info.get("adapter_config_json") or "{}")
                selection = {
                    "adapter_type": lead_adapter_type or "role_builtin",
                    "adapter_config": lead_adapter_config,
                }
            elif role_key in self._TIER3_ROLES:
                effective_seniority = "cheap"
                selection = choose_adapter_for_role(role_key, effective_seniority, project_profiles(Path(self.db_path).parent))
            else:
                effective_seniority = "standard"
                selection = choose_adapter_for_role(role_key, effective_seniority, project_profiles(Path(self.db_path).parent))
            row = create_agent(
                self.db_path,
                agent_id=agent_id,
                role=role_key,
                name=role_key.replace("_", " ").title(),
                seniority=effective_seniority,
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
            log_hiring_decision(
                self.db_path,
                agent_id=str(row["id"]),
                role=role_key,
                adapter_type=str((selection or {}).get("adapter_type") or "role_builtin"),
                adapter_config=(selection or {}).get("adapter_config") or {},
                adapter_profile_id=(selection or {}).get("adapter_profile_id"),
                source="llm_create_issue",
                run_id=source_run_id or None,
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

        # Re-fetch the child issue to get its status *after* liveness overrides were applied
        # (liveness may have auto-set it to 'done' or 'blocked' earlier in the same run).
        child_status = str(issue.get("status") or "unknown")
        fresh_issue = get_issue(self.db_path, issue_id=issue_id)
        if fresh_issue:
            child_status = str(fresh_issue.get("status") or child_status)

        # ── Sibling-completion gate ───────────────────────────────────────────
        # If the child finished normally (done/in_review/in_progress) but there
        # are siblings still actively working, hold back the supervisor wakeup.
        # The supervisor will be woken naturally when the last active sibling
        # finishes or when any sibling is blocked.
        #
        # Why: without this gate the supervisor is woken once per child
        # completion — N LLM calls instead of 1 — each seeing a partial picture
        # and adding a "waiting for siblings" comment that wastes budget.
        #
        # Exceptions: always wake for blocked/cancelled children so the
        # supervisor can intervene immediately.
        _immediate_statuses = {"blocked", "cancelled"}
        if child_status not in _immediate_statuses:
            if self._has_active_siblings(issue_id=issue_id, parent_issue_id=parent_issue_id):
                logger.debug(
                    "_enqueue_supervisor_report: suppressing wakeup for %s (child %s %s) — siblings still active",
                    supervisor_agent_id,
                    issue_id,
                    child_status,
                )
                return
        # ─────────────────────────────────────────────────────────────────────

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

    def _count_unblock_skipped(self, *, child_issue_id: str) -> int:
        """Count how many times the Lead has failed to unblock a specific blocked child.

        Counts ``lead.unblock_skipped`` events in the activity_log where
        target_id == child_issue_id.  Used by the circuit breaker to decide
        when to escalate to the user.
        """
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE action = 'lead.unblock_skipped' AND target_id = ?",
                (child_issue_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def _count_timeout_retries(self, *, agent_id: str, issue_id: str) -> int:
        """Count how many timeout_retry wakeups have been enqueued for this agent+issue.

        Used by the timeout auto-retry logic to enforce the _MAX_TIMEOUT_RETRIES cap.
        Counts wakeup_requests with source='timeout_retry' whose payload references the issue.
        """
        if not agent_id or not issue_id:
            return 0
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM wakeup_requests
                WHERE agent_id = ?
                  AND source = 'timeout_retry'
                  AND payload_json LIKE ?
                """,
                (agent_id, f'%{issue_id}%'),
            ).fetchone()
        return int(row[0]) if row else 0

    def _get_blocked_children(self, *, issue_id: str) -> list[dict]:
        """Return blocked direct child issues for *issue_id* with their skip counts.

        Used to inject ``unblock_action_required`` into the Lead's payload when
        it wakes for any reason (not just ``child_report``) while children are stuck.
        """
        if not issue_id:
            return []
        with contextlib.closing(_connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, title, role FROM issues WHERE parent_id = ? AND status = 'blocked'",
                (issue_id,),
            ).fetchall()
        result = []
        for row in rows:
            skip_count = self._count_unblock_skipped(child_issue_id=str(row["id"]))
            result.append(
                {
                    "child_issue_id": str(row["id"]),
                    "child_title": row["title"],
                    "child_role": row["role"],
                    "previous_failed_attempts": skip_count,
                }
            )
        return result

    def _has_active_siblings(self, *, issue_id: str, parent_issue_id: str) -> bool:
        """Return True when the parent has at least one sibling still actively working.

        "Actively working" means status is NOT in {done, cancelled, blocked} —
        i.e., the sibling is still todo / in_progress / in_review / backlog.

        Used by _enqueue_supervisor_report to suppress intermediate supervisor
        wakeups when multiple parallel children are running.  The supervisor
        will be woken naturally when the last active sibling finishes.
        """
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM issues
                WHERE parent_id = ?
                  AND id != ?
                  AND status NOT IN ('done', 'cancelled', 'blocked')
                LIMIT 1
                """,
                (parent_issue_id, issue_id),
            ).fetchone()
        return row is not None

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

        Note: the QA Tier 2 role has been removed.  The Reviewer absorbs static QA.
        For runtime test execution, a ``test_runner`` (Tier 3) may be delegated; its
        output is informational — the Lead reads exit codes and decides whether to
        re-open a fix cycle.
        """
        rows = self._child_issue_rows(issue_id)
        if not rows:
            return False
        if not all(str(row["status"]) == "done" for row in rows):
            return False
        reviewer_roles = {"reviewer", "code_reviewer"}
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

        # ── Workspace verification ────────────────────────────────────────────
        # The Lead verifies that the workspace actually contains real files —
        # not just that the team reported they created them.  This prevents
        # closing a cycle where the engineer delivered stubs or placeholders.
        workspace_warnings = self._workspace_verification_lines()

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

        if workspace_warnings:
            parts.append("")
            parts.append("**Verificación del workspace (Lead):**")
            for w in workspace_warnings:
                parts.append(f"  {w}")

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

    def _attempt_adapter_recovery(
        self,
        *,
        issue_id: str,
        agent_id: str,
        run_id: str,
        failed_adapter_type: str,
        agent_role: str,
        liveness_reason: str,
    ) -> bool:
        """RUN-003 recovery: swap the blocked agent to another connected adapter.

        When an issue blocks after exhausting continuations without workspace
        evidence, the diagnosis is correct but nothing repairs it. If the
        project has a *different* connected adapter available for the role,
        switch the agent to it, reopen the issue and wake the agent — once per
        issue (audited via ``issue.adapter_recovery`` in the activity log).
        """
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE action = 'issue.adapter_recovery' AND target_id = ?",
                (issue_id,),
            ).fetchone()
        if row and int(row[0]) > 0:
            return False

        profiles = project_profiles(Path(self.db_path).parent)
        candidates = [
            p for p in profiles
            if str(p.get("adapter_type") or "") != failed_adapter_type and profile_is_connected(p)
        ]
        if not candidates:
            return False
        selection = choose_adapter_for_role(agent_role, None, candidates)
        if not selection or str(selection.get("adapter_type") or "") == failed_adapter_type:
            return False

        new_adapter_type = str(selection["adapter_type"])
        with contextlib.closing(_connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE agents SET adapter_type = ?, adapter_config_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (
                    new_adapter_type,
                    json.dumps(selection.get("adapter_config") or {}, ensure_ascii=False, sort_keys=True),
                    agent_id,
                ),
            )
        update_issue(self.db_path, issue_id=issue_id, status="todo")
        log_activity(
            self.db_path,
            action="issue.adapter_recovery",
            target_type="issue",
            target_id=issue_id,
            actor_agent_id=agent_id,
            run_id=run_id,
            payload={
                "failed_adapter_type": failed_adapter_type,
                "new_adapter_type": new_adapter_type,
                "new_adapter_profile_id": selection.get("adapter_profile_id"),
                "liveness_reason": liveness_reason,
            },
        )
        try:
            create_comment(
                self.db_path,
                issue_id=issue_id,
                author_agent_id=None,
                body=(
                    f"⚙ Sistema: el adapter `{failed_adapter_type}` agotó sus intentos sin "
                    f"producir cambios verificables ({liveness_reason}). Reasignado a "
                    f"`{new_adapter_type}` ({selection.get('adapter_profile_id')}) y la issue "
                    "vuelve a `todo` para un intento con el canal nuevo."
                ),
                metadata={"source": "adapter_recovery"},
            )
        except Exception:
            logger.warning("adapter_recovery: failed to post system comment", exc_info=True)
        enqueue_wakeup(
            self.db_path,
            agent_id=agent_id,
            source="adapter_recovery",
            reason="assignment",
            trigger_detail=f"adapter_recovery:{failed_adapter_type}->{new_adapter_type}",
            payload={"issue_id": issue_id, "wake_reason": "assignment"},
            idempotency_key=f"adapter_recovery:{issue_id}",
        )
        logger.info(
            "adapter_recovery: issue %s reassigned %s -> %s after %s",
            issue_id, failed_adapter_type, new_adapter_type, liveness_reason,
        )
        return True

    # ── Issue state machine per role ─────────────────────────────────────────
    # Target statuses a worker (non-lead) may set on its OWN issue. `todo` and
    # `backlog` are excluded: a worker re-queueing itself is loop fuel — only
    # the Lead re-queues work. `cancelled` stays allowed because liveness
    # honours it as a deliberate terminal declaration (rule 6).
    _WORKER_ALLOWED_TARGET_STATUSES = frozenset({"in_progress", "in_review", "done", "blocked", "cancelled"})
    _TERMINAL_ISSUE_STATUSES = frozenset({"done", "cancelled"})
    _LEAD_TIER_ROLES = frozenset({"lead", "team_lead", "lead_executor"})

    def _issue_status_transition_denied(
        self, *, issue_id: str, agent_role: str, new_status: str
    ) -> str | None:
        """Return a denial reason when *agent_role* may not set *new_status*.

        None means the transition is allowed. Lead-tier roles keep full
        authority; system paths (reconcilers, breakers, reopen-for-chat) call
        update_issue directly and are not gated here.
        """
        role_key = str(agent_role or "").strip().lower()
        if role_key in self._LEAD_TIER_ROLES:
            return None
        target = str(new_status or "").strip().lower()
        if target not in self._WORKER_ALLOWED_TARGET_STATUSES:
            return "target_status_not_allowed_for_role"
        try:
            issue = get_issue(self.db_path, issue_id=issue_id)
        except Exception:
            return None  # can't verify — don't block on read failure
        current = str((issue or {}).get("status") or "").strip().lower()
        if current in self._TERMINAL_ISSUE_STATUSES and target != current:
            return "cannot_reopen_terminal_issue"
        return None

    _CHURN_ROLES = frozenset({"engineer", "software_engineer", "reviewer", "code_reviewer"})
    _CHURN_WINDOW_HOURS = 6

    def _delegation_churn_blocked(
        self,
        *,
        parent_issue_id: str,
        role_for_issue: str,
        agent_id: str,
        run_id: str,
        title: str,
    ) -> bool:
        """Return True when creating another same-role child would feed a loop.

        Counts same-role siblings (any status — cancelled ones are precisely
        the churn signature) created under *parent_issue_id* within the last
        _CHURN_WINDOW_HOURS and after the user's last breaker decision. At the
        limit, creation is blocked and ONE idempotent escalation interaction is
        raised: accept = allow another round; reject = keep blocked for the
        rest of the window.
        """
        limit = _delegation_churn_limit()
        role_key = str(role_for_issue or "").strip().lower()
        if limit <= 0 or role_key not in self._CHURN_ROLES or not parent_issue_id:
            return False
        with contextlib.closing(_connect(self.db_path)) as conn:
            breaker = conn.execute(
                """
                SELECT id, status, resolved_at, created_at
                FROM issue_thread_interactions
                WHERE issue_id = ?
                  AND idempotency_key LIKE 'delegation_churn:%'
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (parent_issue_id,),
            ).fetchone()
            if breaker and str(breaker["status"]) not in {"accepted", "rejected", "cancelled", "expired", "answered"}:
                blocked = True  # pending — the user has not decided yet
                epoch = None
            else:
                epoch = str(breaker["resolved_at"]) if breaker and breaker["resolved_at"] else None
                if breaker and str(breaker["status"]) == "rejected":
                    recent = conn.execute(
                        "SELECT 1 FROM issue_thread_interactions WHERE id = ? AND resolved_at >= datetime('now', ?)",
                        (str(breaker["id"]), f"-{self._CHURN_WINDOW_HOURS} hours"),
                    ).fetchone()
                    if recent:
                        self._log_churn_block(parent_issue_id, agent_id, run_id, role_key, title, reason="user_rejected")
                        return True
                params: list[Any] = [parent_issue_id, role_key, f"-{self._CHURN_WINDOW_HOURS} hours"]
                epoch_filter = ""
                if epoch:
                    epoch_filter = "AND created_at > ?"
                    params.append(epoch)
                count_row = conn.execute(
                    f"""
                    SELECT COUNT(*) FROM issues
                    WHERE parent_id = ?
                      AND LOWER(role) = ?
                      AND created_at >= datetime('now', ?)
                      {epoch_filter}
                    """,
                    params,
                ).fetchone()
                blocked = int(count_row[0] or 0) >= limit
            if not blocked:
                return False
        try:
            create_interaction(
                self.db_path,
                issue_id=parent_issue_id,
                kind="request_confirmation",
                payload={
                    "version": 1,
                    "reason": "delegation_churn_limit",
                    "parent_issue_id": parent_issue_id,
                    "role": role_key,
                    "limit": limit,
                    "window_hours": self._CHURN_WINDOW_HOURS,
                },
                continuation_policy="wake_assignee",
                idempotency_key=f"delegation_churn:{parent_issue_id}:{epoch or 'genesis'}",
                source_run_id=run_id,
                created_by_agent_id=agent_id,
                title=f"Freno de delegación — {limit}+ issues de {role_key} bajo la misma tarea",
                summary=(
                    f"El Lead lleva {limit} o más issues de rol `{role_key}` creadas bajo la misma "
                    f"tarea en {self._CHURN_WINDOW_HOURS}h — patrón de bucle corrección↔revisión. "
                    "Acepta para permitir otra ronda de intentos. Rechaza para parar la creación "
                    "automática y decidir tú el siguiente paso."
                ),
            )
        except Exception:
            logger.warning("delegation churn escalation failed for %s", parent_issue_id, exc_info=True)
        self._log_churn_block(parent_issue_id, agent_id, run_id, role_key, title, reason="limit_reached")
        return True

    def _log_churn_block(
        self, parent_issue_id: str, agent_id: str, run_id: str, role_key: str, title: str, *, reason: str
    ) -> None:
        logger.warning(
            "delegation.churn_blocked (%s): parent=%s role=%s title=%r",
            reason, parent_issue_id, role_key, title,
        )
        try:
            log_activity(
                self.db_path,
                action="delegation.churn_blocked",
                target_type="issue",
                target_id=parent_issue_id,
                actor_agent_id=agent_id,
                run_id=run_id,
                payload={"role": role_key, "title": title, "reason": reason},
            )
        except Exception:
            logger.warning("churn block activity failed for %s", parent_issue_id, exc_info=True)

    def _root_issue_id(self, issue_id: str) -> str:
        current = str(issue_id)
        for _ in range(10):
            issue = get_issue(self.db_path, issue_id=current)
            parent = str((issue or {}).get("parent_id") or "").strip()
            if not parent:
                return current
            current = parent
        return current

    def _check_cost_breaker(self, *, issue_id: str, run_id: str, agent_id: str) -> None:
        """Escalate when a subtree accumulates spend without workspace progress.

        The epoch restarts at the last run that materialized files (file_ops /
        workspace_evidence with changes) or at the last resolved breaker
        interaction. Accept = keep going (resets the counter); reject = cancel
        the subtree's open children (applied here, deterministically, on the
        next run after the rejection).
        """
        threshold = _cost_breaker_threshold_cents()
        if threshold <= 0:
            return
        root_id = self._root_issue_id(issue_id)
        with contextlib.closing(_connect(self.db_path)) as conn:
            subtree_ids = [root_id] + [
                str(row["id"])
                for row in conn.execute("SELECT id FROM issues WHERE parent_id = ?", (root_id,)).fetchall()
            ]
            placeholders = ", ".join("?" for _ in subtree_ids)
            breaker = conn.execute(
                """
                SELECT id, status, resolved_at, created_at
                FROM issue_thread_interactions
                WHERE issue_id = ?
                  AND idempotency_key LIKE 'cost_breaker:%'
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (root_id,),
            ).fetchone()
            if breaker and str(breaker["status"]) not in {"accepted", "rejected", "cancelled", "expired", "answered"}:
                return  # pending — the user has not decided yet
            rejected_unapplied = False
            if breaker and str(breaker["status"]) == "rejected":
                applied = conn.execute(
                    "SELECT COUNT(*) FROM activity_log WHERE action = 'cost_breaker.children_cancelled' AND target_id = ?",
                    (str(breaker["id"]),),
                ).fetchone()
                rejected_unapplied = not (applied and int(applied[0]) > 0)
            progress_row = conn.execute(
                f"""
                SELECT MAX(e.created_at) AS at
                FROM run_events e
                JOIN runs r ON r.id = e.run_id
                WHERE r.issue_id IN ({placeholders})
                  AND (
                        e.event_type = 'file_ops'
                     OR (e.event_type = 'workspace_evidence' AND json_extract(e.payload_json, '$.changed') = 1)
                  )
                """,
                subtree_ids,
            ).fetchone()
            epoch_candidates = [
                str(progress_row["at"]) if progress_row and progress_row["at"] else None,
                str(breaker["resolved_at"]) if breaker and breaker["resolved_at"] else None,
            ]
            epoch = max((c for c in epoch_candidates if c), default=None)
            if epoch:
                spend_row = conn.execute(
                    f"SELECT COALESCE(SUM(actual_cost_cents), 0) FROM runs WHERE issue_id IN ({placeholders}) AND created_at > ?",
                    (*subtree_ids, epoch),
                ).fetchone()
            else:
                spend_row = conn.execute(
                    f"SELECT COALESCE(SUM(actual_cost_cents), 0) FROM runs WHERE issue_id IN ({placeholders})",
                    subtree_ids,
                ).fetchone()
            spend = int(spend_row[0] or 0) if spend_row else 0
            open_children = [
                str(row["id"])
                for row in conn.execute(
                    "SELECT id FROM issues WHERE parent_id = ? AND status NOT IN ('done', 'cancelled')",
                    (root_id,),
                ).fetchall()
            ]

        # Rejected breaker: cancel the subtree's open children exactly once.
        if rejected_unapplied and breaker is not None:
            for child_id in open_children:
                try:
                    update_issue(self.db_path, issue_id=child_id, status="cancelled")
                except Exception:
                    logger.warning("cost_breaker: failed to cancel child %s", child_id, exc_info=True)
            log_activity(
                self.db_path,
                action="cost_breaker.children_cancelled",
                target_type="interaction",
                target_id=str(breaker["id"]),
                actor_agent_id=None,
                run_id=run_id,
                payload={"root_issue_id": root_id, "cancelled": open_children},
            )
            return

        if spend < threshold:
            return
        try:
            create_interaction(
                self.db_path,
                issue_id=root_id,
                kind="request_confirmation",
                payload={
                    "version": 1,
                    "reason": "cost_breaker_tripped",
                    "root_issue_id": root_id,
                    "spend_cents": spend,
                    "threshold_cents": threshold,
                    "epoch": epoch,
                },
                continuation_policy="wake_assignee",
                idempotency_key=f"cost_breaker:{root_id}:{epoch or 'genesis'}",
                source_run_id=run_id,
                created_by_agent_id=agent_id,
                title=f"Freno de coste — {spend}¢ gastados sin avance en el workspace",
                summary=(
                    f"El equipo lleva {spend}¢ gastados (umbral {threshold}¢) sin producir "
                    "cambios nuevos en el workspace. Acepta para continuar (el contador se "
                    "reinicia). Rechaza para cancelar las issues abiertas del ciclo y parar el gasto."
                ),
            )
            log_activity(
                self.db_path,
                action="cost_breaker.tripped",
                target_type="issue",
                target_id=root_id,
                actor_agent_id=agent_id,
                run_id=run_id,
                payload={"spend_cents": spend, "threshold_cents": threshold, "epoch": epoch},
            )
            logger.warning(
                "cost_breaker.tripped: issue %s spent %d cents without workspace progress (threshold %d)",
                root_id, spend, threshold,
            )
        except Exception:
            logger.warning("cost_breaker: failed to create escalation for %s", root_id, exc_info=True)

    def _workspace_verification_lines(self) -> list[str]:
        """Scan the workspace for emptiness and stub/placeholder deliverables."""
        warnings: list[str] = []
        try:
            _ws_root = workspace_root_for_db(self.db_path)
            _ws_snap = snapshot_workspace(_ws_root)
            _ws_files = list(_ws_snap.keys()) if isinstance(_ws_snap, dict) else []
            _stub_indicators = ("placeholder", "stub", "todo: replace", "todo: implement", "replace with actual")
            _stub_files: list[str] = []
            for _fpath, _fmeta in (_ws_snap.items() if isinstance(_ws_snap, dict) else {}.items()):
                # Check for stub content in small text files (< 2 KB).
                # snapshot_workspace stores (mtime_ns, size) tuples.
                if isinstance(_fmeta, (tuple, list)) and len(_fmeta) >= 2:
                    _fsize = int(_fmeta[1])
                elif isinstance(_fmeta, dict):
                    _fsize = int(_fmeta.get("size", 0))
                else:
                    _fsize = 0
                if _fsize == 0:
                    _stub_files.append(f"{_fpath} (0 bytes)")
                elif _fsize < 2048:
                    try:
                        _content = (_ws_root / _fpath).read_text(encoding="utf-8", errors="ignore").lower()
                        if any(ind in _content for ind in _stub_indicators):
                            _stub_files.append(f"{_fpath} (stub content detected)")
                    except Exception:
                        pass
            if not _ws_files:
                warnings.append(
                    "⚠ ALERTA CRÍTICA: El workspace está VACÍO — ningún archivo fue entregado. "
                    "El engineer reportó haber creado archivos pero no hay nada en el workspace. "
                    "NO cierres el ciclo hasta que haya entregables reales."
                )
            elif _stub_files:
                warnings.append(
                    f"⚠ ALERTA: {len(_stub_files)} archivo(s) son stubs/vacíos: "
                    + ", ".join(_stub_files[:5])
                    + ". Verifica que los entregables principales no sean placeholders."
                )
            else:
                # Workspace has files — list a short manifest so the user knows what's there
                _visible = sorted(_ws_files)[:10]
                warnings.append(
                    f"Workspace verificado: {len(_ws_files)} archivo(s) presentes. "
                    f"Muestra: {', '.join(_visible)}"
                    + (" [+ más]" if len(_ws_files) > 10 else "")
                )
        except Exception:
            logger.warning("workspace verification scan failed", exc_info=True)
        return warnings

    def _machine_close_verification(self, issue_id: str) -> str:
        """Verification block appended to cycle-close proposals, computed from
        structured child reports and a workspace scan — never from the Lead's
        narration, so an LLM Lead cannot whitewash the reviewer's findings.
        """
        lines: list[str] = []
        try:
            rows = self._child_issue_rows(issue_id)
        except Exception:
            rows = []
        reviewer_roles = {"reviewer", "code_reviewer"}
        verdicts: list[str] = []
        for row in rows:
            if str(row.get("role") or "").strip().lower() in reviewer_roles:
                report = row.get("last_agent_report") or {}
                if report:
                    verdicts.append(str(report.get("result") or "").strip() or "sin veredicto")
        lines.append(
            "Veredicto del reviewer (report estructurado): "
            + (", ".join(verdicts) if verdicts else "sin report estructurado")
        )
        # Acceptance-criteria coverage: explicit done-bar vs assignee evidence.
        for row in rows:
            meta = _decode_json(row.get("metadata_json") or "{}")
            criteria = [str(c) for c in (meta.get("acceptance_criteria") or []) if str(c).strip()]
            if not criteria:
                continue
            report = row.get("last_agent_report") or {}
            evidence = str(report.get("evidence") or "").strip()
            lines.append(
                f"Criterios de aceptación «{str(row.get('title') or '')[:40]}»: {len(criteria)} definidos — "
                + (f"evidencia del assignee: {evidence[:120]}" if evidence else "SIN evidencia reportada")
            )
        lines.extend(self._workspace_verification_lines())
        cost_line = self._cycle_cost_line(issue_id)
        if cost_line:
            lines.append(cost_line)
        return "\n".join(f"- {line}" for line in lines)

    def _cycle_cost_line(self, issue_id: str) -> str | None:
        """Real spend and estimated savings for the issue's subtree runs."""
        try:
            with contextlib.closing(_connect(self.db_path)) as conn:
                row = conn.execute(
                    """
                    SELECT COALESCE(SUM(actual_cost_cents), 0) AS cost,
                           COALESCE(SUM(estimated_savings_cents), 0) AS savings
                    FROM runs
                    WHERE issue_id = ?
                       OR issue_id IN (SELECT id FROM issues WHERE parent_id = ?)
                    """,
                    (issue_id, issue_id),
                ).fetchone()
        except Exception:
            return None
        if row is None:
            return None
        cost = int(row["cost"] or 0)
        savings = int(row["savings"] or 0)
        if cost == 0 and savings == 0:
            return None
        return f"Coste del ciclo: {cost}¢ · ahorro estimado {savings}¢ vs modelos premium"

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

    # Char threshold for auto-spawning a context_curator child.  When the
    # unsynthesized portion of the parent issue thread exceeds this many characters
    # (≈ 2 000 tokens) and no plan document exists, the Lead silently creates a
    # context_curator to synthesise the next block before the next delegation round.
    _CONTEXT_CURATOR_CHAR_THRESHOLD: int = 8_000

    def _maybe_spawn_context_curator(
        self, issue_id: str, agent_id: str, run: dict[str, Any]
    ) -> None:
        """Silently spawn a context_curator child when unsynthesized thread content grows large.

        Triggered in the ``child_report`` branch when ALL conditions hold:

        1. Unsynthesized char count ≥ ``_CONTEXT_CURATOR_CHAR_THRESHOLD`` (8 000).
           "Unsynthesized" means comments after ``synthesized_through_comment_id``
           stored in the ``context_summary`` document, or all comments if no
           synthesis has happened yet.
        2. No ``plan`` document exists for the issue (curator adds the most value
           before a plan is written; once a plan exists the thread is summarised).
        3. No **active** (todo/in_progress/blocked) context_curator child exists.
           - Done: the previous block is complete; a new block MAY be spawned when
             new content accumulates past the threshold (this is the key difference
             from the old comment-count model — done does NOT block re-spawn).
           - Cancelled: the curator was abandoned; a fresh one is always allowed.

        The spawned curator receives a description that tells it exactly which
        comment to start from (``Synthesize from: comment:<id>`` or ``all``).

        The method never raises — any failure is logged and silently swallowed so
        the calling ``child_report`` path continues unaffected.
        """
        try:
            # ── 1. Read context_summary to get synthesized_through_comment_id ──────
            summary_data = get_context_summary(self.db_path, issue_id=issue_id)
            synthesized_through_id: str | None = None
            if summary_data:
                synthesized_through_id = summary_data.get("synthesized_through_comment_id")

            # ── 2. Calculate unsynthesized char count ─────────────────────────────
            with contextlib.closing(_connect(self.db_path)) as conn:
                if synthesized_through_id is None:
                    # No prior synthesis — count all comments
                    unsynthesized_chars: int = conn.execute(
                        "SELECT COALESCE(SUM(LENGTH(body)), 0)"
                        " FROM issue_comments WHERE issue_id = ?",
                        (issue_id,),
                    ).fetchone()[0]
                    first_row = conn.execute(
                        "SELECT id FROM issue_comments WHERE issue_id = ?"
                        " ORDER BY rowid ASC LIMIT 1",
                        (issue_id,),
                    ).fetchone()
                    from_comment_id: str | None = first_row[0] if first_row else None
                else:
                    synth_row = conn.execute(
                        "SELECT rowid FROM issue_comments WHERE id = ?",
                        (synthesized_through_id,),
                    ).fetchone()
                    if synth_row is None:
                        # synthesized_through ID no longer resolvable — count all
                        unsynthesized_chars = conn.execute(
                            "SELECT COALESCE(SUM(LENGTH(body)), 0)"
                            " FROM issue_comments WHERE issue_id = ?",
                            (issue_id,),
                        ).fetchone()[0]
                        from_comment_id = None
                    else:
                        synth_rowid: int = synth_row[0]
                        unsynthesized_chars = conn.execute(
                            "SELECT COALESCE(SUM(LENGTH(body)), 0)"
                            " FROM issue_comments WHERE issue_id = ? AND rowid > ?",
                            (issue_id, synth_rowid),
                        ).fetchone()[0]
                        next_row = conn.execute(
                            "SELECT id FROM issue_comments"
                            " WHERE issue_id = ? AND rowid > ? ORDER BY rowid ASC LIMIT 1",
                            (issue_id, synth_rowid),
                        ).fetchone()
                        from_comment_id = next_row[0] if next_row else None

            # ── 3. Check threshold ────────────────────────────────────────────────
            if unsynthesized_chars < self._CONTEXT_CURATOR_CHAR_THRESHOLD:
                return

            # ── 4. Plan doc check (curator most useful before a plan exists) ──────
            if get_document(self.db_path, issue_id=issue_id, key="plan") is not None:
                return

            # ── 5. Idempotency: only ACTIVE curators block a new spawn ────────────
            with contextlib.closing(_connect(self.db_path)) as conn:
                existing_active_curator = conn.execute(
                    """
                    SELECT id FROM issues
                    WHERE parent_id = ? AND lower(role) = 'context_curator'
                      AND status IN ('todo', 'in_progress', 'blocked')
                    LIMIT 1
                    """,
                    (issue_id,),
                ).fetchone()
            if existing_active_curator is not None:
                return

            # ── 6. Spawn with description anchored to the first unsynthesized comment ─
            from_label = f"comment:{from_comment_id}" if from_comment_id else "all"
            logger.info(
                "Spawning context_curator for issue %s"
                " (unsynthesized_chars=%d, threshold=%d, from=%s)",
                issue_id, unsynthesized_chars, self._CONTEXT_CURATOR_CHAR_THRESHOLD, from_label,
            )
            self._create_delegated_issue(
                issue_id=issue_id,
                agent_id=agent_id,
                run=run,
                spec={
                    "title": "Context curator — synthesize thread block",
                    "description": (
                        f"Target issue: {issue_id}\n"
                        f"Synthesize from: {from_label}\n\n"
                        "El hilo de la issue padre ha acumulado suficiente contenido sin sintetizar. "
                        "Sigue tu protocolo de context_curator:\n"
                        "1. Lee los comentarios del Target issue desde el marcador 'Synthesize from'.\n"
                        "2. Produce un bloque de síntesis y llámalo mediante "
                        "POST /api/issues/{target_id}/context-summary/blocks.\n"
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
                       i.metadata_json,
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
            # Prefer the validated, provenance-checked report; fall back to
            # parsing the last comment only for legacy data recorded before
            # the agent_reports table existed.
            d["last_agent_report"] = (
                latest_agent_report(self.db_path, issue_id=str(d.get("id") or ""))
                or _parse_agent_report(str(d.get("last_comment_body") or ""))
            )
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


def _workspace_file_priority(rel: Path) -> int:
    """Ordering key so review-relevant files get their content within budget.

    A reviewer's first question is usually "does the deliverable + its docs
    exist?" — so READMEs, docs, manifests and scene files must never be
    starved by alphabetical ordering (e.g. README.md sorting after a large
    Assets/ tree).
    """
    name = rel.name.lower()
    suffix = rel.suffix.lower()
    if name.startswith("readme"):
        return 0
    if suffix in {".md", ".txt", ".rst"}:
        return 1
    if "manifest" in name or "projectversion" in name or name in {"package.json", "pyproject.toml"}:
        return 2
    if suffix in {".unity", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini"}:
        return 3
    return 4


def _read_workspace_files(
    workspace_root: Path,
    *,
    max_per_file_bytes: int = 8192,
    max_total_bytes: int = 32768,
) -> list[dict[str, Any]]:
    """Return ``{path, size_bytes, content?}`` for every workspace file.

    Injected into the wake payload for reviewer/QA/scout runs so they review
    real files instead of hallucinating from the engineer's description.

    Every non-binary, non-hidden file ALWAYS appears in the result (path +
    size) so existence questions ("is there a README?") are answerable. File
    *content* is included in priority order (READMEs, docs, manifests, scenes,
    then sources) until *max_total_bytes* is reached; files past the budget
    still appear with a "content omitted" marker rather than being dropped.
    Dropping them (the old behaviour) made alphabetically-late files like
    README.md invisible, which trapped reviewer↔engineer in an endless
    "README missing" fix loop.
    """
    workspace_root = workspace_root.resolve()

    try:
        all_files = list(p for p in workspace_root.rglob("*") if p.is_file())
    except Exception:
        return []

    candidates: list[tuple[Path, bytes]] = []
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
            raw = entry.read_bytes()
        except Exception:
            continue
        # Heuristic binary check: > 15% non-text bytes in first 512
        sample = raw[:512]
        if sample:
            non_text = sum(1 for b in sample if b < 9 or (13 < b < 32) or b == 127)
            if non_text / len(sample) > 0.15:
                continue
        candidates.append((rel, raw))

    # Priority first (so key docs get content), then alphabetical within a tier.
    candidates.sort(key=lambda item: (_workspace_file_priority(item[0]), str(item[0])))

    result: list[dict[str, Any]] = []
    total_bytes = 0
    for rel, raw in candidates:
        path_str = str(rel).replace("\\", "/")
        item: dict[str, Any] = {"path": path_str, "size_bytes": len(raw)}
        if total_bytes < max_total_bytes:
            truncated = len(raw) > max_per_file_bytes
            content = raw[:max_per_file_bytes].decode("utf-8", errors="replace")
            if truncated:
                content += f"\n… [truncated — {len(raw)} bytes total]"
            item["content"] = content
            total_bytes += len(content.encode("utf-8", errors="replace"))
        else:
            item["content"] = "[content omitted — payload budget reached; file exists on disk]"
        result.append(item)

    # Present chronologically-stable path order for the consumer.
    result.sort(key=lambda it: it["path"])
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


# AI Teams must never create (or touch) provider-convention instruction files
# inside managed projects — the correct namespace is .aiteam/instructions.md.
# See docs/NAMING_COLLISION_INVESTIGATION.md.
_FORBIDDEN_FILE_BASENAMES = frozenset({
    "agents.md", "claude.md", "gemini.md", "codex.md", "copilot.md",
})


def _execute_file_ops(
    file_ops: list[dict[str, Any]],
    workspace_root: Path,
) -> list[str]:
    """Materialize write_file / append_file / delete_file ops on disk.

    Paths are resolved relative to *workspace_root*.  Any op that would
    escape the workspace (path traversal) is silently skipped, as is any op
    targeting a provider-convention filename (AGENTS.md, CLAUDE.md, …).
    Returns a list of relative paths that were actually touched.

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

        # Truly absolute paths (drive-prefixed / UNC / POSIX-absolute): agents
        # sometimes emit the full workspace path ("C:\Users\...\proyecto\x.md").
        # Stripping just the drive letter used to re-root it as a RELATIVE
        # path, silently creating a nested "Users/.../proyecto/" tree inside
        # the workspace. Resolve them properly: inside the workspace → use the
        # true relative part; outside → skip.
        # A bare leading "/" or "\" (no drive) is LLM shorthand for the
        # workspace root — keep treating that as relative.
        is_drive_prefixed = len(rel_path) >= 2 and rel_path[1] == ":"
        is_unc = rel_path.startswith(("\\\\", "//"))
        is_posix_absolute = os.name != "nt" and rel_path.startswith("/")
        if is_drive_prefixed or is_unc or is_posix_absolute:
            try:
                resolved_abs = Path(rel_path).resolve()
                rel_path = str(resolved_abs.relative_to(workspace_root))
            except (ValueError, OSError):
                if is_posix_absolute:
                    # Ambiguous: a bare "/README.md" is usually LLM shorthand
                    # for the workspace root — fall back to relative.
                    rel_path = rel_path.lstrip("/")
                else:
                    logger.warning("file_op skipped — absolute path outside workspace: %s", rel_path)
                    continue
        else:
            rel_path = rel_path.lstrip("/\\")

        target = (workspace_root / rel_path).resolve()

        # Security: ensure target stays inside workspace_root
        try:
            target.relative_to(workspace_root)
        except ValueError:
            logger.warning("file_op skipped — path escapes workspace: %s", rel_path)
            continue

        # Naming-collision guard: provider-convention instruction files are
        # off-limits in managed projects; persistent instructions belong in
        # .aiteam/instructions.md.
        if target.name.lower() in _FORBIDDEN_FILE_BASENAMES:
            logger.warning(
                "file_op rejected — provider-convention filename %r is forbidden; "
                "use .aiteam/instructions.md instead",
                rel_path,
            )
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
