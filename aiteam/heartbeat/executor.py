from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

from aiteam.adapters.registry import AdapterRegistry, ExecutionResult
from aiteam.adapters.work_contract import filter_forbidden_ops_for_role
from aiteam.db.agents import create_agent
from aiteam.db.activity_log import log_activity
from aiteam.db.comments import create_comment
from aiteam.db.dependencies import resolve_blocker_wakeups, sync_default_child_dependencies
from aiteam.db.documents import DocumentConflict, append_summary_block, get_document, get_context_summary, put_document
from aiteam.db.wake_payload import build_context_curation_target
from aiteam.db.issues import create_issue
from aiteam.db.finops import BudgetStatus, check_budget, current_period, record_cost
from aiteam.db.interactions import create_interaction, get_interaction, list_interactions
from aiteam.db.issues import get_issue, update_issue
from aiteam.db.runs import append_run_event, finish_run, mark_run_running
from aiteam.db.tool_access import record_tool_access
from aiteam.db.agent_reports import latest_agent_report, record_agent_report
from aiteam.db.quorum_sessions import (
    accept_quorum_synthesis,
    create_quorum_session,
    degrade_quorum_session,
    evaluate_quorum_session,
    get_quorum_session,
    quorum_synthesis_context,
    record_quorum_contribution,
)
from aiteam.db.wake_payload import build_wake_payload, project_open_issues, _parse_agent_report
from aiteam.db.wakeups import enqueue_wakeup, finish_wakeup
from aiteam.heartbeat.scheduler import DispatchResult
from aiteam.lead_intake import apply_accepted_team_proposal, build_team_proposal, format_team_proposal
from aiteam.hiring_economics import log_hiring_decision
from aiteam.project_adapters import choose_adapter_for_role, project_profiles, reconcile_project_agent_policy
from aiteam.provider_governor import GOVERNOR
from aiteam.quorum_quality import (
    QUORUM_AUDIT_MARKER,
    evaluate_plan_depth,
    parse_quorum_audit,
    plan_contract_instruction,
    quorum_audit_contract_instruction,
    validate_quorum_audit,
)
from aiteam.run_liveness import (
    MAX_CONTINUATION_ATTEMPTS,
    LivenessResult,
    RunEvidence,
    _BUILTIN_ADAPTERS,
    classify_run_liveness,
    collect_run_evidence,
)
from aiteam.run_profiles import FULL_TEAM, LEAD_QUORUM, SOLO_LEAD, normalize_run_profile
from aiteam.skills import compose_skill
from aiteam.tools.catalog import check_capability, default_capabilities_for_role, get_agent_capabilities
from aiteam.adapters.subscription_cli_adapter import ClaudeSubscriptionCliRuntime
from aiteam.user_config import (
    inject_adapter_secrets,
    load_adapter_profiles,
    profile_is_connected,
    resolve_adapter_config,
)
from aiteam.workspace_evidence import WorkspaceDelta, diff_snapshots, snapshot_workspace, workspace_root_for_db


_TERMINAL_EXEC_STATUSES = {"completed", "failed", "skipped"}

# Role/flow policies (tiers, ops matrix, breakers, ventanas) viven en
# aiteam.policies (fase 5) — alias locales para los call sites existentes.
from aiteam.policies import (  # noqa: E402
    EXTENSION_PROPOSAL_REASON,
    LEAD_TIER_ROLES as _LEAD_TIER_ROLES_P,
    LLM_ADAPTER_TYPES as _LLM_ADAPTER_TYPES,
    NON_EDITING_ROLES as _NON_EDITING_ROLES,
    QUORUM_MAX_SYNTHESIS_ATTEMPTS as _QUORUM_MAX_SYNTHESIS_ATTEMPTS,
    RUNTIME_VERIFICATION_WAIVER_REASON as _RUNTIME_VERIFICATION_WAIVER_REASON,
    TERMINAL_ISSUE_STATUSES as _TERMINAL_ISSUE_STATUSES_P,
    WORKSPACE_NOISE_DIRS as _SKIP_DIRS,
    cost_breaker_threshold_cents as _cost_breaker_threshold_cents,
    daily_cost_cap_cents as _daily_cost_cap_cents,
    delegation_churn_limit as _delegation_churn_limit,
    operational_interaction_default as _operational_interaction_default,
    rereview_limit as _rereview_limit,
    workspace_file_max_bytes as _ws_file_max_bytes,
    workspace_files_budget_bytes as _ws_files_budget_bytes,
)


def _is_rate_limit_error(error: str | None) -> bool:
    text = str(error or "").lower()
    return "429" in text or "rate limit" in text or "rate_limit" in text


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
    quorum_block_start = text.rfind(QUORUM_AUDIT_MARKER)
    if quorum_block_start >= 0 and (block_start < 0 or quorum_block_start < block_start):
        block_start = quorum_block_start
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

        _run_issue = get_issue(self.db_path, issue_id=str(run.get("issue_id") or "")) or {}
        _run_issue_metadata = _decode_json(_run_issue.get("metadata_json") or "{}")
        _active_run_profile = normalize_run_profile(_run_issue_metadata.get("profile") or "")
        try:
            reconcile_project_agent_policy(
                self.db_path,
                include_tier3=_active_run_profile != SOLO_LEAD,
            )
        except Exception:
            logger.warning("reconcile_project_agent_policy failed for db %s", self.db_path, exc_info=True)
        agent_info = self._agent_info(agent_id)
        adapter_type = (agent_info.get("adapter_type") if agent_info else None) or "manual"
        agent_role = (agent_info.get("role") if agent_info else None) or ""
        _solo_direct_lead = (
            _active_run_profile == SOLO_LEAD
            and str(agent_role or "").strip().lower() in {"lead", "team_lead"}
        )
        # ── Review cross-provider vinculante (criticidad alta) ───────────────
        # Antes de resolver el runtime: si este run es de un reviewer sobre una
        # issue high/critical y su proveedor coincide con el del engineer del
        # subtree, re-apuntarlo a otro proveedor conectado — el sesgo de
        # auto-preferencia del juez no se mitiga con una nota, se mitiga con
        # otra familia de modelo.
        try:
            if self._enforce_cross_provider_review(
                issue_id=str(run.get("issue_id") or ""), agent_id=agent_id, agent_role=agent_role
            ):
                agent_info = self._agent_info(agent_id)
                adapter_type = (agent_info.get("adapter_type") if agent_info else None) or "manual"
        except Exception:
            logger.warning("cross-provider review enforcement failed for run %s", run_id, exc_info=True)
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
                if agent_role.lower() in _NON_EDITING_ROLES and not _solo_direct_lead:
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
        skill_content = compose_skill(agent_role, self.db_path.parent) if agent_role else None

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
                # Quorum auditors review the immutable Plan A, not workspace
                # files or sibling implementation. Do not expose other auditor
                # answers here: first-pass independence is contractual.
                _audited_issue = get_issue(self.db_path, issue_id=issue_id_str) or {}
                _audited_meta = _decode_json(_audited_issue.get("metadata_json") or "{}")
                if (
                    agent_role in {"lead", "team_lead"}
                    and normalize_run_profile(_audited_meta.get("profile") or "") == LEAD_QUORUM
                ):
                    with contextlib.closing(_connect(self.db_path)) as _conn:
                        _has_quorum = _conn.execute(
                            "SELECT 1 FROM quorum_sessions WHERE issue_id=? LIMIT 1",
                            (issue_id_str,),
                        ).fetchone()
                    if _has_quorum is None:
                        _quorum_instruction = (
                            "⚠ CONTRATO LEAD_QUORUM OBLIGATORIO: esta run debe emitir update_plan "
                            "con el Plan A completo. NO emitas set_status:done, NO pidas dispensar "
                            "tests y NO crees issues de implementación. Al persistirse Plan A, el "
                            "control plane creará automáticamente auditorías independientes.\n"
                            + plan_contract_instruction()
                        )
                        existing_instruction = str(payload.get("mandatory_instruction") or "").strip()
                        payload["mandatory_instruction"] = (
                            _quorum_instruction
                            + (f"\n\n{existing_instruction}" if existing_instruction else "")
                        )
                _audit_session_id = str(_audited_meta.get("quorum_session_id") or "").strip()
                if _audit_session_id:
                    with contextlib.closing(_connect(self.db_path)) as _conn:
                        _base = _conn.execute(
                            """
                            SELECT qs.base_plan_revision_id, r.title, r.body, r.revision_number,
                                   parent.title AS objective_title,
                                   parent.description AS objective_description,
                                   parent.metadata_json AS objective_metadata_json
                            FROM quorum_sessions qs
                            JOIN issue_document_revisions r ON r.id=qs.base_plan_revision_id
                            JOIN issues parent ON parent.id=qs.issue_id
                            WHERE qs.id=?
                            """,
                            (_audit_session_id,),
                        ).fetchone()
                    if _base is not None:
                        payload["quorum_review"] = {
                            "session_id": _audit_session_id,
                            "base_plan_revision_id": _base["base_plan_revision_id"],
                            "plan": {
                                "title": _base["title"],
                                "body": _base["body"],
                                "revision_number": _base["revision_number"],
                            },
                            "objective": (
                                _decode_json(_base["objective_metadata_json"] or "{}").get(
                                    "quorum_objective_snapshot"
                                )
                                or {
                                    "title": _base["objective_title"],
                                    "description": _base["objective_description"],
                                }
                            ),
                            "instruction": (
                                "Revisa exclusivamente esta revisión inmutable del plan. "
                                "No exijas workspace_files ni implementación. "
                                "El Lead es el owner real: argumenta con profundidad y entrégale tu informe.\n"
                                + quorum_audit_contract_instruction()
                            ),
                        }
                    if str(ctx.get("wake_reason") or "") == "quorum_report_retry":
                        correction = (
                            "CORRECCIÓN OBLIGATORIA: tu run anterior terminó sin un AGENT-REPORT "
                            "estructurado y por tanto no cuenta para el quorum. Debes usar `add_comment` "
                            "e incluir al final exactamente un bloque `---AGENT-REPORT---` con role, "
                            "result, issue_status, next_owner, blocker y evidence, además del bloque "
                            f"`{QUORUM_AUDIT_MARKER}` completo. No termines solo con summary."
                        )
                        existing_instruction = str(payload.get("mandatory_instruction") or "").strip()
                        payload["mandatory_instruction"] = correction + (
                            f"\n\n{existing_instruction}" if existing_instruction else ""
                        )
                _quorum_session_id = str(ctx.get("quorum_session_id") or "").strip()
                if _quorum_session_id and str(ctx.get("wake_reason") or "") in {
                    "quorum_ready", "quorum_degraded"
                }:
                    try:
                        payload["quorum"] = quorum_synthesis_context(
                            self.db_path, session_id=_quorum_session_id
                        )
                        payload["quorum"]["lead_instruction"] = (
                            "Eres el Lead real y conservas la decisión final. Lee todos los informes, "
                            "contrasta argumentos y crea un Plan B robusto. Para cada finding usa accept, "
                            "qualify o discard con rationale sustantiva; preserva fortalezas y explica "
                            "trade-offs y alternativas. " + plan_contract_instruction(final=True)
                        )
                    except Exception:
                        logger.warning(
                            "failed to build quorum synthesis context for %s",
                            _quorum_session_id,
                            exc_info=True,
                        )
                # ── Workspace files for reviewer/QA (prevents hallucinated reviews) ──
                # API-only reviewers and QA agents cannot read files themselves; inject
                # the actual workspace content into the wake payload so they work with
                # real code rather than hallucinating based on the engineer's comment.
                # Files the issue explicitly mentions (title/description/criteria/
                # recent comments) get content FIRST — a reviewer must never receive
                # the files under review as "content omitted" while unrelated docs
                # consume the budget (the capa-2 lead↔reviewer ping-pong).
                _issue_meta = payload.get("issue") or {}
                _focus_paths = _extract_focus_paths([
                    str(_issue_meta.get("title") or ""),
                    str(_issue_meta.get("description") or ""),
                    *[str(c) for c in (_issue_meta.get("acceptance_criteria") or [])],
                    *[str((c or {}).get("body") or "") for c in (payload.get("comments") or [])[-6:]],
                ])
                if agent_role in _WORKSPACE_READER_ROLES:
                    ws_files = _read_workspace_files(workspace_root, focus_paths=_focus_paths)
                    if ws_files:
                        # Dieta de contexto (P5): workspace idéntico al de la
                        # última run completada de ESTE agente sobre ESTA issue
                        # → solo lista de paths, sin cuerpos.
                        from aiteam.policies import payload_delta_enabled  # noqa: PLC0415
                        _unchanged = False
                        if payload_delta_enabled():
                            try:
                                _unchanged = self._workspace_digest_unchanged(
                                    issue_id=issue_id_str,
                                    agent_id=agent_id,
                                    run_id=run_id,
                                    workspace_root=workspace_root,
                                )
                            except Exception:
                                logger.warning("payload delta check failed", exc_info=True)
                        if _unchanged:
                            payload["workspace_files"] = [
                                {k: v for k, v in item.items() if k != "content"}
                                for item in ws_files
                            ]
                            payload["workspace_files_note"] = (
                                "El workspace NO ha cambiado desde tu última run completada: "
                                "los cuerpos de archivo se omiten para ahorrar contexto (ya los "
                                "viste). La lista de paths sigue completa; si necesitas releer "
                                "un archivo concreto, dilo en tu comentario."
                            )
                        else:
                            payload["workspace_files"] = ws_files
                # ── Global open-issues view for the Lead ──────────────────────
                # The payload is scoped to ONE issue's subtree; with several
                # root issues (each user task can start a new root), a Lead
                # woken on a finished root truthfully answered "no hay issues
                # abiertas" while other trees had live work. Project-level
                # claims need project-level data.
                if agent_role in _LEAD_TIER_ROLES_P:
                    try:
                        payload["project_open_issues"] = project_open_issues(
                            self.db_path, exclude_issue_id=issue_id_str
                        )
                    except Exception:
                        logger.warning("Failed to inject project_open_issues", exc_info=True)
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
                                ws_files = _read_workspace_files(workspace_root, focus_paths=_focus_paths)
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
                                # ── Extension proposal resolution (self-extension PR2) ──
                                # The owner's accept/reject IS the decision — committed
                                # here deterministically, not left to the Lead's LLM to
                                # re-confirm via some other op (that class of bug already
                                # bit us once with a misrouted delegation). Idempotent
                                # (upsert by name), safe if this wake is ever retried.
                                if isinstance(_r_payload, dict) and _r_payload.get("reason") == EXTENSION_PROPOSAL_REASON:
                                    try:
                                        from aiteam.extensions import approve_mcp_server, reject_mcp_server  # noqa: PLC0415
                                        _ext_action = str(ctx.get("action") or "")
                                        if _ext_action == "accept":
                                            approve_mcp_server(
                                                self.db_path.parent,
                                                name=str(_r_payload.get("name") or ""),
                                                source=str(_r_payload.get("source") or ""),
                                                args=_r_payload.get("args") or [],
                                                env_required=_r_payload.get("env_required") or [],
                                                applies_to_roles=_r_payload.get("applies_to_roles") or [],
                                                justification=str(_r_payload.get("justification") or ""),
                                                approved_by="user",
                                            )
                                            log_activity(
                                                self.db_path, action="extension.approved", target_type="issue",
                                                target_id=issue_id_str, run_id=run_id,
                                                payload={"name": _r_payload.get("name"), "source": _r_payload.get("source")},
                                            )
                                        else:
                                            reject_mcp_server(
                                                self.db_path.parent,
                                                name=str(_r_payload.get("name") or ""),
                                                justification=str(_r_payload.get("justification") or ""),
                                            )
                                            log_activity(
                                                self.db_path, action="extension.rejected", target_type="issue",
                                                target_id=issue_id_str, run_id=run_id,
                                                payload={"name": _r_payload.get("name")},
                                            )
                                    except Exception:
                                        logger.warning("Failed to commit extension proposal resolution (int_id=%r)", _r_int_id, exc_info=True)
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

        # ── Hard daily cost cap (project-wide) ─────────────────────────────
        # Independiente del progreso: topa el gasto real por día natural aunque
        # el subtree siga "avanzando". Mitigación del cascade pile-up.
        cap_gate = self._daily_cost_cap_gate(run_id=run_id, issue_id=str(run.get("issue_id") or ""), agent_id=agent_id)
        if cap_gate == "blocked":
            finish_wakeup(
                self.db_path,
                wakeup_id=wakeup_id,
                status="skipped",
                run_id=run_id,
                error="daily_cost_cap_reached",
            )
            return
        if cap_gate == "rejected":
            finish_run(
                self.db_path,
                run_id=run_id,
                status="failed",
                error="daily_cost_cap_rejected",
                error_code="daily_cost_cap_rejected",
            )
            finish_wakeup(
                self.db_path,
                wakeup_id=wakeup_id,
                status="failed",
                run_id=run_id,
                error="daily_cost_cap_rejected",
            )
            return

        # ── Preflight loop guards ──────────────────────────────────────────
        # Silent-dedupe guards first (no escalation cards — nothing for the
        # user to answer), then the re-review cap which escalates:
        #   issue_terminal            — the wake targets a done/cancelled issue
        #                               (dependency fan-out zombies).
        #   awaiting_user_decision    — a PRODUCT decision is pending on this
        #                               subtree's root; burning fix/review runs
        #                               before the user answers only feeds the
        #                               loop the user is being asked about.
        #   rereview_limit_reached    — reviewer churn cap (escalates once).
        #   review_evidence_unchanged — same workspace state as the reviewer's
        #                               last completed run → same verdict; skip.
        skip_reason = self._preflight_skip_reason(
            issue_id=issue_id_str,
            agent_role=agent_role,
            ctx=ctx,
            run_id=run_id,
            agent_id=agent_id,
            workspace_root=workspace_root,
        )
        if skip_reason:
            if skip_reason == "review_evidence_unchanged" and issue_id_str:
                # A Lead may reopen a completed review to request another pass.
                # If preflight proves the workspace is identical, the previous
                # completed verdict still satisfies that issue; leaving it todo
                # creates a stranded root with an empty wakeup queue.
                with contextlib.suppress(Exception):
                    update_issue(self.db_path, issue_id=issue_id_str, status="done")
                    log_activity(
                        self.db_path,
                        action="review.redundant_wake_closed",
                        target_type="issue",
                        target_id=issue_id_str,
                        actor_agent_id=agent_id,
                        run_id=run_id,
                        payload={"reason": skip_reason},
                    )
                    self._enqueue_supervisor_report(
                        issue_id=issue_id_str,
                        reporting_agent_id=agent_id,
                        source_run_id=run_id,
                    )
            finish_run(
                self.db_path,
                run_id=run_id,
                status="skipped",
                error=skip_reason,
                error_code=skip_reason,
            )
            finish_wakeup(
                self.db_path,
                wakeup_id=wakeup_id,
                status="skipped",
                run_id=run_id,
                error=skip_reason,
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
        effective_provider, effective_channel = self._effective_adapter_identity(
            runtime_provider=runtime.descriptor.provider,
            runtime_channel=runtime.descriptor.channel,
            adapter_cfg=adapter_cfg,
        )
        self._record_run_adapter_metadata(
            run_id=run_id,
            adapter_type=runtime.descriptor.adapter_type,
            provider=effective_provider,
            model=effective_model,
            channel=effective_channel,
        )
        # Keep the in-memory run aligned with the durable row.  Downstream
        # provenance writers (notably quorum contributions) run before the DB
        # row is reloaded and must not persist the scheduler's stale NULLs.
        run = {
            **run,
            "adapter_type": runtime.descriptor.adapter_type,
            "provider": effective_provider,
            "model": effective_model,
            "channel": effective_channel,
        }
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
                "channel": effective_channel,
                "provider": effective_provider,
                "model": effective_model,
            },
        )

        env = runtime.build_env(run_id=run_id, wake_context=wake_context)
        if _solo_direct_lead:
            # Durable identity remains role:lead, but the invoked process must
            # receive a pure single-agent contract. Reusing lead.md leaves the
            # model under contradictory manager/delegation instructions.
            env = {
                **env,
                "AITEAM_AGENT_ROLE": "solo_lead",
                "AITEAM_AGENT_SKILL": compose_skill("solo_lead", self.db_path.parent) or "",
            }
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
        if adapter_type in _LLM_ADAPTER_TYPES and str(agent_role or "").strip().lower() != "test_runner":
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
            if str(agent_role or "").strip().lower() == "test_runner":
                # Ejecutar una suite es determinista (subprocess + exit code):
                # un LLM aquí solo añade coste y la posibilidad de alucinar la
                # evidencia que el quality gate exige. El builtin corre SIEMPRE,
                # sea cual sea el adapter contratado para el rol.
                result = self._execute_builtin_test_runner(run=run, agent_id=agent_id)
            elif (
                str(agent_role or "").strip().lower() in {"lead", "team_lead"}
                and str(ctx.get("wake_reason") or "") == "interaction_resolved"
                and str(ctx.get("kind") or "") == "suggest_tasks"
                and str(ctx.get("action") or "") == "accept"
            ):
                # Accepting a persisted proposal is a deterministic control-
                # plane transition.  It must not depend on whether the Lead's
                # adapter is builtin, API or subscription CLI, nor spend a
                # second LLM call merely to replay the user's acceptance.
                result = self._execute_builtin_lead(run=run, agent_id=agent_id, context=ctx)
            elif (
                str(agent_role or "").strip().lower() in {"lead", "team_lead"}
                and str(ctx.get("wake_reason") or "") == "quorum_degraded"
            ):
                # Degradation is a control-plane failure mode, not an open
                # planning question. Escalate identically for every adapter.
                result = self._execute_builtin_lead(run=run, agent_id=agent_id, context=ctx)
            elif adapter_type in _llm_adapters:
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
        if (
            _requested_file_ops
            and str(agent_role or "").strip().lower() in _NON_EDITING_ROLES
            and not _solo_direct_lead
        ):
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

        # ── Recibo VCS: commit automático de los cambios de la run ──────────
        # Solo en repos gestionados por AI Teams (init en el bootstrap del
        # proyecto). El diffstat queda como run_event 'git_commit': evidencia
        # estructurada de QUÉ cambió esta run, y rollback determinista si hizo
        # falta revertirla. Nunca bloquea la run si git falla.
        if workspace_delta.changed:
            try:
                from aiteam.workspace_git import commit_run_snapshot  # noqa: PLC0415
                receipt = commit_run_snapshot(
                    workspace_root,
                    run_id=run_id,
                    agent_id=agent_id,
                    issue_id=str(run.get("issue_id") or "") or None,
                )
                if receipt:
                    append_run_event(
                        self.db_path,
                        run_id=run_id,
                        event_type="git_commit",
                        stream="system",
                        payload=receipt,
                    )
            except Exception:
                logger.warning("git receipt failed for run %s", run_id, exc_info=True)

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
                        _stored_report = record_agent_report(
                            self.db_path,
                            issue_id=issue_id_str,
                            agent_id=agent_id,
                            run_id=run_id,
                            agent_role=agent_role,
                            parsed=_parsed_report,
                        )
                        self._maybe_record_quorum_contribution(
                            issue_id=issue_id_str,
                            agent_id=agent_id,
                            run=run,
                            report=_stored_report,
                            source_body=result.output,
                        )
                    except Exception:
                        logger.warning("agent report persistence failed for run %s", run_id, exc_info=True)

        # ── Step 2: Apply the adapter's own result actions ───────────────────
        self._apply_result_actions(run=run, agent_id=agent_id, agent_role=agent_role, result=result)
        self._ensure_quorum_auditor_continuation(
            issue_id=issue_id_str,
            agent_id=agent_id,
            run_id=run_id,
            run_status=result.status,
        )

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
            # Cascada de calidad en dos peldaños: (1) modelo senior del mismo
            # perfil — más barato de intentar y suele bastar; (2) solo si no
            # hay peldaño 1 disponible, cambio de canal (adapter recovery).
            _escalated = False
            try:
                _escalated = self._attempt_model_escalation(
                    issue_id=issue_id_str,
                    agent_id=agent_id,
                    run_id=run_id,
                    agent_role=agent_role,
                    liveness_reason=str(liveness_result.reason or ""),
                )
            except Exception:
                logger.warning("model escalation failed for issue %s", issue_id_str, exc_info=True)
            if not _escalated:
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
        if (
            workspace_delta.changed
            and str(agent_role or "").strip().lower() in _NON_EDITING_ROLES
            and not _solo_direct_lead
        ):
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
        final_error = result.error or ("agent reported failure" if final_status == "failed" else None)
        final_error_code = result.error_code or ("agent_reported_failure" if final_status == "failed" else None)
        finished = finish_run(
            self.db_path,
            run_id=run_id,
            status=final_status,
            exit_code=result.exit_code,
            error=final_error,
            error_code=final_error_code,
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
        # Registrar el evento también con coste 0 cuando hubo tokens: el canal
        # de suscripción (tarifa plana) consumía cientos de miles de tokens sin
        # dejar rastro en cost_events, así que el resumen de costes y la
        # economía de hiring solo veían el canal API (infraestimación masiva).
        _usage_dict = result.usage if isinstance(result.usage, dict) else {}
        _has_token_usage = any(
            int(_usage_dict.get(key) or 0) > 0
            for key in ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens")
        )
        if int(result.actual_cost_cents or 0) > 0 or _has_token_usage:
            record_cost(
                self.db_path,
                run_id=run_id,
                agent_id=agent_id,
                amount_cents=result.actual_cost_cents,
                metadata={"source": "run_executor", "usage": _usage_dict} if _has_token_usage else {"source": "run_executor"},
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
                            author_user_id="system",
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

        if context.get("wake_reason") == "quorum_degraded":
            session_id = str(context.get("quorum_session_id") or "").strip()
            session = get_quorum_session(self.db_path, session_id=session_id) or {}
            skipped_reason = str(session.get("skipped_reason") or "quorum_gate_unsatisfied")
            return ExecutionResult(
                status="completed",
                output=f"Quorum degradado: {skipped_reason}.",
                actions={
                    "interactions": [
                        {
                            "kind": "request_confirmation",
                            "payload": {
                                "version": 1,
                                "reason": "quorum_degraded",
                                "quorum_session_id": session_id,
                                "skipped_reason": skipped_reason,
                            },
                            "title": "Quorum sin evidencia suficiente",
                            "summary": (
                                f"El quorum no pudo satisfacer el gate ({skipped_reason}). "
                                "Revisa los adapters de auditoría o decide continuar sin quorum."
                            ),
                            "idempotency_key": f"quorum:degraded:{session_id}",
                        }
                    ]
                },
            )

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
            if str(payload.get("profile") or "").strip().lower() == "lead_quorum":
                self._initialize_quorum_session(
                    parent_issue_id=issue_id,
                    proposal=payload,
                    created_issue_ids=outcome["created_issues"],
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
                "El Lead propone un quorum de revisión antes de ejecutar. "
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
                "He recibido la delegación. Antes de implementar necesito concretar el primer entregable "
                "verificable: alcance mínimo, tecnología objetivo y criterio de aceptación. "
                "Puedo absorber la lectura y tareas mecánicas para reservar el contexto del Lead."
            )
        elif role == "reviewer":
            output = (
                f"Reviewer intake para: {title}\n\n"
                "Riesgos principales a vigilar: plan demasiado amplio, dependencias no ordenadas, falta de un "
                "entregable ejecutable, y revisiones que bloqueen sin evidencia. Revisaré decisiones y qué puede "
                "romper la siguiente run."
            )
        elif role == "qa":
            output = (
                f"QA intake para: {title}\n\n"
                "Propongo una verificación ligera: ejercitar el flujo principal del entregable, capturar errores "
                "visibles y registrar evidencia mínima. Sin gates fuertes salvo riesgo alto."
            )
        else:
            output = f"{role or agent_id} intake para: {title}\n\nDelegación recibida y lista para ejecutar."
        return ExecutionResult(
            status="completed",
            output=output,
            actions={"issue_status": "done", "notify_supervisor": True},
        )

    # pytest/npm colorean su salida aunque no haya TTY (FORCE_COLOR, colorama);
    # los escapes acababan dentro de evidence en agent_reports y en la UI.
    _ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

    def _execute_builtin_test_runner(self, *, run: dict[str, Any], agent_id: str) -> ExecutionResult:
        """Ejecuta la suite de tests del workspace de forma determinista.

        Produce el ---AGENT-REPORT--- que ``_test_runner_gate_line`` exige
        (result aprobatorio + "exit 0" en evidence) sin pasar por un LLM: la
        evidencia del gate sale de un exit code real, no de la narración de un
        modelo — y cuesta cero tokens. El fallo de la suite NO es un fallo de
        la run: el runner cumplió informando, la issue queda blocked para que
        el Lead re-delegue el arreglo.
        """
        issue_id = str(run.get("issue_id") or "")
        root = workspace_root_for_db(self.db_path)
        cmd, suite_kind = self._resolve_test_command(root)
        if cmd is None:
            output = (
                "Test runner: no se encontró una suite ejecutable en el workspace "
                "(ni señales pytest con un Python disponible, ni package.json con script test).\n\n"
                "---AGENT-REPORT---\n"
                "role: test_runner\n"
                "result: blocked\n"
                "issue_status: blocked\n"
                "blocker: no hay comando de tests ejecutable en este entorno\n"
                "evidence: escaneo del workspace sin suite ejecutable\n"
            )
            return ExecutionResult(
                status="completed",
                output=output,
                actions={"issue_status": "blocked", "notify_supervisor": True},
            )

        timeout_sec = int(os.environ.get("AITEAM_TEST_RUNNER_TIMEOUT_SEC", "600") or "600")
        # P3 — pytest corre BAJO coverage cuando la herramienta existe en ese
        # intérprete: una sola ejecución produce exit code Y métrica de
        # cobertura. El data file va a .aiteam/ (gitignorado) para no
        # contaminar el workspace del equipo.
        runner_env = {**os.environ}
        measuring_coverage = False
        if suite_kind == "pytest" and self._python_has_module(cmd[0], "coverage"):
            runner_env["COVERAGE_FILE"] = str(Path(root) / ".aiteam" / ".coverage")
            cmd = [cmd[0], "-m", "coverage", "run", "-m", "pytest", *cmd[3:]]
            measuring_coverage = True
        cmd_display = " ".join(cmd)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                env=runner_env,
            )
            exit_code = int(proc.returncode)
            tail = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            tail = self._ANSI_RE.sub("", tail).strip()[-2000:]
        except subprocess.TimeoutExpired:
            output = (
                f"Test runner: `{cmd_display}` superó el timeout de {timeout_sec}s.\n\n"
                "---AGENT-REPORT---\n"
                "role: test_runner\n"
                "result: blocked\n"
                "issue_status: blocked\n"
                f"blocker: la suite no terminó en {timeout_sec}s (timeout)\n"
                f"evidence: {cmd_display} -> timeout tras {timeout_sec}s\n"
            )
            return ExecutionResult(
                status="completed",
                output=output,
                actions={"issue_status": "blocked", "notify_supervisor": True},
            )
        except Exception as exc:
            return ExecutionResult(status="failed", error=f"test runner exec failed: {exc}", exit_code=1)

        passed = exit_code == 0
        # ── P3: métricas de calidad deterministas (cobertura + lint) ────────
        # Subprocess + números, no narración: es el listón mecánico que un
        # agente no puede alucinar. Umbrales opcionales bloqueantes por env.
        coverage_pct: float | None = None
        if measuring_coverage and passed:
            cov = self._run_quiet(
                [cmd[0], "-m", "coverage", "report", "--format=total"],
                cwd=root, env=runner_env,
            )
            if cov is not None and cov.returncode == 0:
                with contextlib.suppress(ValueError):
                    coverage_pct = float(cov.stdout.strip())
        lint_issues: int | None = None
        if suite_kind == "pytest" and self._python_has_module(cmd[0], "ruff"):
            ruff = self._run_quiet(
                [cmd[0], "-m", "ruff", "check", "--output-format=concise", "."],
                cwd=root, env=runner_env,
            )
            if ruff is not None and ruff.returncode in (0, 1):
                lint_issues = len([l for l in (ruff.stdout or "").splitlines() if l.strip()])

        blocker: str | None = None if passed else f"la suite falló con exit {exit_code}"
        if passed and coverage_pct is not None:
            _min_cov = os.environ.get("AITEAM_MIN_COVERAGE_PERCENT", "").strip()
            with contextlib.suppress(ValueError):
                if _min_cov and coverage_pct < float(_min_cov):
                    passed = False
                    blocker = f"cobertura {coverage_pct:.0f}% por debajo del mínimo exigido ({_min_cov}%)"
        if passed and lint_issues is not None:
            _max_lint = os.environ.get("AITEAM_MAX_LINT_ISSUES", "").strip()
            with contextlib.suppress(ValueError):
                if _max_lint and lint_issues > int(_max_lint):
                    passed = False
                    blocker = f"{lint_issues} avisos de lint por encima del máximo permitido ({_max_lint})"

        # "exit 0" literal en evidence: es lo que _test_runner_gate_line busca
        # cuando el report llega por la tabla agent_reports (que no conserva
        # el campo exit_code, solo los REPORT_FIELDS).
        evidence = f"{cmd_display} -> exit {exit_code} ({suite_kind})"
        summary_line = tail.splitlines()[-1].strip() if tail else ""
        if summary_line:
            evidence += f"; {summary_line[:160]}"
        if coverage_pct is not None:
            evidence += f"; coverage {coverage_pct:.0f}%"
        if lint_issues is not None:
            evidence += f"; ruff {lint_issues} issue(s)"
        metrics_lines = ""
        if coverage_pct is not None:
            metrics_lines += f"coverage_percent: {coverage_pct:.0f}\n"
        if lint_issues is not None:
            metrics_lines += f"lint_issues: {lint_issues}\n"
        report = (
            "---AGENT-REPORT---\n"
            "role: test_runner\n"
            f"result: {'done' if passed else 'failed'}\n"
            f"issue_status: {'done' if passed else 'blocked'}\n"
            + (f"blocker: {blocker}\n" if blocker else "")
            + metrics_lines
            + f"evidence: {evidence}\n"
        )
        output = (
            f"Test runner (builtin determinista): `{cmd_display}` en {root}\n"
            f"Exit code: {exit_code}\n\n"
            f"--- salida (tail) ---\n{tail}\n\n{report}"
        )
        return ExecutionResult(
            status="completed",
            output=output,
            exit_code=exit_code,
            actions={"issue_status": "done" if passed else "blocked", "notify_supervisor": True},
        )

    def _resolve_test_command(self, root: Path) -> tuple[list[str] | None, str]:
        """Detecta la suite del workspace y devuelve (comando, tipo).

        Python primero (pytest con el venv del workspace o, en su defecto, el
        intérprete del orquestador — que siempre existe y trae pytest), luego
        npm test si package.json declara un script test real. Devuelve
        (None, "") si no hay nada ejecutable.
        """
        has_pytest_signals = False
        pytest_targets: list[str] = []
        node_pkg: Path | None = None
        try:
            for rel_path in snapshot_workspace(root):
                path_key = str(rel_path).replace("\\", "/")
                name = path_key.rsplit("/", 1)[-1]
                if name in {"pytest.ini", "tox.ini", "noxfile.py"}:
                    has_pytest_signals = True
                elif path_key.startswith(("tests/", "test/")) and name.startswith("test_") and name.endswith(".py"):
                    has_pytest_signals = True
                    pytest_targets.append(path_key)
                elif name.startswith("test_") and name.endswith(".py"):
                    has_pytest_signals = True
                    pytest_targets.append(path_key)
                elif name == "pyproject.toml":
                    with contextlib.suppress(Exception):
                        if "pytest" in (root / rel_path).read_text(encoding="utf-8", errors="ignore").lower():
                            has_pytest_signals = True
                elif name == "package.json" and node_pkg is None:
                    node_pkg = root / rel_path
        except Exception:
            logger.warning("test command scan failed for %s", root, exc_info=True)
            return None, ""

        if has_pytest_signals:
            python = self._resolve_workspace_python(root)
            if python:
                # Pass discovered test files explicitly. Pytest's recursive
                # collection can otherwise enter locked CLI scratch folders on
                # Windows and fail an otherwise green solo run with WinError 5.
                return [python, "-m", "pytest", "-q", *sorted(set(pytest_targets))], "pytest"

        if node_pkg is not None:
            with contextlib.suppress(Exception):
                pkg = json.loads(node_pkg.read_text(encoding="utf-8", errors="ignore"))
                test_script = str((pkg.get("scripts") or {}).get("test") or "")
                if test_script and "no test specified" not in test_script:
                    import shutil as _shutil  # noqa: PLC0415
                    npm = _shutil.which("npm.cmd") or _shutil.which("npm")
                    if npm:
                        return [npm, "test", "--silent"], "npm test"
        return None, ""

    # Cache de sondas (python, module) — el intérprete no cambia entre runs y
    # cada sonda cuesta un arranque de Python (~1s en Windows).
    _MODULE_PROBE_CACHE: dict[tuple[str, str], bool] = {}

    def _python_has_module(self, python: str, module: str) -> bool:
        key = (python, module)
        cached = self._MODULE_PROBE_CACHE.get(key)
        if cached is not None:
            return cached
        probe = self._run_quiet([python, "-c", f"import {module}"], cwd=None, env=None, timeout=30)
        result = probe is not None and probe.returncode == 0
        self._MODULE_PROBE_CACHE[key] = result
        return result

    @staticmethod
    def _run_quiet(
        cmd: list[str], *, cwd: Path | None, env: dict[str, str] | None, timeout: int = 120
    ) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                cmd,
                cwd=str(cwd) if cwd else None,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except Exception:
            return None

    def _resolve_workspace_python(self, root: Path) -> str | None:
        """Python con pytest importable: venv del workspace > intérprete propio."""
        candidates = [
            root / "venv" / "Scripts" / "python.exe",
            root / ".venv" / "Scripts" / "python.exe",
            root / "venv" / "bin" / "python",
            root / ".venv" / "bin" / "python",
        ]
        for candidate in candidates:
            if not candidate.exists():
                continue
            with contextlib.suppress(Exception):
                probe = subprocess.run(
                    [str(candidate), "-c", "import pytest"],
                    capture_output=True, timeout=30,
                )
                if probe.returncode == 0:
                    return str(candidate)
        with contextlib.suppress(Exception):
            probe = subprocess.run(
                [sys.executable, "-c", "import pytest"],
                capture_output=True, timeout=30,
            )
            if probe.returncode == 0:
                return sys.executable
        return None

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
            "accept_quorum_synthesis": "accept_quorum_synthesis",
            "append_context_summary": "append_context_summary",
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

        # El curador solo puede cerrar tras persistir exactamente el rango que
        # recibió. Recalculamos source/rango/ratio: el modelo no decide estos
        # invariantes ni puede escribir sobre otra issue.
        _context_summary_persisted = False
        _context_summary_error = "missing append_context_summary operation"
        _context_action = actions.get("append_context_summary")
        if isinstance(_context_action, dict) and str(agent_role or "").strip().lower() == "context_curator":
            try:
                with contextlib.closing(_connect(self.db_path)) as _conn:
                    _parent_row = _conn.execute(
                        "SELECT parent_id FROM issues WHERE id=?", (issue_id,)
                    ).fetchone()
                _parent_id = str(_parent_row["parent_id"] or "") if _parent_row else ""
                _expected = build_context_curation_target(self.db_path, issue_id=_parent_id)
                if not _expected:
                    raise ValueError("no unsynthesized parent context is available")
                for _field in (
                    "target_issue_id", "start_comment_id", "end_comment_id",
                    "char_count_original", "start_char_offset", "end_char_offset",
                ):
                    if str(_context_action.get(_field) or "") != str(_expected.get(_field) or ""):
                        raise ValueError(f"context summary {_field} does not match the durable source slice")
                _summary_body = str(_context_action.get("summary_markdown") or "").strip()
                if not _summary_body:
                    raise ValueError("context summary body is empty")
                if len(_summary_body) / int(_expected["char_count_original"]) > 0.30:
                    raise ValueError("context summary exceeds the 30% compression budget")
                with contextlib.closing(_connect(self.db_path)) as _conn:
                    _end_row = _conn.execute(
                        "SELECT LENGTH(body) AS body_chars FROM issue_comments WHERE id=? AND issue_id=?",
                        (_expected["end_comment_id"], _parent_id),
                    ).fetchone()
                _end_is_complete = bool(
                    _end_row and int(_expected["end_char_offset"]) >= int(_end_row["body_chars"] or 0)
                )
                append_summary_block(
                    self.db_path,
                    issue_id=_parent_id,
                    block={
                        "summary_markdown": _summary_body,
                        "start_comment_id": _expected["start_comment_id"],
                        "end_comment_id": _expected["end_comment_id"],
                        "char_count_original": _expected["char_count_original"],
                        "start_char_offset": _expected["start_char_offset"],
                        "end_char_offset": _expected["end_char_offset"],
                    },
                    synthesized_through_comment_id=(
                        str(_expected["end_comment_id"]) if _end_is_complete else None
                    ),
                    partial_comment_id=(None if _end_is_complete else str(_expected["end_comment_id"])),
                    partial_char_offset=(None if _end_is_complete else int(_expected["end_char_offset"])),
                    run_id=str(run.get("id") or "") or None,
                )
                _context_summary_persisted = True
                log_activity(
                    self.db_path,
                    action="context_summary.appended",
                    target_type="issue",
                    target_id=_parent_id,
                    actor_agent_id=agent_id,
                    run_id=str(run.get("id") or ""),
                    payload={
                        "start_comment_id": _expected["start_comment_id"],
                        "end_comment_id": _expected["end_comment_id"],
                        "char_count_original": _expected["char_count_original"],
                        "char_count_summary": len(_summary_body),
                    },
                )
            except Exception as exc:
                _context_summary_error = str(exc)
                logger.warning("append_context_summary rejected for issue %s: %s", issue_id, exc)

        if (
            str(agent_role or "").strip().lower() == "context_curator"
            and actions.get("issue_status") == "done"
            and not _context_summary_persisted
        ):
            actions = dict(actions)
            _curator_issue = get_issue(self.db_path, issue_id=issue_id) or {}
            _curator_metadata = _decode_json(_curator_issue.get("metadata_json") or "{}")
            _recovery = _curator_metadata.get("context_curator_recovery")
            if not isinstance(_recovery, dict):
                _recovery = {}
            _attempts = int(_recovery.get("corrective_attempts") or 0)
            _diagnostic = (
                "El bloque append_context_summary fue rechazado: "
                f"{_context_summary_error}. Reutiliza exactamente "
                "payload.context_curation_target y vuelve a emitir el artefacto."
            )
            _curator_metadata["context_curator_recovery"] = {
                "corrective_attempts": min(_attempts + 1, 2),
                "last_error": _context_summary_error,
                "last_run_id": str(run.get("id") or ""),
                "state": "retry_queued" if _attempts == 0 else "escalated",
            }
            update_issue(self.db_path, issue_id=issue_id, metadata=_curator_metadata)
            actions.setdefault("add_comments", []).append(f"⚙ Sistema: {_diagnostic}")
            if _attempts == 0:
                # Conserva in_progress: Tier 3 no tiene autoridad para volver a
                # todo. La wakeup durable crea la siguiente run sobre la misma
                # issue una vez finalice la actual.
                actions.pop("issue_status", None)
                actions.pop("notify_supervisor", None)
                enqueue_wakeup(
                    self.db_path,
                    agent_id=agent_id,
                    source="context_curator_recovery",
                    reason="context_summary_corrective_retry",
                    payload={
                        "issue_id": issue_id,
                        "diagnostic": _diagnostic,
                        "corrective_attempt": 1,
                        "source_run_id": str(run.get("id") or ""),
                    },
                    idempotency_key=f"context-curator-recovery:{issue_id}:1",
                    trigger_detail=_context_summary_error,
                )
                log_activity(
                    self.db_path,
                    action="context_summary.recovery_queued",
                    target_type="issue",
                    target_id=issue_id,
                    actor_agent_id=agent_id,
                    run_id=str(run.get("id") or ""),
                    payload={"attempt": 1, "error": _context_summary_error},
                )
            else:
                actions["issue_status"] = "blocked"
                actions["notify_supervisor"] = True
                log_activity(
                    self.db_path,
                    action="context_summary.recovery_exhausted",
                    target_type="issue",
                    target_id=issue_id,
                    actor_agent_id=agent_id,
                    run_id=str(run.get("id") or ""),
                    payload={"attempts": _attempts + 1, "error": _context_summary_error},
                )

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
            _payload_reason_early = str(((interaction.get("payload") or {}) or {}).get("reason") or "")
            # ── Extension proposals (self-extension PR2) — Tier 1 only ────────
            # propose_extension is documented as a create_interaction with this
            # reason, not a new op type (mirrors lead_wants_file_read etc.). Only
            # the Lead formalizes a proposal — Tier 2 can only SIGNAL a need in
            # its report (needs_capability:); enforced here, not just by prompt,
            # since installing an MCP server runs third-party code on approval.
            if _payload_reason_early == EXTENSION_PROPOSAL_REASON:
                if agent_role.lower() not in _LEAD_TIER_ROLES_P:
                    logger.warning(
                        "role.op_denied (extension_proposal): role=%r issue=%s — only Lead-tier may propose",
                        agent_role, issue_id,
                    )
                    try:
                        log_activity(
                            self.db_path,
                            action="role.op_denied",
                            target_type="issue",
                            target_id=issue_id,
                            actor_agent_id=agent_id,
                            run_id=str(run.get("id")),
                            payload={"role": agent_role, "action_group": "extension_proposal", "reason": EXTENSION_PROPOSAL_REASON},
                        )
                    except Exception:
                        pass
                    continue
                _ext_payload = interaction.get("payload") or {}
                _missing = [
                    field for field in ("name", "source", "justification")
                    if not str(_ext_payload.get(field) or "").strip()
                ]
                if _missing:
                    logger.warning(
                        "extension_proposal rejected for issue %s — missing fields: %s", issue_id, _missing,
                    )
                    try:
                        create_comment(
                            self.db_path,
                            issue_id=issue_id,
                            body=(
                                f"⚙ Sistema: propuesta de extensión rechazada — faltan campos obligatorios: "
                                f"{', '.join(_missing)}. payload.reason={EXTENSION_PROPOSAL_REASON} requiere "
                                "name, source y justification."
                            ),
                            author_user_id="system",
                        )
                    except Exception:
                        logger.warning("failed to post extension-proposal rejection comment on %s", issue_id)
                    continue
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
            if issue_status == "done" and str(agent_role or "").strip().lower() in {"lead", "team_lead"}:
                _quality_denied_reason = self._quality_close_denied(issue_id=issue_id)
                if _quality_denied_reason:
                    logger.warning(
                        "quality_gate.denied (issue_status): role=%r issue=%s target=%r reason=%s",
                        agent_role, issue_id, issue_status, _quality_denied_reason,
                    )
                    try:
                        log_activity(
                            self.db_path,
                            action="quality_gate.denied",
                            target_type="issue",
                            target_id=issue_id,
                            actor_agent_id=agent_id,
                            run_id=str(run.get("id")),
                            payload={
                                "role": agent_role,
                                "target_status": issue_status,
                                "reason": _quality_denied_reason,
                            },
                        )
                    except Exception:
                        pass
                    # La denegación NUNCA puede ser silenciosa: sin esto el Lead
                    # cree que cerró ("marco como completada"), la issue queda
                    # blocked/in_progress sin wakeups pendientes y el proyecto
                    # se estanca hasta que un humano mira la activity_log
                    # (deadlock visto en vivo en CLI Notas, 2026-07-15).
                    self._notify_quality_gate_denial(
                        issue_id=issue_id,
                        agent_id=agent_id,
                        run_id=str(run.get("id")),
                        reason=_quality_denied_reason,
                    )
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
                with contextlib.closing(_connect(self.db_path)) as conn:
                    conn.execute(
                        """
                        UPDATE wakeup_requests
                        SET status='cancelled', finished_at=CURRENT_TIMESTAMP,
                            updated_at=CURRENT_TIMESTAMP,
                            error='target_issue_terminal'
                        WHERE status='queued'
                          AND COALESCE(
                              json_extract(payload_json, '$.issue_id'),
                              json_extract(payload_json, '$.task_id')
                          ) = ?
                        """,
                        (issue_id,),
                    )
            except Exception:
                logger.warning("terminal wakeup cancellation failed for issue %s", issue_id, exc_info=True)
            try:
                resolve_blocker_wakeups(self.db_path, resolved_issue_id=issue_id, source_run_id=str(run.get("id") or ""))
            except Exception:
                logger.warning("resolve_blocker_wakeups failed for issue %s", issue_id, exc_info=True)
            # Cierre de una issue RAÍZ = fin de ciclo del proyecto: destilar
            # los hechos operativos a learning_facts (+ espejo global) para
            # que el próximo proyecto no re-descubra las mismas fricciones.
            if issue_status == "done":
                try:
                    with contextlib.closing(_connect(self.db_path)) as conn:
                        _parent = conn.execute(
                            "SELECT parent_id FROM issues WHERE id = ?", (issue_id,)
                        ).fetchone()
                    if _parent is not None and _parent["parent_id"] is None:
                        from aiteam.learning import distill_learning_facts  # noqa: PLC0415
                        distilled = distill_learning_facts(self.db_path)
                        if distilled:
                            logger.info(
                                "learning: %d fact(s) distilled at close of %s", len(distilled), issue_id
                            )
                except Exception:
                    logger.warning("learning distillation failed at close of %s", issue_id, exc_info=True)
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
        # Auto-report on any terminal issue_status so the Lead is always woken
        # without requiring the LLM to remember the op. Applies to EVERY
        # non-lead role: la lista blanca original (engineer/reviewer/qa) dejó
        # fuera a los scouts Tier-3, y un file_scout de verificación final que
        # cerró done sin notify_supervisor dejó al Lead sin despertar para
        # siempre — padre in_progress, todos los hijos done, cero wakeups
        # (visto en vivo en CLI Tareas, 2026-07-15).
        # Idempotency in _enqueue_supervisor_report prevents double-wakeups when
        # notify_supervisor was already emitted above.
        if (
            isinstance(issue_status, str)
            and issue_status in {"done", "blocked", "cancelled"}
            and agent_role.lower() not in _LEAD_TIER_ROLES_P
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
                    _stored_comment_report = record_agent_report(
                        self.db_path,
                        issue_id=issue_id,
                        agent_id=agent_id,
                        run_id=str(run.get("id")),
                        agent_role=agent_role,
                        parsed=_comment_report,
                    )
                    self._maybe_record_quorum_contribution(
                        issue_id=issue_id,
                        agent_id=agent_id,
                        run=run,
                        report=_stored_comment_report,
                        source_body=body,
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
        _quorum_action_candidate = actions.get("accept_quorum_synthesis")
        _new_plan_revision_id = ""
        # Diagnóstico del gate de profundidad del Plan B. Se evalúa FUERA del
        # try genérico: si cayera en su except, el Lead recibiría después la
        # causa equivocada ("requires update_plan in the same run") y podría
        # quemar sus reintentos sin saber qué dimensiones faltan.
        _final_plan_depth_error = ""
        if isinstance(plan_action, dict) and plan_action.get("body"):
            if isinstance(_quorum_action_candidate, dict):
                final_depth = evaluate_plan_depth(str(plan_action["body"]))
                if not final_depth["valid"]:
                    _final_plan_depth_error = (
                        "el Plan B no cumple el contrato de profundidad — dimensiones ausentes: "
                        f"{', '.join(final_depth['missing_dimensions']) or 'ninguna'}; "
                        f"palabras: {final_depth['word_count']}/{final_depth['min_words']}"
                    )
        if isinstance(plan_action, dict) and plan_action.get("body") and not _final_plan_depth_error:
            try:
                existing = get_document(self.db_path, issue_id=issue_id, key="plan")
                updated_plan = put_document(
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
                _new_plan_revision_id = str(updated_plan.get("current_revision_id") or "")
            except DocumentConflict:
                pass
            except Exception as exc:
                logger.warning("update_plan action failed for issue %s: %s", issue_id, exc)

        # ``lead_quorum`` is already an explicit user choice. Once Plan A is
        # durable, start its independent reviews without asking for a second
        # hiring approval. This also supports Leads that emitted a plan-shaped
        # add_comment: _maybe_materialize_plan_comment ran immediately above.
        self._maybe_start_explicit_quorum(issue_id=issue_id, run_id=str(run.get("id") or ""))

        quorum_action = _quorum_action_candidate
        if isinstance(quorum_action, dict):
            session_id = str(quorum_action.get("session_id") or "").strip()
            dispositions = quorum_action.get("dispositions")
            try:
                if str(agent_role or "").strip().lower() not in {"lead", "team_lead"}:
                    raise ValueError("only the Lead may accept a quorum synthesis")
                session = get_quorum_session(self.db_path, session_id=session_id)
                if session is None or str(session.get("issue_id") or "") != issue_id:
                    raise ValueError("quorum session does not belong to the current Lead issue")
                if not _new_plan_revision_id:
                    raise ValueError(
                        _final_plan_depth_error
                        or "quorum synthesis requires update_plan in the same run"
                    )
                if not isinstance(dispositions, list):
                    raise ValueError("quorum synthesis dispositions must be a list")
                accept_quorum_synthesis(
                    self.db_path,
                    session_id=session_id,
                    synthesis_run_id=str(run.get("id") or ""),
                    final_plan_revision_id=_new_plan_revision_id,
                    dispositions=dispositions,
                )
            except Exception as exc:
                logger.warning("quorum synthesis rejected for %s: %s", session_id, exc)
                with contextlib.suppress(Exception):
                    create_comment(
                        self.db_path,
                        issue_id=issue_id,
                        author_user_id="system",
                        body=(
                            f"⚙ Sistema: síntesis de quorum rechazada — {exc}. "
                            "Actualiza el plan y dispone cada finding antes de reintentar."
                        ),
                        metadata={"source": "quorum_synthesis_validation"},
                    )
                with contextlib.suppress(Exception):
                    log_activity(
                        self.db_path,
                        action="quorum.synthesis_rejected",
                        target_type="quorum_session",
                        target_id=session_id,
                        actor_agent_id=agent_id,
                        run_id=str(run.get("id") or "") or None,
                        payload={"issue_id": issue_id, "reason": str(exc)},
                    )
                with contextlib.closing(_connect(self.db_path)) as conn:
                    rejected_attempts = int(conn.execute(
                        """
                        SELECT COUNT(*) FROM activity_log
                        WHERE action = 'quorum.synthesis_rejected' AND target_id = ?
                        """,
                        (session_id,),
                    ).fetchone()[0])
                if rejected_attempts >= _QUORUM_MAX_SYNTHESIS_ATTEMPTS:
                    degrade_quorum_session(
                        self.db_path,
                        session_id=session_id,
                        skipped_reason="synthesis_attempts_exhausted",
                    )
                    with contextlib.closing(_connect(self.db_path)) as conn:
                        conn.execute(
                            """
                            UPDATE wakeup_requests
                            SET status = 'cancelled', error = 'synthesis_attempts_exhausted',
                                finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                            WHERE agent_id = ? AND status = 'queued'
                              AND reason = 'quorum_ready'
                              AND json_extract(payload_json, '$.quorum_session_id') = ?
                            """,
                            (agent_id, session_id),
                        )
                    create_interaction(
                        self.db_path,
                        issue_id=issue_id,
                        kind="request_confirmation",
                        payload={
                            "version": 1,
                            "reason": "quorum_synthesis_failed",
                            "quorum_session_id": session_id,
                            "last_error": str(exc),
                        },
                        idempotency_key=f"quorum:synthesis-failed:{session_id}",
                        source_run_id=str(run.get("id") or "") or None,
                        created_by_agent_id=agent_id,
                        title="Síntesis de quorum bloqueada",
                        summary=(
                            f"El Lead no pudo producir una síntesis válida tras "
                            f"{rejected_attempts} intentos. Revisa el plan o cancela el quorum."
                        ),
                    )
                else:
                    enqueue_wakeup(
                        self.db_path,
                        agent_id=agent_id,
                        source="quorum",
                        reason="quorum_ready",
                        payload={
                            "issue_id": issue_id,
                            "quorum_session_id": session_id,
                            "wake_reason": "quorum_ready",
                            "correction": str(exc),
                        },
                        idempotency_key=f"quorum_synthesis_retry:{session_id}:{str(run.get('id') or '')}",
                    )

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
                # A deterministic test failure cannot improve by executing the
                # same runner against the same files.  The Lead used to bypass
                # wakeup idempotency because every directive was keyed by its
                # own run id, producing test_runner -> Lead loops with identical
                # evidence.  Keep this as a runtime invariant: code must change
                # (normally through an engineer/fix issue) before the runner is
                # eligible again.
                if (
                    new_status in _active_requeue_statuses
                    and str(child_issue.get("role") or "").strip().lower() == "test_runner"
                    and self._failed_test_runner_workspace_unchanged(
                        issue_id=child_issue_id,
                        workspace_root=workspace_root_for_db(self.db_path),
                    )
                ):
                    digest = self._workspace_digest(workspace_root_for_db(self.db_path))
                    correction = (
                        f"⚙ Sistema: reejecución de `{child_issue_id}` rechazada. "
                        "El último test_runner falló y el workspace no ha cambiado. "
                        "Crea o reactiva un issue de engineer con la evidencia del fallo; "
                        "el test_runner podrá ejecutarse de nuevo después de un cambio real."
                    )
                    with contextlib.suppress(Exception):
                        create_comment(
                            self.db_path,
                            issue_id=issue_id,
                            author_agent_id=agent_id,
                            source_run_id=str(run.get("id")),
                            body=correction,
                            metadata={"source": "unchanged_test_failure_guard"},
                        )
                    with contextlib.suppress(Exception):
                        log_activity(
                            self.db_path,
                            action="lead.requeue_suppressed_unchanged_workspace",
                            target_type="issue",
                            target_id=child_issue_id,
                            actor_agent_id=agent_id,
                            run_id=str(run.get("id")),
                            payload={"parent_issue_id": issue_id, "workspace_digest": digest},
                        )
                    enqueue_wakeup(
                        self.db_path,
                        agent_id=agent_id,
                        source="policy",
                        reason="unchanged_test_failure",
                        payload={
                            "issue_id": issue_id,
                            "child_issue_id": child_issue_id,
                            "wake_reason": "unchanged_test_failure",
                            "mandatory_instruction": correction,
                        },
                        idempotency_key=f"unchanged_test_failure:{child_issue_id}:{digest}",
                    )
                    continue
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
        _issue_metadata = _decode_json((get_issue(self.db_path, issue_id=issue_id) or {}).get("metadata_json") or "{}")
        _run_profile = normalize_run_profile(_issue_metadata.get("profile") or "")
        _issue_specs = [
            spec for spec in (actions.get("create_issues") or []) if isinstance(spec, dict)
        ]
        if _run_profile == SOLO_LEAD and str(agent_role or "").strip().lower() in {"lead", "team_lead"}:
            # ``solo_lead`` is a true single-agent mode: the Lead itself owns
            # planning, implementation and verification. Any child creation
            # would turn it back into manager/worker orchestration.
            original_count = len(_issue_specs)
            _issue_specs = []
            if original_count:
                log_activity(
                    self.db_path,
                    action="profile.delegation_constrained",
                    target_type="issue",
                    target_id=issue_id,
                    actor_agent_id=agent_id,
                    run_id=str(run.get("id")),
                    payload={
                        "profile": SOLO_LEAD,
                        "requested_issues": original_count,
                        "accepted_issues": len(_issue_specs),
                        "accepted_role": "",
                    },
                )
        created_child_roles: list[str] = []
        for spec in _issue_specs:
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
        self._maybe_add_independent_test_designer(
            issue_id=issue_id,
            agent_id=agent_id,
            run=run,
            created_child_roles=created_child_roles,
        )
        self._maybe_add_adversarial_qa(
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
                            author_user_id="system",
                        )
                    except Exception:
                        logger.warning("failed to post thin-delegation rejection comment on %s", issue_id)
                    return None

            # ── Role-vs-work mismatch: read-only role, editing task ────────────
            # Live bug: the Lead delegated an exact, well-specified code fix
            # ("Files to modify: ...") with role=file_scout instead of engineer.
            # file_scout is Tier-3/read-only by design (NON_EDITING_ROLES) — it
            # read the file, confirmed the same diagnosis, and closed done
            # WITHOUT changing anything, because that IS its whole contract
            # ("read and report, close in the same run"). The Lead read that as
            # "fix delegated" and moved on, leaving the review permanently
            # blocked on a fix that was never materialized. "Files to modify:"
            # is unambiguous editing intent — no read-only role can satisfy it.
            # El título cuenta como señal: "Fix: corregir stats" (CLI Textos)
            # llevaba toda la intención en el título y una descripción neutra.
            if role_for_issue in _NON_EDITING_ROLES and _FILE_EDIT_SIGNAL_RE.search(
                f"{title_val}\n{_desc_val}"
            ):
                logger.warning(
                    "delegation_role_mismatch: issue %r delegated to read-only role=%r "
                    "but title/description requests file edits — rejecting.",
                    title_val, role_for_issue,
                )
                log_activity(
                    self.db_path,
                    action="delegation.role_mismatch",
                    target_type="issue",
                    target_id=issue_id,
                    actor_agent_id=agent_id,
                    run_id=str(run.get("id")),
                    payload={"title": title_val, "role": role_for_issue},
                )
                _mismatch_body = (
                    f"⚙ Sistema: delegación rechazada para «{title_val}» — "
                    f"role={role_for_issue} es de solo lectura (Tier 3, no puede escribir archivos) "
                    "pero la descripción pide modificar un archivo. Vuelve a delegar con "
                    "role=engineer (o software_engineer) para que el cambio se materialice de verdad."
                )
                try:
                    create_comment(
                        self.db_path,
                        issue_id=issue_id,
                        body=_mismatch_body,
                        author_user_id="system",
                    )
                except Exception:
                    logger.warning("failed to post role-mismatch rejection comment on %s", issue_id)
                return None

            # ── Action routing override ───────────────────────────────────────
            # If the spec includes criticality + action_type, apply route_action()
            # to determine the effective role regardless of what the LLM proposed.
            # This enforces tier discipline even when the LLM mis-assigns a role.
            _criticality = str(spec.get("criticality") or "").strip().lower()
            _complexity = str(spec.get("complexity") or "").strip().lower()
            _action_type = str(spec.get("action_type") or "").strip().lower()
            if _criticality and _action_type and not bool(spec.get("_profile_role_locked")):
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
                # Antes la criticality del spec se PERDÍA (solo viajaba en el
                # metadata del routing): el gate de compliance high/critical y
                # el pase adversarial nunca veían issues delegadas críticas.
                criticality=str(spec.get("criticality") or "").strip().lower() or None,
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

    def _maybe_add_independent_test_designer(
        self,
        *,
        issue_id: str,
        agent_id: str,
        run: dict[str, Any],
        created_child_roles: list[str],
    ) -> None:
        """Materializa el test_designer hermano al delegar engineering.

        La suite de aceptación la escribe un agente que NO implementa: desde
        la especificación del padre, en paralelo al engineer (las dependencias
        por defecto hacen que test_runner y reviewer esperen a AMBOS). Una vez
        por padre; determinista — no depende de que el Lead se acuerde.
        """
        from aiteam.policies import independent_tests_enabled

        if not independent_tests_enabled():
            return
        parent_issue = get_issue(self.db_path, issue_id=issue_id) or {}
        parent_meta = _decode_json(parent_issue.get("metadata_json") or "{}")
        if normalize_run_profile(parent_meta.get("profile") or "") != FULL_TEAM:
            return
        if "test_designer" in created_child_roles:
            return
        if not {"engineer", "software_engineer", "lead_executor"} & set(created_child_roles):
            return
        agent = self._agent_info(agent_id) or {}
        if str(agent.get("role") or "").strip().lower() not in {"lead", "team_lead"}:
            return
        with contextlib.closing(_connect(self.db_path)) as conn:
            existing = conn.execute(
                "SELECT id FROM issues WHERE parent_id = ? AND lower(role) = 'test_designer' LIMIT 1",
                (issue_id,),
            ).fetchone()
        if existing is not None:
            return
        criteria = [
            str(item) for item in (parent_meta.get("acceptance_criteria") or []) if str(item).strip()
        ]
        criteria_block = (
            "\n\nCriterios de aceptación del padre:\n" + "\n".join(f"- {c}" for c in criteria[:10])
            if criteria else ""
        )
        spec_source = str(parent_issue.get("description") or parent_issue.get("title") or "")[:1500]
        self._create_delegated_issue(
            issue_id=issue_id,
            agent_id=agent_id,
            run=run,
            spec={
                "title": "Suite de aceptación independiente (desde la spec)",
                "description": (
                    "Escribe tests de aceptación en tests/ SOLO a partir de la especificación "
                    "de abajo. NO leas la implementación ni sus tests: tu suite debe fallar si "
                    "la spec no se cumple, no confirmar lo que el código ya hace. Cubre el "
                    "camino feliz de cada requisito y al menos un caso borde por comando/función "
                    "(entradas vacías, datos inválidos, unicode). Los archivos deben poder "
                    "coexistir con otros tests: usa nombres tests/test_acceptance_*.py.\n\n"
                    f"Especificación:\n{spec_source}{criteria_block}"
                ),
                "role": "test_designer",
                "complexity": "medium",
            },
            metadata_source="independent_test_designer_guardrail",
            activity_source="guardrail:independent_test_designer",
        )

    def _maybe_add_adversarial_qa(
        self,
        *,
        issue_id: str,
        agent_id: str,
        run: dict[str, Any],
        created_child_roles: list[str],
    ) -> None:
        """Materializa el pase adversarial post-implementación (P4).

        Contrato inverso al del reviewer: no juzgar si está bien, sino
        intentar demostrarlo roto — solo puede aportar tests que FALLEN. Se
        activa en criticidad high/critical (o siempre con
        AITEAM_ADVERSARIAL_QA=always); una vez por padre; el rol qa ya es
        dependiente por defecto (espera a engineer y test_designer) y el
        enforcement cross-provider lo saca de la familia del engineer.
        """
        from aiteam.policies import adversarial_qa_mode

        mode = adversarial_qa_mode()
        if mode == "off":
            return
        parent_issue = get_issue(self.db_path, issue_id=issue_id) or {}
        parent_meta = _decode_json(parent_issue.get("metadata_json") or "{}")
        if normalize_run_profile(parent_meta.get("profile") or "") != FULL_TEAM:
            return
        if not {"engineer", "software_engineer", "lead_executor"} & set(created_child_roles):
            return
        if {"qa", "qa_engineer"} & set(created_child_roles):
            return
        agent = self._agent_info(agent_id) or {}
        if str(agent.get("role") or "").strip().lower() not in {"lead", "team_lead"}:
            return
        with contextlib.closing(_connect(self.db_path)) as conn:
            existing = conn.execute(
                "SELECT id FROM issues WHERE parent_id = ? AND lower(role) IN ('qa', 'qa_engineer') LIMIT 1",
                (issue_id,),
            ).fetchone()
            if existing is not None:
                return
            if mode == "high":
                parent_row = conn.execute(
                    "SELECT criticality FROM issues WHERE id = ?", (issue_id,)
                ).fetchone()
                parent_crit = str((parent_row or {"criticality": ""})["criticality"] or "").strip().lower()
                child_crit = conn.execute(
                    "SELECT COUNT(*) FROM issues WHERE parent_id = ? "
                    "AND lower(COALESCE(criticality, '')) IN ('high', 'critical')",
                    (issue_id,),
                ).fetchone()[0]
                if parent_crit not in {"high", "critical"} and not child_crit:
                    return
        self._create_delegated_issue(
            issue_id=issue_id,
            agent_id=agent_id,
            run=run,
            spec={
                "title": "Pase adversarial: intenta romper la entrega",
                "description": (
                    "Tu trabajo NO es confirmar que la entrega está bien: es intentar demostrarla "
                    "rota. Lee la implementación buscando huecos frente a la especificación y "
                    "aporta ÚNICAMENTE tests que fallen (tests/test_adversarial_*.py): casos borde "
                    "(entradas vacías, datos inválidos, unicode, archivos corruptos, límites), "
                    "condiciones de error y contratos implícitos de la spec que nadie probó. "
                    "Si un test que escribes pasa, bórralo — no aporta. Si tras un intento serio "
                    "no encuentras nada, reporta result: approved con evidencia de QUÉ intentaste "
                    "y por qué no rompió. Si encuentras fallos, deja los tests fallando, reporta "
                    "result: changes_requested y describe cada fallo en el blocker."
                ),
                "role": "qa",
                "complexity": "medium",
            },
            metadata_source="adversarial_qa_guardrail",
            activity_source="guardrail:adversarial_qa",
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

    @staticmethod
    def _effective_adapter_identity(
        *,
        runtime_provider: str | None,
        runtime_channel: str | None,
        adapter_cfg: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        """Resolve provenance from the durable profile, not a shared CLI runtime.

        ``subscription_cli`` is intentionally one runtime for Codex, Gemini and
        local CLIs.  Its descriptor therefore cannot identify the provider used
        by an individual run.  The selected profile can, and is also the source
        used by hiring and quorum diversity.
        """
        profile_id = str(adapter_cfg.get("profile_id") or "").strip()
        if profile_id:
            profile = next(
                (item for item in load_adapter_profiles() if str(item.get("id") or "") == profile_id),
                None,
            )
            if profile:
                provider = str(profile.get("provider") or "").strip() or runtime_provider
                channel = str(profile.get("channel") or "").strip() or runtime_channel
                return provider, channel
        return runtime_provider, runtime_channel

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

    def _maybe_start_explicit_quorum(self, *, issue_id: str, run_id: str) -> dict[str, Any] | None:
        """Start Plan-A reviews after an explicit ``lead_quorum`` selection.

        The profile selection is the user's approval for planning auditors; it
        must not depend on a second ``suggest_tasks`` interaction intended for
        implementation hiring.
        """
        issue = get_issue(self.db_path, issue_id=issue_id) or {}
        metadata = _decode_json(issue.get("metadata_json") or "{}")
        if normalize_run_profile(metadata.get("profile") or "") != LEAD_QUORUM:
            return None
        with contextlib.closing(_connect(self.db_path)) as conn:
            existing = conn.execute(
                "SELECT id FROM quorum_sessions WHERE issue_id=? ORDER BY created_at DESC LIMIT 1",
                (issue_id,),
            ).fetchone()
        if existing is not None:
            return None
        plan = get_document(self.db_path, issue_id=issue_id, key="plan")
        revision_id = str((plan or {}).get("current_revision_id") or "").strip()
        if not revision_id:
            return None
        plan_depth = evaluate_plan_depth(str((plan or {}).get("body") or ""))
        if not plan_depth["valid"]:
            with contextlib.closing(_connect(self.db_path)) as conn:
                already_reported = conn.execute(
                    "SELECT 1 FROM activity_log WHERE action='quorum.plan_depth_rejected' "
                    "AND target_id=? LIMIT 1",
                    (revision_id,),
                ).fetchone()
            if already_reported is None:
                create_comment(
                    self.db_path,
                    issue_id=issue_id,
                    author_user_id="system",
                    body=(
                        "⚙ Sistema: Plan A todavía no cumple el contrato profundo de quorum. "
                        f"Faltan dimensiones: {', '.join(plan_depth['missing_dimensions']) or 'ninguna'}; "
                        f"palabras: {plan_depth['word_count']}/{plan_depth['min_words']}. "
                        "El Lead debe publicar una revisión más completa antes de auditar."
                    ),
                    metadata={"source": "quorum_plan_depth_gate", "revision_id": revision_id},
                )
                log_activity(
                    self.db_path,
                    action="quorum.plan_depth_rejected",
                    target_type="plan_revision",
                    target_id=revision_id,
                    actor_agent_id="role:lead",
                    run_id=run_id or None,
                    payload={"issue_id": issue_id, **plan_depth},
                )
                enqueue_wakeup(
                    self.db_path,
                    agent_id=str(issue.get("assignee_agent_id") or "role:lead"),
                    source="quorum",
                    reason="quorum_plan_revision_required",
                    trigger_detail=f"quorum:plan-depth:{revision_id}",
                    payload={
                        "issue_id": issue_id,
                        "wake_reason": "quorum_plan_revision_required",
                        "plan_depth": plan_depth,
                    },
                    idempotency_key=f"quorum-plan-depth:{revision_id}",
                )
            return None
        proposal = build_team_proposal(
            issue,
            adapter_profiles=project_profiles(Path(self.db_path).parent),
            profile=LEAD_QUORUM,
        )
        proposal["plan_revision_id"] = revision_id
        outcome = apply_accepted_team_proposal(
            self.db_path,
            parent_issue_id=issue_id,
            proposal=proposal,
            source_run_id=run_id or None,
        )
        session = self._initialize_quorum_session(
            parent_issue_id=issue_id,
            proposal=proposal,
            created_issue_ids=outcome["created_issues"],
        )
        if session is not None:
            log_activity(
                self.db_path,
                action="quorum.auto_started",
                target_type="quorum_session",
                target_id=str(session["id"]),
                actor_agent_id="role:lead",
                run_id=run_id or None,
                payload={"issue_id": issue_id, "base_plan_revision_id": revision_id},
            )
        return session

    def _initialize_quorum_session(
        self,
        *,
        parent_issue_id: str,
        proposal: dict[str, Any],
        created_issue_ids: list[str],
    ) -> dict[str, Any] | None:
        base_revision_id = str(proposal.get("plan_revision_id") or "").strip()
        if not base_revision_id:
            logger.warning("lead_quorum proposal has no plan_revision_id for %s", parent_issue_id)
            return None
        auditor_specs = [
            item
            for item in (proposal.get("suggested_issues") or [])
            if isinstance(item, dict) and str(item.get("delegation_type") or "") == "risk_review"
        ]
        proposed_auditor_ids = {
            str(item.get("id") or "")
            for item in (proposal.get("proposed_team") or [])
            if isinstance(item, dict) and str(item.get("id") or "")
        }
        if proposed_auditor_ids:
            auditor_specs = [
                item for item in auditor_specs
                if str(item.get("assignee_agent_id") or "") in proposed_auditor_ids
            ]
        if not auditor_specs:
            return None
        session = create_quorum_session(
            self.db_path,
            issue_id=parent_issue_id,
            base_plan_revision_id=base_revision_id,
            requested_contributions=len(auditor_specs),
        )
        created_set = {str(item) for item in created_issue_ids}
        with contextlib.closing(_connect(self.db_path)) as conn:
            parent_row = conn.execute(
                "SELECT title, description, metadata_json FROM issues WHERE id=?", (parent_issue_id,)
            ).fetchone()
            parent_metadata = _decode_json(parent_row["metadata_json"] if parent_row else "{}")
            parent_metadata["quorum_objective_snapshot"] = {
                "session_id": session["id"],
                "title": str(parent_row["title"] or "") if parent_row else "",
                "description": str(parent_row["description"] or "") if parent_row else "",
                "base_plan_revision_id": base_revision_id,
            }
            conn.execute(
                "UPDATE issues SET metadata_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(parent_metadata, ensure_ascii=False, sort_keys=True), parent_issue_id),
            )
            for ordinal, spec in enumerate(auditor_specs, start=1):
                child_id = str(spec.get("id") or "").strip()
                if not child_id or child_id not in created_set:
                    continue
                row = conn.execute(
                    "SELECT metadata_json FROM issues WHERE id = ?", (child_id,)
                ).fetchone()
                metadata = _decode_json(row["metadata_json"] if row else "{}")
                metadata.update(
                    {
                        "quorum_session_id": session["id"],
                        "quorum_ordinal": ordinal,
                        "quorum_base_plan_revision_id": base_revision_id,
                    }
                )
                conn.execute(
                    "UPDATE issues SET metadata_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False, sort_keys=True), child_id),
                )
        log_activity(
            self.db_path,
            action="quorum.session_created",
            target_type="quorum_session",
            target_id=str(session["id"]),
            actor_agent_id="role:lead",
            payload={
                "issue_id": parent_issue_id,
                "base_plan_revision_id": base_revision_id,
                "requested_contributions": len(auditor_specs),
            },
        )
        return session

    def _ensure_quorum_auditor_continuation(
        self,
        *,
        issue_id: str,
        agent_id: str,
        run_id: str,
        run_status: str,
    ) -> None:
        """Retry or degrade when an auditor completes without a valid report.

        A successful process exit is not quorum evidence. Local/small models can
        satisfy the CLI output schema with only a summary, leaving the session
        reviewing with no wakeups. One corrective retry is allowed; a second
        format failure degrades the session and wakes the Lead durably.
        """
        if run_status not in {"completed", "skipped", "failed"} or not issue_id:
            return
        issue = get_issue(self.db_path, issue_id=issue_id) or {}
        metadata = _decode_json(issue.get("metadata_json") or "{}")
        session_id = str(metadata.get("quorum_session_id") or "").strip()
        if not session_id:
            return
        session = get_quorum_session(self.db_path, session_id=session_id)
        if session is None or str(session.get("status") or "") in {"accepted", "degraded", "failed"}:
            return
        with contextlib.closing(_connect(self.db_path)) as conn:
            contribution = conn.execute(
                "SELECT 1 FROM quorum_contributions WHERE session_id=? AND run_id=? AND valid=1",
                (session_id, run_id),
            ).fetchone()
            already_recorded = conn.execute(
                "SELECT 1 FROM activity_log WHERE action='quorum.auditor_report_missing' "
                "AND target_id=? AND run_id=?",
                (issue_id, run_id),
            ).fetchone()
            prior = int(conn.execute(
                "SELECT COUNT(*) FROM activity_log "
                "WHERE action='quorum.auditor_report_missing' AND target_id=?",
                (issue_id,),
            ).fetchone()[0])
        if contribution or already_recorded:
            return

        log_activity(
            self.db_path,
            action="quorum.auditor_report_missing",
            target_type="issue",
            target_id=issue_id,
            actor_agent_id=agent_id,
            run_id=run_id,
            payload={"session_id": session_id, "attempt": prior + 1},
        )
        if prior < 1:
            update_issue(self.db_path, issue_id=issue_id, status="todo")
            create_comment(
                self.db_path,
                issue_id=issue_id,
                author_user_id="system",
                body=(
                    "⚙ Sistema: la auditoría terminó sin AGENT-REPORT estructurado y no cuenta "
                    "para el quorum. Se permite un único reintento correctivo de formato."
                ),
                metadata={"source": "quorum_report_validation"},
            )
            enqueue_wakeup(
                self.db_path,
                agent_id=agent_id,
                source="quorum",
                reason="quorum_report_retry",
                payload={
                    "issue_id": issue_id,
                    "quorum_session_id": session_id,
                    "wake_reason": "quorum_report_retry",
                },
                idempotency_key=f"quorum_report_retry:{session_id}:{agent_id}",
            )
            return

        degraded = degrade_quorum_session(
            self.db_path,
            session_id=session_id,
            skipped_reason="auditor_report_format_exhausted",
        )
        with contextlib.closing(_connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE wakeup_requests SET status='cancelled', finished_at=CURRENT_TIMESTAMP, "
                "updated_at=CURRENT_TIMESTAMP, error='quorum_session_degraded' "
                "WHERE agent_id=? AND reason='quorum_report_retry' AND status='queued' "
                "AND json_extract(payload_json, '$.quorum_session_id')=?",
                (agent_id, session_id),
            )
            conn.commit()
        parent_issue_id = str(issue.get("parent_id") or degraded.get("issue_id") or "").strip()
        parent = get_issue(self.db_path, issue_id=parent_issue_id) or {}
        lead_id = str(parent.get("assignee_agent_id") or "role:lead").strip()
        enqueue_wakeup(
            self.db_path,
            agent_id=lead_id,
            source="quorum",
            reason="quorum_degraded",
            payload={
                "issue_id": parent_issue_id,
                "child_issue_id": issue_id,
                "quorum_session_id": session_id,
                "wake_reason": "quorum_degraded",
                "skipped_reason": "auditor_report_format_exhausted",
            },
            idempotency_key=f"quorum_degraded:{session_id}",
        )

    def _maybe_record_quorum_contribution(
        self,
        *,
        issue_id: str,
        agent_id: str,
        run: dict[str, Any],
        report: dict[str, Any],
        source_body: str = "",
    ) -> dict[str, Any] | None:
        issue = get_issue(self.db_path, issue_id=issue_id) or {}
        metadata = _decode_json(issue.get("metadata_json") or "{}")
        session_id = str(metadata.get("quorum_session_id") or "").strip()
        if not session_id or not int(report.get("valid") or 0) or not int(report.get("is_assignee") or 0):
            return None
        evidence = str(report.get("evidence") or report.get("blocker") or "").strip()
        result = str(report.get("result") or "").strip().lower()
        parsed_audit = parse_quorum_audit(source_body)
        audit_validation = validate_quorum_audit(parsed_audit)
        findings = audit_validation["findings"] if audit_validation["valid"] else []
        if audit_validation["valid"] and isinstance(parsed_audit, dict):
            evidence = json.dumps(
                {
                    "executive_assessment": parsed_audit.get("executive_assessment"),
                    "strengths": parsed_audit.get("strengths"),
                    "assumptions_challenged": parsed_audit.get("assumptions_challenged"),
                    "agent_report_evidence": evidence,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        if not findings:
            log_activity(
                self.db_path,
                action="quorum.audit_contract_invalid",
                target_type="issue",
                target_id=issue_id,
                actor_agent_id=agent_id,
                run_id=str(run.get("id") or "") or None,
                payload={"session_id": session_id, "errors": audit_validation["errors"]},
            )
        contribution = record_quorum_contribution(
            self.db_path,
            session_id=session_id,
            agent_id=agent_id,
            run_id=str(run.get("id") or "") or None,
            ordinal=int(metadata.get("quorum_ordinal") or 0),
            provider=str(run.get("provider") or ""),
            model=str(run.get("model") or ""),
            channel=str(run.get("channel") or "") or None,
            result=result,
            evidence=evidence,
            findings=findings,
        )
        log_activity(
            self.db_path,
            action="quorum.contribution_recorded",
            target_type="quorum_contribution",
            target_id=str(contribution["id"]),
            actor_agent_id=agent_id,
            run_id=str(run.get("id") or "") or None,
            payload={
                "session_id": session_id,
                "issue_id": issue_id,
                "valid": bool(contribution["valid"]),
                "provider": contribution.get("provider"),
            },
        )
        # El reporte puede llegar en result.output o dentro de un add_comment.
        # En el segundo caso, el auto-supervisor genérico ya se ejecutó antes;
        # evaluar aquí garantiza que el último aporte siempre continúe el gate.
        self._enqueue_supervisor_report(
            issue_id=issue_id,
            reporting_agent_id=agent_id,
            source_run_id=str(run.get("id") or ""),
        )
        return contribution

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

        # Quorum auditors report through their durable session gate, not via a
        # generic child_report.  Partial opinions never wake the Lead.  Once
        # all configured auditors finished, emit either quorum_ready or an
        # explicit degraded continuation — never leave the root silent.
        issue_metadata = _decode_json((fresh_issue or issue).get("metadata_json") or "{}")
        quorum_session_id = str(issue_metadata.get("quorum_session_id") or "").strip()
        if quorum_session_id:
            session = get_quorum_session(self.db_path, session_id=quorum_session_id)
            if session is None:
                return
            if str(session.get("status") or "") in {"accepted", "degraded", "failed"}:
                return
            gate = evaluate_quorum_session(self.db_path, session_id=quorum_session_id)
            if gate["ready"]:
                reason = "quorum_ready"
            elif gate["total_contributions"] >= int(session["requested_contributions"]):
                reason = "quorum_degraded"
                skipped_reason = (
                    "provider_diversity_unsatisfied"
                    if not gate["diversity_satisfied"]
                    else "insufficient_valid_contributions"
                )
                degrade_quorum_session(
                    self.db_path,
                    session_id=quorum_session_id,
                    skipped_reason=skipped_reason,
                )
            else:
                return
            enqueue_wakeup(
                self.db_path,
                agent_id=supervisor_agent_id,
                source="quorum",
                reason=reason,
                trigger_detail=f"quorum:{quorum_session_id}:{reason}",
                payload={
                    "issue_id": parent_issue_id,
                    "child_issue_id": issue_id,
                    "quorum_session_id": quorum_session_id,
                    "wake_reason": reason,
                    "quorum_gate": gate,
                },
                idempotency_key=f"{reason}:{quorum_session_id}",
            )
            return

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

    def _enforce_cross_provider_review(
        self, *, issue_id: str, agent_id: str, agent_role: str
    ) -> bool:
        """Fuerza juez de otra familia en issues high/critical (una vez por issue).

        Devuelve True si re-apuntó el adapter del reviewer (el caller debe
        recargar agent_info). Sin alternativa conectada, queda la señal
        informativa de `_separation_of_duties_line` — no se bloquea el review.
        """
        from aiteam.policies import cross_provider_review_enforced

        role_key = str(agent_role or "").strip().lower()
        # qa incluido: el pase adversarial (P4) pierde su gracia si el atacante
        # comparte los sesgos del implementador.
        if role_key not in {"reviewer", "code_reviewer", "qa", "qa_engineer"} or not issue_id:
            return False
        if not cross_provider_review_enforced():
            return False
        issue = self._issue_info(issue_id)
        criticality = str((issue or {}).get("criticality") or "").strip().lower()
        if criticality not in {"high", "critical"}:
            return False
        with contextlib.closing(_connect(self.db_path)) as conn:
            if conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE action = 'review.cross_provider_enforced' AND target_id = ?",
                (issue_id,),
            ).fetchone()[0]:
                return False

        from aiteam.hiring_economics import provider_and_model_for  # noqa: PLC0415

        root_id = self._root_issue_id(issue_id)
        engineer_provider = ""
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT a.adapter_type, a.adapter_config_json
                FROM issues i JOIN agents a ON a.id = i.assignee_agent_id
                WHERE i.parent_id = ? AND lower(i.role) IN ('engineer', 'software_engineer')
                ORDER BY i.created_at DESC LIMIT 1
                """,
                (root_id,),
            ).fetchone()
            if row:
                engineer_provider, _ = provider_and_model_for(
                    str(row["adapter_type"] or ""), _decode_json(str(row["adapter_config_json"] or "{}"))
                )
            me = conn.execute(
                "SELECT adapter_type, adapter_config_json FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
        if not engineer_provider or me is None:
            return False
        my_provider, _ = provider_and_model_for(
            str(me["adapter_type"] or ""), _decode_json(str(me["adapter_config_json"] or "{}"))
        )
        if my_provider != engineer_provider:
            return False  # ya es cross-provider

        profiles = project_profiles(Path(self.db_path).parent)
        candidates = []
        for profile in profiles:
            if not profile_is_connected(profile):
                continue
            prov, _ = provider_and_model_for(
                str(profile.get("adapter_type") or ""), {"profile_id": profile.get("id")}
            )
            if prov and prov != engineer_provider:
                candidates.append(profile)
        if not candidates:
            return False
        from aiteam.hiring_economics import demoted_profile_ids as _demoted  # noqa: PLC0415
        selection = choose_adapter_for_role(
            agent_role, None, candidates,
            demoted_profile_ids=_demoted(self.db_path, candidates),
        )
        if not selection:
            return False

        with contextlib.closing(_connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE agents SET adapter_type = ?, adapter_config_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (
                    str(selection["adapter_type"]),
                    json.dumps(selection.get("adapter_config") or {}, ensure_ascii=False, sort_keys=True),
                    agent_id,
                ),
            )
        log_activity(
            self.db_path,
            action="review.cross_provider_enforced",
            target_type="issue",
            target_id=issue_id,
            actor_agent_id=agent_id,
            payload={
                "criticality": criticality,
                "engineer_provider": engineer_provider,
                "new_adapter_type": str(selection["adapter_type"]),
                "new_adapter_profile_id": selection.get("adapter_profile_id"),
            },
        )
        with contextlib.suppress(Exception):
            create_comment(
                self.db_path,
                issue_id=issue_id,
                author_agent_id=None,
                body=(
                    f"⚙ Sistema: issue de criticidad {criticality} — el reviewer compartía proveedor "
                    f"con el engineer ({engineer_provider}) y el sesgo de auto-preferencia del juez "
                    f"debilitaría el veredicto. Reviewer re-apuntado a `{selection['adapter_type']}` "
                    f"({selection.get('adapter_profile_id')})."
                ),
                metadata={"source": "cross_provider_review"},
            )
        logger.info(
            "cross_provider_review: issue %s reviewer moved off %s to %s",
            issue_id, engineer_provider, selection.get("adapter_profile_id"),
        )
        return True

    def _attempt_model_escalation(
        self,
        *,
        issue_id: str,
        agent_id: str,
        run_id: str,
        agent_role: str,
        liveness_reason: str,
    ) -> bool:
        """Peldaño 1 de la cascada de calidad: escalar al modelo senior del
        MISMO perfil antes de cambiar de canal.

        Patrón FrugalGPT (skill multi-model-orchestration): el barato intenta,
        y solo se escala capacidad cuando falla — aquí "falla" = agotó sus
        continuaciones sin evidencia de workspace. Una vez por issue; si el
        agente ya corre el modelo tope del perfil (o no hay perfil), devuelve
        False y el caller pasa al peldaño 2 (_attempt_adapter_recovery, cambio
        de canal).
        """
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE action = 'issue.model_escalation' AND target_id = ?",
                (issue_id,),
            ).fetchone()
            if row and int(row[0]) > 0:
                return False
            agent_row = conn.execute(
                "SELECT adapter_type, adapter_config_json FROM agents WHERE id = ?",
                (agent_id,),
            ).fetchone()
        if agent_row is None:
            return False
        config = _decode_json(str(agent_row["adapter_config_json"] or "{}"))
        profile_id = str(config.get("profile_id") or "").strip()
        current_model = str(config.get("model") or "").strip()
        if not profile_id:
            return False
        from aiteam.project_adapters import senior_model_for_profile  # noqa: PLC0415
        senior = str(senior_model_for_profile(profile_id) or "").strip()
        if not senior or senior == current_model:
            return False

        new_config = {**config, "model": senior}
        with contextlib.closing(_connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE agents SET adapter_config_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(new_config, ensure_ascii=False, sort_keys=True), agent_id),
            )
        update_issue(self.db_path, issue_id=issue_id, status="todo")
        log_activity(
            self.db_path,
            action="issue.model_escalation",
            target_type="issue",
            target_id=issue_id,
            actor_agent_id=agent_id,
            run_id=run_id,
            payload={
                "profile_id": profile_id,
                "from_model": current_model or None,
                "to_model": senior,
                "liveness_reason": liveness_reason,
            },
        )
        with contextlib.suppress(Exception):
            create_comment(
                self.db_path,
                issue_id=issue_id,
                author_agent_id=None,
                body=(
                    f"⚙ Sistema: `{current_model or 'el modelo asignado'}` agotó sus intentos sin "
                    f"producir cambios verificables ({liveness_reason}). Escalado al modelo senior "
                    f"del mismo perfil (`{senior}`) y la issue vuelve a `todo` — si también falla, "
                    "el siguiente paso será cambiar de canal."
                ),
                metadata={"source": "model_escalation"},
            )
        enqueue_wakeup(
            self.db_path,
            agent_id=agent_id,
            source="model_escalation",
            reason="assignment",
            trigger_detail=f"model_escalation:{current_model or '?'}->{senior}",
            payload={"issue_id": issue_id, "wake_reason": "assignment"},
            idempotency_key=f"model_escalation:{issue_id}",
        )
        logger.info(
            "model_escalation: issue %s escalated %s -> %s after %s",
            issue_id, current_model or "?", senior, liveness_reason,
        )
        return True

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
        from aiteam.hiring_economics import demoted_profile_ids  # noqa: PLC0415
        selection = choose_adapter_for_role(
            agent_role, None, candidates,
            demoted_profile_ids=demoted_profile_ids(self.db_path, candidates),
        )
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

    # ── Issue state machine per role (datos en aiteam.policies) ─────────────
    from aiteam.policies import (
        LEAD_TIER_ROLES as _LEAD_TIER_ROLES,
        TERMINAL_ISSUE_STATUSES as _TERMINAL_ISSUE_STATUSES,
        WORKER_ALLOWED_TARGET_STATUSES as _WORKER_ALLOWED_TARGET_STATUSES,
    )

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

    from aiteam.policies import (
        DELEGATION_CHURN_ROLES as _CHURN_ROLES,
        DELEGATION_CHURN_WINDOW_HOURS as _CHURN_WINDOW_HOURS,
    )

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

    _REREVIEW_ROLES = frozenset({"reviewer", "code_reviewer", "qa"})

    def _preflight_skip_reason(
        self,
        *,
        issue_id: str,
        agent_role: str,
        ctx: dict[str, Any],
        run_id: str,
        agent_id: str,
        workspace_root: Path,
    ) -> str | None:
        """Return an error_code when this wake should be skipped, else None."""
        role_key = str(agent_role or "").strip().lower()
        is_lead = role_key in _LEAD_TIER_ROLES_P
        wake_reason = str(ctx.get("wake_reason") or "")

        if issue_id and not is_lead:
            issue = get_issue(self.db_path, issue_id=issue_id)
            if issue is not None and str(issue.get("status") or "") in _TERMINAL_ISSUE_STATUSES_P:
                logger.info("preflight: issue %s is terminal — skipping wake for %s", issue_id, agent_id)
                return "issue_terminal"
            if wake_reason != "interaction_resolved" and self._pending_product_decision(issue_id):
                logger.info(
                    "preflight: product decision pending on root of %s — pausing %s until the user answers",
                    issue_id, agent_id,
                )
                return "awaiting_user_decision"

        if self._rereview_capped(issue_id=issue_id, agent_role=agent_role, ctx=ctx, run_id=run_id, agent_id=agent_id):
            return "rereview_limit_reached"

        if (
            issue_id
            and role_key in self._REREVIEW_ROLES
            and wake_reason not in {"interaction_resolved", "liveness_continuation", "quorum_report_retry"}
            and self._review_evidence_unchanged(issue_id=issue_id, workspace_root=workspace_root, current_run_id=run_id)
        ):
            logger.info(
                "preflight: workspace unchanged since last completed review of %s — skipping duplicate review",
                issue_id,
            )
            return "review_evidence_unchanged"
        return None

    def _pending_product_decision(self, issue_id: str) -> bool:
        """True when the subtree's ROOT has a pending PRODUCT interaction.

        While the user is being asked a product question (cycle close, scope,
        which option to take), fix/review runs under that root only feed the
        loop the question is about. Operational escalations (breakers) do NOT
        pause work — they resolve via autonomy or a quick accept.
        """
        try:
            root_id = self._root_issue_id(issue_id)
        except Exception:
            return False
        with contextlib.closing(_connect(self.db_path)) as conn:
            rows = conn.execute(
                """
                SELECT payload_json FROM issue_thread_interactions
                WHERE issue_id = ? AND status = 'pending'
                """,
                (root_id,),
            ).fetchall()
        for row in rows:
            payload = _decode_json(row["payload_json"])
            reason = str(payload.get("reason") or payload.get("escalation_reason") or "")
            if _operational_interaction_default(reason) is None:
                return True
        return False

    def _workspace_digest_unchanged(
        self, *, issue_id: str, agent_id: str, run_id: str, workspace_root: Path
    ) -> bool:
        """True si el workspace es idéntico al de la última run COMPLETADA de
        este agente sobre esta issue (P5, dieta de contexto).

        Registra siempre el digest de ESTA run (activity
        ``workspace.context_digest``) para que la siguiente pueda comparar.
        Una run previa fallida re-envía cuerpos completos: el agente pudo no
        haber llegado a leerlos.
        """
        snapshot = snapshot_workspace(workspace_root)
        digest = hashlib.sha256(
            json.dumps(sorted(snapshot.items()), sort_keys=True).encode("utf-8")
        ).hexdigest()
        unchanged = False
        with contextlib.closing(_connect(self.db_path)) as conn:
            prev = conn.execute(
                """
                SELECT a.payload_json, r.status AS run_status
                FROM activity_log a
                LEFT JOIN runs r ON r.id = a.run_id
                WHERE a.action = 'workspace.context_digest'
                  AND a.target_id = ?
                  AND a.actor_agent_id = ?
                  AND a.run_id != ?
                ORDER BY a.created_at DESC, a.rowid DESC
                LIMIT 1
                """,
                (issue_id, agent_id, run_id),
            ).fetchone()
        if prev is not None and str(prev["run_status"] or "") == "completed":
            prev_digest = str(_decode_json(str(prev["payload_json"] or "{}")).get("digest") or "")
            unchanged = bool(prev_digest) and prev_digest == digest
        with contextlib.suppress(Exception):
            log_activity(
                self.db_path,
                action="workspace.context_digest",
                target_type="issue",
                target_id=issue_id,
                actor_agent_id=agent_id,
                run_id=run_id,
                payload={"digest": digest},
            )
        return unchanged

    @staticmethod
    def _workspace_digest(workspace_root: Path) -> str:
        snapshot = snapshot_workspace(workspace_root)
        return hashlib.sha256(
            json.dumps(sorted(snapshot.items()), sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _failed_test_runner_workspace_unchanged(
        self, *, issue_id: str, workspace_root: Path
    ) -> bool:
        """Whether the latest trusted test report failed on this exact tree."""
        current_digest = self._workspace_digest(workspace_root)
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT ar.result, a.payload_json
                FROM agent_reports ar
                LEFT JOIN activity_log a
                  ON a.run_id = ar.run_id
                 AND a.action = 'workspace.context_digest'
                 AND a.target_id = ar.issue_id
                WHERE ar.issue_id = ?
                  AND ar.valid = 1
                  AND ar.is_assignee = 1
                  AND ar.agent_role = 'test_runner'
                ORDER BY ar.created_at DESC, ar.rowid DESC, a.rowid DESC
                LIMIT 1
                """,
                (issue_id,),
            ).fetchone()
        if row is None or str(row["result"] or "").lower() not in {"failed", "blocked"}:
            return False
        prior_digest = str(_decode_json(str(row["payload_json"] or "{}")).get("digest") or "")
        return bool(prior_digest) and prior_digest == current_digest

    def _review_evidence_unchanged(self, *, issue_id: str, workspace_root: Path, current_run_id: str) -> bool:
        """True when the workspace fingerprint matches the one recorded at the
        reviewer's last run on this issue AND that run completed — same files,
        same verdict, nothing to add.

        The fingerprint is recorded (activity_log ``review.evidence``) for every
        review run that is allowed to execute; nothing is recorded for skips, so
        a workspace change always re-enables exactly one review round. A failed
        last run always re-enables the retry regardless of the fingerprint.
        """
        try:
            snapshot = snapshot_workspace(workspace_root)
        except Exception:
            return False
        digest = hashlib.sha256(
            json.dumps(sorted(snapshot.items()), sort_keys=True).encode("utf-8")
        ).hexdigest()
        with contextlib.closing(_connect(self.db_path)) as conn:
            prev = conn.execute(
                """
                SELECT payload_json FROM activity_log
                WHERE action = 'review.evidence' AND target_id = ?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (issue_id,),
            ).fetchone()
            # Most recent run BEFORE the one being preflighted (which already
            # exists in the table — exclude it by id, its status varies).
            last_run = conn.execute(
                """
                SELECT status FROM runs
                WHERE issue_id = ? AND id != ?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (issue_id, current_run_id),
            ).fetchone()
        prev_digest = str(_decode_json(prev["payload_json"]).get("fingerprint") or "") if prev else ""
        last_status = str(last_run["status"]) if last_run else ""
        if prev_digest == digest and last_status == "completed":
            return True
        # Different (or first) evidence, or a failed last attempt — record the
        # fingerprint and let the run execute.
        log_activity(
            self.db_path,
            action="review.evidence",
            target_type="issue",
            target_id=issue_id,
            payload={"fingerprint": digest, "files": len(snapshot)},
        )
        return False

    def _rereview_capped(
        self, *, issue_id: str, agent_role: str, ctx: dict[str, Any], run_id: str, agent_id: str
    ) -> bool:
        """True when this reviewer/QA wake should escalate instead of running.

        Trips after AITEAM_REREVIEW_LIMIT completed runs on the same issue.
        The escalation interaction is idempotent per (issue, round count), so
        an accepted round raises the count and arms a fresh escalation for
        the next burst — repeated bursts keep reaching the user (or the
        autonomy policy) instead of silently freezing reviews.
        """
        if not issue_id or str(agent_role or "").strip().lower() not in self._REREVIEW_ROLES:
            return False
        if str(ctx.get("wake_reason") or "") == "interaction_resolved":
            return False  # the user (or autonomy) just authorised this round
        limit = _rereview_limit()
        if limit <= 0:
            return False
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE issue_id = ? AND status = 'completed'",
                (issue_id,),
            ).fetchone()
        completed = int(row[0]) if row else 0
        if completed < limit:
            return False
        # One pending re-review card at a time: N parallel review issues over
        # the same workspace tripping together must not flood the user with N
        # identical cards (capa-2 got 3 at once). The run is still skipped.
        with contextlib.closing(_connect(self.db_path)) as conn:
            pending_same = conn.execute(
                """
                SELECT COUNT(*) FROM issue_thread_interactions
                WHERE status = 'pending'
                  AND payload_json LIKE '%"reason": "rereview_limit_reached"%'
                """
            ).fetchone()
        if pending_same and int(pending_same[0] or 0) > 0:
            log_activity(
                self.db_path,
                action="rereview.capped",
                target_type="issue",
                target_id=issue_id,
                actor_agent_id=agent_id,
                run_id=run_id,
                payload={"completed_runs": completed, "limit": limit, "agent_role": agent_role, "deduped": True},
            )
            return True
        try:
            create_interaction(
                self.db_path,
                issue_id=issue_id,
                kind="request_confirmation",
                continuation_policy="wake_assignee",
                payload={
                    "version": 1,
                    "reason": "rereview_limit_reached",
                    "completed_runs": completed,
                    "limit": limit,
                },
                source_run_id=run_id,
                created_by_agent_id=agent_id,
                idempotency_key=f"rereview:{issue_id}:{completed}",
                title=f"Freno de re-revisión — {completed} runs de review sobre la misma issue",
                summary=(
                    f"El reviewer ya ejecutó {completed} runs sobre esta issue (límite {limit}) "
                    "sin que cambie su evidencia. Acepta para autorizar una ronda más; "
                    "rechaza para dejar la issue como está y que el Lead decida otra vía."
                ),
            )
            log_activity(
                self.db_path,
                action="rereview.capped",
                target_type="issue",
                target_id=issue_id,
                actor_agent_id=agent_id,
                run_id=run_id,
                payload={"completed_runs": completed, "limit": limit, "agent_role": agent_role},
            )
            logger.warning(
                "rereview.capped: issue %s already has %d completed review runs (limit %d) — escalating",
                issue_id, completed, limit,
            )
        except Exception:
            logger.warning("rereview gate: failed to create escalation for %s", issue_id, exc_info=True)
        return True

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

    def _workspace_has_test_signals(self) -> bool:
        """Return True when the workspace contains files that imply a test suite."""
        try:
            root = workspace_root_for_db(self.db_path)
            if not root.exists() or not root.is_dir():
                return False
            marker_files = {
                "pytest.ini",
                "tox.ini",
                "noxfile.py",
                "package.json",
                "vitest.config.js",
                "vitest.config.ts",
                "jest.config.js",
                "jest.config.ts",
                "playwright.config.js",
                "playwright.config.ts",
            }
            for rel_path in snapshot_workspace(root):
                path_key = str(rel_path).replace("\\", "/")
                name = path_key.rsplit("/", 1)[-1]
                if path_key.startswith(("tests/", "test/")) and name:
                    return True
                if name in marker_files:
                    return True
                if name.startswith("test_") and name.endswith(".py"):
                    return True
                if name.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts", "_test.py")):
                    return True
                if name == "pyproject.toml":
                    try:
                        content = (root / rel_path).read_text(encoding="utf-8", errors="ignore").lower()
                        if "[tool.pytest" in content or "pytest" in content:
                            return True
                    except Exception:
                        return True
        except Exception:
            logger.warning("test signal scan failed", exc_info=True)
        return False

    def _test_runner_gate_line(self, rows: list[dict[str, Any]]) -> str | None:
        if not self._workspace_has_test_signals():
            return None

        runner_reports: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("role") or "").strip().lower() != "test_runner":
                continue
            report = row.get("last_agent_report") or {}
            if report:
                runner_reports.append(report)

        for report in runner_reports:
            result = str(report.get("result") or "").strip().lower()
            evidence = str(report.get("evidence") or "").strip().lower()
            exit_code = str(report.get("exit_code") or report.get("exit code") or "").strip()
            if exit_code == "0" or (result in {"passed", "pass", "approved", "done"} and "exit 0" in evidence):
                return "Test runner: suite detectada y report aprobatorio con exit 0."

        if runner_reports:
            return (
                "BLOQUEANTE: hay tests en el workspace, pero ningun test_runner reporta exit 0. "
                "No cerrar hasta ejecutar la suite y registrar evidencia."
            )
        return (
            "BLOQUEANTE: hay tests en el workspace, pero no existe report de test_runner. "
            "No cerrar hasta delegar ejecucion de tests y obtener exit 0."
        )

    def _quality_close_denied(self, *, issue_id: str) -> str | None:
        issue = get_issue(self.db_path, issue_id=issue_id) or {}
        metadata = _decode_json(issue.get("metadata_json") or "{}")
        if normalize_run_profile(metadata.get("profile") or "") == LEAD_QUORUM:
            with contextlib.closing(_connect(self.db_path)) as conn:
                session = conn.execute(
                    "SELECT status FROM quorum_sessions WHERE issue_id=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (issue_id,),
                ).fetchone()
            if session is None:
                return "lead_quorum_plan_and_session_required"
            if str(session["status"] or "") != "accepted":
                return "lead_quorum_accepted_plan_required"
        try:
            rows = self._child_issue_rows(issue_id)
        except Exception:
            return None
        test_gate = self._test_runner_gate_line(rows)
        if test_gate and test_gate.startswith("BLOQUEANTE:"):
            issue = get_issue(self.db_path, issue_id=issue_id) or {}
            metadata = _decode_json(issue.get("metadata_json") or "{}")
            if normalize_run_profile(metadata.get("profile") or "") == SOLO_LEAD:
                if self._solo_lead_machine_verification_passed(issue_id=issue_id):
                    return None
            if self._runtime_verification_waived(issue_id):
                return None
            return "test_runner_exit_zero_required"
        return None

    def _solo_lead_machine_verification_passed(self, *, issue_id: str) -> bool:
        """Verify a solo agent's tests without inventing a second agent.

        `solo_lead` owns implementation and verification, but the close gate
        must still trust a subprocess receipt rather than model narration.
        Successful receipts are cached in activity_log to avoid rerunning the
        suite when the same close action is retried.
        """
        with contextlib.closing(_connect(self.db_path)) as conn:
            cached = conn.execute(
                "SELECT 1 FROM activity_log WHERE action='solo_lead.verification_passed' "
                "AND target_id=? LIMIT 1",
                (issue_id,),
            ).fetchone()
        if cached:
            return True
        root = workspace_root_for_db(self.db_path)
        cmd, suite_kind = self._resolve_test_command(root)
        if cmd is None:
            return False
        timeout_sec = int(os.environ.get("AITEAM_TEST_RUNNER_TIMEOUT_SEC", "600") or "600")
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                env={**os.environ},
            )
        except Exception as exc:
            log_activity(
                self.db_path,
                action="solo_lead.verification_failed",
                target_type="issue",
                target_id=issue_id,
                payload={"suite_kind": suite_kind, "error": str(exc)},
            )
            return False
        tail = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()[-2000:]
        action = "solo_lead.verification_passed" if proc.returncode == 0 else "solo_lead.verification_failed"
        log_activity(
            self.db_path,
            action=action,
            target_type="issue",
            target_id=issue_id,
            payload={
                "suite_kind": suite_kind,
                "command": cmd,
                "exit_code": int(proc.returncode),
                "output_tail": tail,
            },
        )
        return proc.returncode == 0

    # Avisos correctivos por (issue, reason) antes de escalar al usuario. Dos
    # es deliberado: el primer aviso corrige un descuido del Lead; si el
    # segundo tampoco produce evidencia, es que el equipo NO PUEDE (entorno
    # sin runtime, rol inexistente) y seguir despertándolo solo quema tokens.
    _GATE_NOTIFY_CAP = 2

    def _notify_quality_gate_denial(
        self, *, issue_id: str, agent_id: str, run_id: str, reason: str
    ) -> None:
        """Hace audible una denegación del quality gate y garantiza continuación.

        1..N<cap: comentario correctivo de sistema (con marker dedupeable) +
        re-wake del asignado para que delegue un test_runner o escale.
        >=cap: el bucle de corrección no converge — auto-escala una
        request_confirmation al usuario cuya aceptación dispensa el gate
        (contrato RUNTIME_VERIFICATION_WAIVER_REASON). Sin más wakes: el
        proyecto queda esperando una decisión humana, no en deadlock mudo.
        """
        marker = f"[gate:{reason}]"
        is_quorum_gate = reason.startswith("lead_quorum_")
        try:
            with contextlib.closing(_connect(self.db_path)) as conn:
                prior_notices = conn.execute(
                    "SELECT COUNT(*) FROM issue_comments"
                    " WHERE issue_id = ? AND author_user_id = 'system' AND body LIKE ?",
                    (issue_id, f"%{marker}%"),
                ).fetchone()[0]
                last_body = conn.execute(
                    "SELECT body FROM issue_comments WHERE issue_id = ?"
                    " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (issue_id,),
                ).fetchone()
        except Exception:
            logger.warning("gate denial notify: comment scan failed for %s", issue_id, exc_info=True)
            return

        if prior_notices >= self._GATE_NOTIFY_CAP:
            with contextlib.suppress(Exception):
                create_interaction(
                    self.db_path,
                    issue_id=issue_id,
                    kind="request_confirmation",
                    payload={
                        "parent_issue_id": issue_id,
                        "reason": "lead_quorum_plan_missing" if is_quorum_gate else _RUNTIME_VERIFICATION_WAIVER_REASON,
                        "gate_reason": reason,
                        "version": 1,
                    },
                    continuation_policy="wake_assignee",
                    idempotency_key=f"gate-escalation:{reason}:{issue_id}",
                    source_run_id=run_id,
                    title=(
                        "El Lead no materializa el Plan A requerido"
                        if is_quorum_gate else "Gate de tests bloquea el cierre y el equipo no produce evidencia"
                    ),
                    summary=((
                        "El perfil lead_quorum requiere un Plan A durable antes de lanzar auditores. "
                        f"Tras {prior_notices} avisos el Lead no emitió update_plan. Revisa el adapter "
                        "o reintenta la planificación; no existe un plan aceptable que dispensar."
                    ) if is_quorum_gate else (
                        "El quality gate exige una suite de tests ejecutada con exit 0, pero tras "
                        f"{prior_notices} avisos correctivos ningún test_runner ha registrado esa "
                        "evidencia (entorno posiblemente incapaz de ejecutar los tests). "
                        "¿Aceptas cerrar dispensando la verificación runtime, o rechazas y "
                        "esperas a que el entorno pueda ejecutar la suite?"
                    )),
                )
            return

        if not (last_body and marker in str(last_body[0] or "")):
            with contextlib.suppress(Exception):
                create_comment(
                    self.db_path,
                    issue_id=issue_id,
                    body=((
                        f"⚙ Sistema {marker}: cierre DENEGADO — `lead_quorum` exige persistir Plan A "
                        "mediante `update_plan` y completar dos auditorías independientes antes de "
                        "aceptar Plan B. En la próxima run emite `update_plan`; no cierres la issue, "
                        "no delegues implementación y no pidas dispensar tests."
                    ) if is_quorum_gate else (
                        f"⚙ Sistema {marker}: cierre DENEGADO por el quality gate — hay tests en el "
                        "workspace pero ningún test_runner reporta exit 0, así que la issue NO quedó "
                        "cerrada aunque lo hayas intentado. Para desbloquear: (a) delega una sub-issue "
                        "con role=test_runner (ejecuta la suite de forma determinista y registra la "
                        "evidencia), o (b) si el entorno no puede ejecutar los tests, escala una "
                        f"request_confirmation con reason='{_RUNTIME_VERIFICATION_WAIVER_REASON}' en el "
                        "payload: si el usuario la acepta, este gate queda dispensado."
                    )),
                    author_user_id="system",
                )
        with contextlib.suppress(Exception):
            enqueue_wakeup(
                self.db_path,
                agent_id=agent_id,
                source="quality_gate",
                reason="quality_gate_denied",
                payload={
                    "issue_id": issue_id,
                    "wake_reason": "quality_gate_denied",
                    "gate_reason": reason,
                },
                idempotency_key=f"gate-denied:{reason}:{issue_id}:{prior_notices}",
            )

    def _runtime_verification_waived(self, issue_id: str) -> bool:
        """True cuando el usuario aceptó explícitamente cerrar sin verificación
        runtime de tests para esta issue.

        La aceptación llega como ``request_confirmation`` con
        ``payload.reason == RUNTIME_VERIFICATION_WAIVER_REASON`` — el contrato
        definido en policies. Sin este check, el gate contradecía en silencio
        una decisión vinculante del usuario y la issue quedaba en deadlock.
        """
        try:
            rows = list_interactions(self.db_path, issue_id=issue_id)
        except Exception:
            return False
        for row in rows:
            if str(row.get("kind") or "") != "request_confirmation":
                continue
            if str(row.get("status") or "") != "accepted":
                continue
            payload = _decode_json(str(row.get("payload_json") or "{}"))
            reason = str(payload.get("reason") or "").strip()
            if reason == _RUNTIME_VERIFICATION_WAIVER_REASON:
                with contextlib.suppress(Exception):
                    log_activity(
                        self.db_path,
                        action="quality_gate.waived",
                        target_type="issue",
                        target_id=issue_id,
                        payload={
                            "reason": "test_runner_exit_zero_required",
                            "waived_by_interaction": str(row.get("id") or ""),
                        },
                    )
                return True
        return False

    def _acceptance_criteria_lines(self, rows: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for row in rows:
            meta = _decode_json(row.get("metadata_json") or "{}")
            criteria = [str(c).strip() for c in (meta.get("acceptance_criteria") or []) if str(c).strip()]
            if not criteria:
                continue
            report = row.get("last_agent_report") or {}
            evidence = str(report.get("evidence") or "").strip()
            evidence_lc = evidence.lower()
            covered = sum(1 for criterion in criteria if criterion.lower() in evidence_lc)
            lines.append(
                f"Criterios de aceptacion \"{str(row.get('title') or '')[:40]}\": "
                f"{covered}/{len(criteria)} con evidencia especifica del assignee"
            )
            for criterion in criteria[:8]:
                status = "cubierto" if criterion.lower() in evidence_lc else "pendiente"
                lines.append(f"Criterio {status}: {criterion[:140]}")
            if len(criteria) > 8:
                lines.append(f"{len(criteria) - 8} criterios adicionales no listados")
            if evidence and covered < len(criteria):
                lines.append(f"Evidencia general del assignee: {evidence[:160]}")
            elif not evidence:
                lines.append("SIN evidencia reportada por el assignee")
        return lines

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
        lines.extend(self._acceptance_criteria_lines(rows))
        sod_line = self._separation_of_duties_line(rows)
        if sod_line:
            lines.append(sod_line)
        test_gate_line = self._test_runner_gate_line(rows)
        if test_gate_line:
            if test_gate_line.startswith("BLOQUEANTE:") and self._runtime_verification_waived(issue_id):
                test_gate_line = (
                    "Test runner: verificación runtime dispensada por el usuario "
                    f"({_RUNTIME_VERIFICATION_WAIVER_REASON} aceptada) — el gate no bloquea el cierre."
                )
            lines.append(test_gate_line)
        # Suite independiente: señala si la aceptación la escribió un agente
        # distinto del implementador (la del engineer confirma lo que el código
        # hace; la del test_designer, lo que la spec pide).
        _has_engineering = any(
            str(r.get("role") or "").strip().lower() in {"engineer", "software_engineer"} for r in rows
        )
        if _has_engineering:
            _designer_rows = [
                r for r in rows if str(r.get("role") or "").strip().lower() == "test_designer"
            ]
            if _designer_rows:
                _designer_report = _designer_rows[0].get("last_agent_report") or {}
                _d_status = str(_designer_rows[0].get("status") or "")
                lines.append(
                    "Tests independientes (test_designer): "
                    + (f"report {_designer_report.get('result')}" if _designer_report else "sin report")
                    + f", issue {_d_status}"
                )
            else:
                lines.append(
                    "Tests independientes: NO hay suite del test_designer — la aceptación "
                    "solo la verificó quien implementó."
                )
        lines.extend(self._workspace_verification_lines())
        cost_line = self._cycle_cost_line(issue_id)
        if cost_line:
            lines.append(cost_line)
        return "\n".join(f"- {line}" for line in lines)

    def _separation_of_duties_line(self, rows: list[dict[str, Any]]) -> str | None:
        """Signal when maker and checker share the same provider.

        A reviewer running on the same provider/model family as the engineer
        it audits shares that model's blind spots — the review is weaker than
        it looks. Signal only (no blocking); quorum with a different provider
        is the recommended remedy for critical closes.
        """
        try:
            from aiteam.hiring_economics import provider_and_model_for
            engineer_roles = {"engineer", "software_engineer"}
            reviewer_roles = {"reviewer", "code_reviewer"}
            providers: dict[str, set[str]] = {"engineer": set(), "reviewer": set()}
            for row in rows:
                role = str(row.get("role") or "").strip().lower()
                bucket = "engineer" if role in engineer_roles else "reviewer" if role in reviewer_roles else None
                if bucket is None:
                    continue
                agent = self._agent_info(str(row.get("assignee_agent_id") or ""))
                if not agent:
                    continue
                provider, _model = provider_and_model_for(
                    str(agent.get("adapter_type") or ""),
                    _decode_json(agent.get("adapter_config_json") or "{}"),
                )
                if provider:
                    providers[bucket].add(provider)
            shared = providers["engineer"] & providers["reviewer"]
            if shared:
                return (
                    f"Separation of duties: engineer y reviewer comparten proveedor ({', '.join(sorted(shared))}); "
                    "la revision hereda los puntos ciegos del mismo modelo. Para cierres criticos, "
                    "considera un quorum auditor con proveedor distinto."
                )
        except Exception:
            logger.warning("separation-of-duties check failed", exc_info=True)
        return None

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
                        "1. Lee exclusivamente payload.context_curation_target.\n"
                        "2. Produce el bloque causal y persístelo con la op estructurada "
                        "append_context_summary, copiando sus IDs y char_count_original exactos.\n"
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
                    "(subscription_cli, Codex, Antigravity u Ollama)."
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

    def _daily_cost_cap_gate(self, *, run_id: str, issue_id: str, agent_id: str) -> str:
        """Techo duro de gasto real por día natural para todo el proyecto.

        Suma cost_events del día UTC actual; si alcanza el cap, escala UNA vez
        (request_confirmation con idempotency por día) y bloquea. Accept =
        override para ese día; reject = pausa; pending = bloqueado. El canal
        flat-rate registra 0 céntimos, así que solo topa gasto por-token real.
        """
        if not issue_id:
            return "allowed"
        cap = _daily_cost_cap_cents()
        if cap <= 0:
            return "allowed"
        from datetime import datetime, timezone

        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with contextlib.closing(_connect(self.db_path)) as conn:
            spent_row = conn.execute(
                "SELECT COALESCE(SUM(cost_cents), 0) FROM cost_events WHERE substr(created_at, 1, 10) = ?",
                (day,),
            ).fetchone()
        spent = int(spent_row[0] or 0) if spent_row else 0
        if spent < cap:
            return "allowed"

        root_id = self._root_issue_id(issue_id)
        idem = f"daily_cost_cap:{day}:{root_id}"
        interaction = self._interaction_by_idempotency(issue_id=root_id, idempotency_key=idem)
        if interaction is None:
            create_interaction(
                self.db_path,
                issue_id=root_id,
                kind="request_confirmation",
                continuation_policy="wake_assignee",
                payload={
                    "version": 1,
                    "reason": "daily_cost_cap_reached",
                    "day": day,
                    "spent_cents": spent,
                    "cap_cents": cap,
                    "run_id": run_id,
                },
                source_run_id=run_id,
                created_by_agent_id=agent_id,
                title="Cap de coste diario alcanzado",
                summary=(
                    f"El proyecto lleva {spent} céntimos de gasto real hoy ({day}), "
                    f"al o por encima del cap de {cap}. ¿Autorizas seguir gastando hoy "
                    "(accept) o pausas hasta mañana / subir el cap (reject)?"
                ),
                idempotency_key=idem,
            )
            return "blocked"
        status = str(interaction.get("status") or "").strip().lower()
        if status == "accepted":
            return "allowed"
        if status == "rejected":
            return "rejected"
        return "blocked"

    def _interaction_by_idempotency(self, *, issue_id: str, idempotency_key: str) -> dict[str, Any] | None:
        with contextlib.closing(_connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT * FROM issue_thread_interactions WHERE issue_id = ? AND idempotency_key = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (issue_id, idempotency_key),
            ).fetchone()
        return dict(row) if row is not None else None

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


# Tokens that look like file paths inside issue text: "Assets/Scripts/Foo.cs",
# "TestSceneManager.cs.meta", "docs\\plan.md"… Requires a dot-extension so
# plain prose words don't match; version-like tokens ("5.5") are filtered
# out below because their extension has no letters.
_FOCUS_PATH_RE = re.compile(r"[A-Za-z0-9_\-./\\]*[A-Za-z0-9_\-]\.[A-Za-z0-9.]{1,12}\b")

# Unambiguous editing intent in a delegation description — no read-only
# (Tier 3 / NON_EDITING_ROLES) role can satisfy this, see _create_delegated_issue.
_FILE_EDIT_SIGNAL_RE = re.compile(
    # Señal explícita ("Files to modify: ...") o intención de arreglo: la run
    # CLI Textos delegó "Fix: corregir stats..." a un file_scout (solo
    # lectura), que cerró done sin tocar un archivo, y el sistema quemó 4
    # rondas de review contra un fix inexistente hasta el freno. Un verbo de
    # arreglo/implementación es intención de edición aunque no liste archivos.
    r"files? to modify|archivos? a modificar|file to change|archivo a cambiar"
    r"|\bfix\b|\bfixe?ar\b|\barregla[r]?\b|\bcorr?ige\b|\bcorregir\b"
    r"|\bimplementa[r]?\b|\bimplement\b|\brefactor\w*\b|\breescrib\w*\b|\brewrite\b",
    re.IGNORECASE,
)


def _extract_focus_paths(texts: Iterable[str]) -> frozenset[str]:
    """File-path-looking tokens mentioned in issue text, normalised for matching.

    Used to give the files an issue is actually ABOUT top priority in the
    workspace_files content budget (see :func:`_read_workspace_files`).
    """
    tokens: set[str] = set()
    for text in texts:
        for match in _FOCUS_PATH_RE.findall(str(text or "")):
            token = match.replace("\\", "/").lstrip("./").rstrip(".").lower()
            if not token or "." not in token:
                continue
            ext = token.rsplit(".", 1)[-1]
            if not re.search(r"[a-z]", ext):
                continue  # "5.5", "v1.2" — versions, not files
            tokens.add(token)
    return frozenset(tokens)


def _read_workspace_files(
    workspace_root: Path,
    *,
    max_per_file_bytes: int | None = None,
    max_total_bytes: int | None = None,
    focus_paths: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Return ``{path, size_bytes, content?}`` for every workspace file.

    Injected into the wake payload for reviewer/QA/scout runs so they review
    real files instead of hallucinating from the engineer's description.

    Every non-binary, non-hidden file ALWAYS appears in the result (path +
    size) so existence questions ("is there a README?") are answerable. File
    *content* is included in priority order until *max_total_bytes* is
    reached: first the files the issue explicitly mentions (*focus_paths* —
    a reviewer must never receive the files under review as "content
    omitted"), then READMEs, docs, manifests, scenes, then sources. Files
    past the budget still appear with a "content omitted" marker rather than
    being dropped. Dropping them (the old behaviour) made alphabetically-late
    files like README.md invisible, which trapped reviewer↔engineer in an
    endless "README missing" fix loop.
    """
    if max_per_file_bytes is None:
        max_per_file_bytes = _ws_file_max_bytes()
    if max_total_bytes is None:
        max_total_bytes = _ws_files_budget_bytes()
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

    def _is_focused(rel: Path) -> bool:
        if not focus_paths:
            return False
        rel_posix = str(rel).replace("\\", "/").lower()
        name = rel.name.lower()
        return any(
            rel_posix == token or rel_posix.endswith("/" + token) or name == token
            for token in focus_paths
        )

    # Focused files first (the issue names them explicitly), then priority
    # tiers (so key docs get content), then alphabetical within a tier.
    candidates.sort(
        key=lambda item: (
            -1 if _is_focused(item[0]) else _workspace_file_priority(item[0]),
            str(item[0]),
        )
    )

    result: list[dict[str, Any]] = []
    total_bytes = 0
    for rel, raw in candidates:
        path_str = str(rel).replace("\\", "/")
        item: dict[str, Any] = {"path": path_str, "size_bytes": len(raw)}
        if _is_focused(rel):
            item["focus"] = True
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
            # 'body' es el contrato; 'content' es el alias que los LLM emiten
            # con frecuencia — sin esta tolerancia el op escribía un archivo
            # VACÍO en silencio (lo cazó el canario e2e con su propio stub).
            body = str(op.get("body") if op.get("body") is not None else (op.get("content") or ""))
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
    markers = ("objetivo", "objective", "sub-issue", "delegacion", "delegación", "riesgo", "risk", "criterio")
    return sum(1 for marker in markers if marker in text) >= 3
