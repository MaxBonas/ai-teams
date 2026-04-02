import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from api.utils import (
    _require_api_auth_request,
    _workspace_from_request,
    _build_project_continuity_context,
    _detect_notebooklm_status,
    _load_chat_context_curator_insights,
    _peer_consultation_summary_fields,
    _load_chat_rewiring_insights,
    _load_chat_specialist_insights,
    _read_jsonl_records,
    _read_json_payload,
    _read_runtime_tasks_payload,
    _read_runtime_workflow_state,
    _extract_user_message_from_task_description,
    _event_summary,
    PROJECT_ROOT,
    get_current_workspace,
)

from aiteam.dashboard import build_dashboard_payload
from aiteam.cli import build_default_orchestrator
from aiteam.pilot import compute_pilot_metrics
from aiteam.provider_ops import provider_ops_status
from aiteam.autotools import AutoToolIntegrator
from aiteam.types import Complexity, Criticality, Role, RoutingRequest

router = APIRouter()


def _load_chat_workflow_insights(
    runtime_dir: Path,
    task_id: str,
) -> dict[str, object]:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return {}
    workflow_state = _read_runtime_workflow_state(runtime_dir)
    if not isinstance(workflow_state, dict):
        return {}
    entry = workflow_state.get(normalized_task_id, {})
    if not isinstance(entry, dict):
        return {}
    return {
        "phase_evidence_plan": dict(entry.get("phase_evidence_plan", {}) or {}),
        "delegate_batches": list(entry.get("delegate_batches", []) or []),
        "delegate_economics": dict(entry.get("delegate_economics_summary", {}) or {}),
        "lead_run_mode": str(entry.get("lead_run_mode", "") or ""),
        **_load_chat_context_curator_insights(runtime_dir, normalized_task_id),
        **_peer_consultation_summary_fields(runtime_dir, normalized_task_id),
        **_load_chat_rewiring_insights(runtime_dir, normalized_task_id),
        **_load_chat_specialist_insights(runtime_dir, normalized_task_id),
    }


def _load_tool_catalog_index(workspace: Path) -> dict[str, dict[str, object]]:
    catalog_path = workspace / "config" / "tool_sources.catalog.json"
    payload = _read_json_payload(catalog_path, fallback={"tools": []})
    if not isinstance(payload, dict):
        return {}
    items = payload.get("tools", [])
    if not isinstance(items, list):
        return {}
    output: dict[str, dict[str, object]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        if not name:
            continue
        output[name] = dict(item)
    return output


def _build_routing_catalog(runtime_dir: Path) -> dict[str, object]:
    orchestrator = build_default_orchestrator(runtime_dir=runtime_dir, environment="dev")
    router_obj = orchestrator.router
    policy = router_obj.policy
    ops_status = provider_ops_status(runtime_dir)

    adapters = list(router_obj.adapters)
    adapter_rows: list[dict[str, object]] = []
    provider_index: dict[str, dict[str, object]] = {}
    role_order = [role.value for role in Role]

    for adapter in adapters:
        profile = router_obj.model_catalog.get(adapter.name)
        ops_row = dict(ops_status.get(adapter.name, {}) or {})
        try:
            available = bool(adapter.available())
        except Exception:
            available = False
        operational = bool(ops_row.get("operational", available))
        row = {
            "adapter_name": adapter.name,
            "provider": adapter.provider,
            "model": adapter.model,
            "channel": adapter.channel.value,
            "cost_tier": int(adapter.cost_tier),
            "routing_priority": int(adapter.routing_priority),
            "requires_approval": bool(adapter.requires_approval),
            "capabilities": sorted(adapter.capabilities),
            "role_targets": sorted(adapter.role_targets),
            "available": available,
            "operational": operational,
            "tier": getattr(profile, "tier", ""),
            "intelligence_rank": int(getattr(profile, "intelligence_rank", 0) or 0),
            "coding_rank": int(getattr(profile, "coding_rank", 0) or 0),
            "reasoning_rank": int(getattr(profile, "reasoning_rank", 0) or 0),
            "trust_rank": int(getattr(profile, "trust_rank", 0) or 0),
            "notes": str(getattr(profile, "notes", "") or ""),
            "doctor_healthy": bool(ops_row.get("doctor_healthy", False)),
            "smoke_healthy": bool(ops_row.get("smoke_healthy", False)),
            "doctor_details": str(ops_row.get("doctor_details", "") or ""),
            "smoke_details": str(ops_row.get("smoke_details", "") or ""),
        }
        adapter_rows.append(row)
        provider_row = provider_index.setdefault(
            str(adapter.provider),
            {"provider": str(adapter.provider), "adapter_count": 0, "operational_count": 0},
        )
        provider_row["adapter_count"] = int(provider_row.get("adapter_count", 0)) + 1
        if operational:
            provider_row["operational_count"] = int(provider_row.get("operational_count", 0)) + 1

    adapter_rows.sort(
        key=lambda item: (
            str(item.get("provider", "")),
            str(item.get("channel", "")),
            str(item.get("adapter_name", "")),
        )
    )

    role_matrix: list[dict[str, object]] = []
    for role_name in role_order:
        request = RoutingRequest(
            role=Role(role_name),
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            environment="dev",
        )
        eligible = router_obj.eligible_adapters(request)
        eligible_names = {adapter.name for adapter in eligible}
        configured_provider_order = list(policy.role_provider_preferences.get(role_name, []) or [])
        configured_model_order = list(policy.role_model_preferences.get(role_name, []) or [])
        configured_provider_set = {str(item).strip().lower() for item in configured_provider_order if str(item).strip()}
        configured_model_set = {str(item).strip().lower() for item in configured_model_order if str(item).strip()}

        effective_rows: list[dict[str, object]] = []
        for adapter in adapters:
            ops_row = dict(ops_status.get(adapter.name, {}) or {})
            try:
                available = bool(adapter.available())
            except Exception:
                available = False
            operational = bool(ops_row.get("operational", available))
            allowed = adapter.name in eligible_names
            blockers: list[str] = []
            if adapter.role_targets and role_name not in adapter.role_targets:
                blockers.append("role_targets")
            if role_name == Role.TEAM_LEAD.value and not router_obj._team_lead_allowed(adapter):
                blockers.append("team_lead_guard")
            if adapter.requires_approval:
                blockers.append("approval_required")
            if not available:
                blockers.append("adapter_unavailable")
            if getattr(router_obj._profile_for(adapter), "tier", "") == "local":
                if str(os.getenv("AITEAM_PROVIDER_LOCAL_DEGRADED", "0")).strip() == "1":
                    blockers.append("local_degraded")
            effective_rows.append(
                {
                    "adapter_name": adapter.name,
                    "provider": adapter.provider,
                    "model": adapter.model,
                    "channel": adapter.channel.value,
                    "tier": str(getattr(router_obj._profile_for(adapter), "tier", "") or ""),
                    "configured_provider_preferred": adapter.provider.strip().lower() in configured_provider_set,
                    "configured_model_preferred": adapter.model.strip().lower() in configured_model_set,
                    "eligible": allowed,
                    "available": available,
                    "operational": operational,
                    "role_targets": sorted(adapter.role_targets),
                    "blockers": sorted(set(blockers)),
                }
            )

        effective_rows.sort(
            key=lambda item: (
                0 if bool(item.get("eligible")) else 1,
                0 if bool(item.get("configured_provider_preferred")) else 1,
                str(item.get("provider", "")),
                str(item.get("model", "")),
            )
        )
        effective_providers = list(dict.fromkeys([str(adapter.provider) for adapter in eligible]))
        primary = eligible[0] if eligible else None
        role_matrix.append(
            {
                "role": role_name,
                "configured_provider_order": configured_provider_order,
                "configured_model_order": configured_model_order,
                "effective_provider_order": effective_providers,
                "primary": (
                    {
                        "adapter_name": primary.name,
                        "provider": primary.provider,
                        "model": primary.model,
                        "channel": primary.channel.value,
                        "tier": str(getattr(router_obj._profile_for(primary), "tier", "") or ""),
                    }
                    if primary is not None
                    else {}
                ),
                "fallbacks": [
                    {
                        "adapter_name": adapter.name,
                        "provider": adapter.provider,
                        "model": adapter.model,
                        "channel": adapter.channel.value,
                        "tier": str(getattr(router_obj._profile_for(adapter), "tier", "") or ""),
                    }
                    for adapter in eligible[1:6]
                ],
                "adapters": effective_rows,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "role_count": len(role_matrix),
            "provider_count": len(provider_index),
            "adapter_count": len(adapter_rows),
        },
        "providers": sorted(provider_index.values(), key=lambda item: str(item.get("provider", ""))),
        "roles": role_order,
        "adapters": adapter_rows,
        "role_matrix": role_matrix,
    }


def _load_mcp_overview(request: Request) -> dict[str, object]:
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        mgr = _get_mcp_manager(request)
        catalog_index = _load_tool_catalog_index(workspace)
        servers = []
        replacement_counts: dict[str, int] = {}
        fallback_counts: dict[str, int] = {}
        for item in mgr.server_status():
            row = dict(item)
            name = str(row.get("name", "") or "").strip()
            catalog_entry = catalog_index.get(name, {})
            replacements = [
                str(candidate).strip()
                for candidate in list(catalog_entry.get("replacement_candidates", []) or [])
                if str(candidate).strip()
            ]
            fallback_strategy = str(catalog_entry.get("fallback_strategy", "") or "").strip()
            availability_note = str(catalog_entry.get("availability_note", "") or "").strip()
            row["catalog_enabled"] = bool(catalog_entry.get("enabled", row.get("enabled", False)))
            row["catalog_fallback_strategy"] = fallback_strategy
            row["catalog_replacement_candidates"] = replacements
            row["catalog_availability_note"] = availability_note
            if fallback_strategy:
                fallback_counts[fallback_strategy] = int(fallback_counts.get(fallback_strategy, 0)) + 1
            for candidate in replacements:
                replacement_counts[candidate] = int(replacement_counts.get(candidate, 0)) + 1
            servers.append(row)
        opencode = mgr.opencode_bootstrap_status()
        machine_profile = mgr.current_machine_profile()
        total = len(servers)
        enabled = sum(1 for item in servers if bool(item.get("enabled", False)))
        healthy = sum(
            1
            for item in servers
            if str(item.get("health_status", "") or "").strip().lower() == "healthy"
        )
        running = sum(1 for item in servers if bool(item.get("running", False)))
        bootstrapped = sum(
            1
            for item in servers
            if str(item.get("bootstrap_source", "") or "").strip().lower() == "opencode_mcp_list"
        )
        portability_counts: dict[str, int] = {}
        health_categories: dict[str, int] = {}
        health_recommendations: dict[str, int] = {}
        for item in servers:
            category = str(item.get("health_category", "unknown") or "unknown").strip().lower() or "unknown"
            health_categories[category] = int(health_categories.get(category, 0)) + 1
            recommendation = str(item.get("health_recommendation", "inspect_runtime_logs") or "inspect_runtime_logs").strip().lower()
            health_recommendations[recommendation] = int(health_recommendations.get(recommendation, 0)) + 1
            portability = str(item.get("portability_status", "unknown") or "unknown").strip().lower() or "unknown"
            portability_counts[portability] = int(portability_counts.get(portability, 0)) + 1
        return {
            "total_servers": total,
            "enabled_servers": enabled,
            "healthy_servers": healthy,
            "running_servers": running,
            "bootstrapped_servers": bootstrapped,
            "machine_profile": machine_profile,
            "portability_counts": portability_counts,
            "health_categories": health_categories,
            "health_recommendations": health_recommendations,
            "fallback_counts": fallback_counts,
            "replacement_counts": replacement_counts,
            "servers": servers,
            "opencode": opencode,
        }
    except Exception as exc:
        return {"error": str(exc), "servers": []}


def _latest_chat_run_summary(recent_events: list[dict]) -> dict[str, object]:
    def _safe_int(value: object, default: int = 0) -> int:
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = str(value or "").strip()
        if not text:
            return default
        try:
            return int(text)
        except Exception:
            return default

    latest_plan: dict | None = None
    latest_plan_payload: dict[str, object] = {}
    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_plan_created":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        latest_plan = record
        latest_plan_payload = payload
        break

    if latest_plan is None:
        return {}

    task_id = str(latest_plan_payload.get("task_id", "") or "")
    mode = str(latest_plan_payload.get("chat_mode", "") or "")
    round_budget = _safe_int(latest_plan_payload.get("round_budget", 0), 0)
    phase_count = _safe_int(latest_plan_payload.get("phase_count", 0), 0)
    delegated_count = _safe_int(latest_plan_payload.get("delegated_count", 0), 0)
    continuation_requested = bool(latest_plan_payload.get("continuation_requested", False))
    continuation_of = str(latest_plan_payload.get("continuation_of", "") or "")

    rounds_used = 0
    exhausted = False
    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_window_exhausted":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        exhausted = True
        rounds_used = _safe_int(payload.get("rounds_used", 0), 0)
        break

    if rounds_used <= 0 and task_id:
        task_prefix = f"{task_id}::"
        for record in recent_events:
            if str(record.get("event_type", "")) != "task_execution":
                continue
            payload = record.get("payload", {})
            if not isinstance(payload, dict):
                continue
            event_task_id = str(payload.get("task_id", "") or "")
            if not event_task_id.startswith(task_prefix):
                continue
            rounds_used = max(rounds_used, _safe_int(payload.get("execution_round", 0), 0))

    execution_mode = "unknown"
    placeholder_outputs = 0
    evidence_gate_rejected = False
    successful_checks: list[str] = []
    live_mode_required = False
    live_mode_rejected = False
    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_execution_mode_assessed":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        execution_mode = str(payload.get("execution_mode", "unknown") or "unknown")
        placeholder_outputs = _safe_int(payload.get("placeholder_outputs", 0), 0)
        live_mode_required = bool(payload.get("live_mode_required", False))
        break

    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_evidence_gate_rejected":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        evidence_gate_rejected = True
        break

    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_live_mode_required_rejected":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        live_mode_required = True
        live_mode_rejected = True
        break

    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_quality_assessed":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        raw_checks = payload.get("successful_checks", [])
        if isinstance(raw_checks, list):
            successful_checks = sorted(
                {
                    str(item or "").strip()
                    for item in raw_checks
                    if str(item or "").strip()
                }
            )
        break

    # ── Lead autonomous decisions ────────────────────────────────────────────
    advisory_mode = False
    advisory_reason = ""
    auto_extended_rounds = 0
    lead_budget_extended = False
    lead_budget_extension = 0
    peer_consulted_roles: list[str] = []
    peer_consulted_providers: list[str] = []
    peer_diversity_observed = False

    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "lcp_directive_applied":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        directive = str(payload.get("directive", "") or "")
        if directive == "advisory_mode" and not advisory_mode:
            advisory_mode = True
            advisory_reason = str(payload.get("reason", "") or "")
        if directive == "extend_budget_mid_run" and not lead_budget_extended:
            lead_budget_extended = True
            lead_budget_extension = _safe_int(payload.get("extension", 0), 0)

    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_auto_rounds_extended":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        from_b = _safe_int(payload.get("from_round_budget", 0), 0)
        to_b = _safe_int(payload.get("to_round_budget", 0), 0)
        auto_extended_rounds = max(auto_extended_rounds, to_b - from_b)
        break

    task_prefix = f"{task_id}::" if task_id else ""
    consulted_role_set: set[str] = set()
    consulted_provider_set: set[str] = set()
    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "decision_recorded":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        decision_task_id = str(payload.get("task_id", "") or "")
        if decision_task_id != task_id and (not task_prefix or not decision_task_id.startswith(task_prefix)):
            continue
        raw_roles = payload.get("consulted_roles", [])
        if isinstance(raw_roles, list):
            consulted_role_set.update(
                str(item or "").strip()
                for item in raw_roles
                if str(item or "").strip()
            )
        raw_providers = payload.get("consulted_providers", [])
        if isinstance(raw_providers, list):
            consulted_provider_set.update(
                str(item or "").strip()
                for item in raw_providers
                if str(item or "").strip()
            )
        peer_diversity_observed = bool(
            payload.get("peer_diversity_observed", False)
        ) or peer_diversity_observed

    peer_consulted_roles = sorted(consulted_role_set)
    peer_consulted_providers = sorted(consulted_provider_set)
    peer_diversity_observed = peer_diversity_observed or len(peer_consulted_providers) >= 2

    status = "window_exhausted" if exhausted else "completed_or_closed"
    return {
        "task_id": task_id,
        "mode": mode,
        "round_budget": round_budget,
        "rounds_used": rounds_used,
        "phase_count": phase_count,
        "delegated_count": delegated_count,
        "continuation_requested": continuation_requested,
        "continuation_of": continuation_of,
        "status": status,
        "execution_mode": execution_mode,
        "placeholder_outputs": placeholder_outputs,
        "successful_checks": successful_checks,
        "successful_check_count": len(successful_checks),
        "live_mode_required": live_mode_required,
        "live_mode_rejected": live_mode_rejected,
        "evidence_gate_rejected": evidence_gate_rejected,
        "advisory_mode": advisory_mode,
        "advisory_reason": advisory_reason,
        "auto_extended_rounds": auto_extended_rounds,
        "lead_budget_extended": lead_budget_extended,
        "lead_budget_extension": lead_budget_extension,
        "peer_consultation_summary": {
            "consulted_roles": peer_consulted_roles,
            "consulted_providers": peer_consulted_providers,
            "unavailable_roles": [],
            "provider_count": len(peer_consulted_providers),
            "diversity_observed": peer_diversity_observed,
        },
        "ts": str(latest_plan.get("ts", "") or ""),
    }


def _latest_lead_user_summary(runtime_dir: Path, task_id: str) -> dict[str, object]:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return {}

    records = _read_jsonl_records(runtime_dir / "mailbox.jsonl")
    for record in reversed(records):
        if str(record.get("task_id", "") or "") != normalized_task_id:
            continue
        sender = str(record.get("sender", "") or "").strip().lower()
        recipient = str(record.get("recipient", "") or "").strip().lower()
        if sender != "team_lead" or recipient != "user":
            continue
        body = str(record.get("body", "") or "").strip()
        if not body:
            continue
        return {
            "task_id": normalized_task_id,
            "subject": str(record.get("subject", "") or ""),
            "body": body,
            "timestamp": str(record.get("timestamp", "") or ""),
        }
    return {}

@router.get("/api/dashboard")
async def get_dashboard(request: Request):
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        if not runtime_dir.exists():
            raise HTTPException(status_code=404, detail="No AI Team environment found (missing runtime/ folder).")

        # Run orchestrator initialization in a thread to avoid blocking the event loop
        def _load_data():
            orch = build_default_orchestrator(
                runtime_dir=runtime_dir,
                browser_mode="basic",
                environment="dev"
            )
            tasks = orch.taskboard.list_tasks()
            summary = orch.event_logger.summary()
            pilot_metrics = compute_pilot_metrics(tasks, summary)
            
            budget = orch.router.budget_manager
            budget_snapshot = budget.snapshot() if budget is not None else {}
            
            memory_counts = {}
            try:
                memory_counts = {
                    agent: orch.memory.count(agent)
                    for agent in orch.memory.list_agents()
                }
            except Exception:
                pass

            return build_dashboard_payload(
                runtime_dir=runtime_dir,
                tasks=tasks,
                summary=summary,
                pilot_metrics=pilot_metrics,
                budget_snapshot=budget_snapshot,
                memory_counts=memory_counts,
                environment="dev"
            )

        payload = await asyncio.to_thread(_load_data)
        return payload
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Unhandled error in aiteam router")
        return {"error": str(e)}


@router.get("/api/aiteam/state")
async def get_aiteam_state(request: Request, environment: str = "dev"):
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        if not runtime_dir.exists():
            raise HTTPException(status_code=404, detail="No runtime folder found in workspace.")

        def _load_state():
            orch = build_default_orchestrator(
                runtime_dir=runtime_dir,
                browser_mode="basic",
                environment=environment,
            )
            tasks = orch.taskboard.list_tasks()
            summary = orch.event_logger.summary()
            pilot_metrics = compute_pilot_metrics(tasks, summary)
            budget = orch.router.budget_manager
            budget_snapshot = budget.snapshot() if budget is not None else {}
            memory_counts = {
                agent: orch.memory.count(agent)
                for agent in orch.memory.list_agents()
            }
            payload = build_dashboard_payload(
                runtime_dir=runtime_dir,
                tasks=tasks,
                summary=summary,
                pilot_metrics=pilot_metrics,
                budget_snapshot=budget_snapshot,
                memory_counts=memory_counts,
                environment=environment,
            )
            continuity = _build_project_continuity_context(runtime_dir)
            recent = payload.get("recent_events", [])
            # Use all events (not just the recent 120 in payload) so that
            # chat_plan_created events are found even in long sessions with many events.
            all_events = _read_jsonl_records(runtime_dir / "events.jsonl")
            latest_chat_run = _latest_chat_run_summary(all_events if isinstance(all_events, list) else [])
            latest_chat_run = {
                **latest_chat_run,
                **_load_chat_workflow_insights(
                    runtime_dir,
                    str(latest_chat_run.get("task_id", "") or ""),
                ),
            }
            latest_lead_summary = _latest_lead_user_summary(runtime_dir, str(latest_chat_run.get("task_id", "") or ""))
            return {
                "task_total": payload.get("task_total", 0),
                "task_state_counts": payload.get("task_state_counts", {}),
                "summary": payload.get("summary", {}),
                "agent_latency_percentiles": payload.get("agent_latency_percentiles", {}),
                "agent_latency_trends": payload.get("agent_latency_trends", {}),
                "tuning_recommendations": payload.get("tuning_recommendations", []),
                "tasks": payload.get("tasks", [])[:80],
                "recent_events": recent[-40:],
                "last_chat_run": latest_chat_run,
                "last_lead_user_summary": latest_lead_summary,
                "notebooklm_status": _detect_notebooklm_status(runtime_dir, PROJECT_ROOT),
                "project_continuity": continuity,
                "mcp_overview": _load_mcp_overview(request),
            }

        return await asyncio.to_thread(_load_state)
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Unhandled error in aiteam router")
        return {"error": str(e)}


@router.get("/api/aiteam/conversations")
async def get_aiteam_conversations(request: Request, limit: int = 80):
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        mailbox_path = runtime_dir / "mailbox.jsonl"
        events_path = runtime_dir / "events.jsonl"
        records = _read_jsonl_records(mailbox_path)
        items: list[dict[str, object]] = []
        for record in records:
            ts = str(record.get("timestamp", ""))
            items.append(
                {
                    "timestamp": ts,
                    "sender": str(record.get("sender", "")),
                    "recipient": str(record.get("recipient", "")),
                    "subject": str(record.get("subject", "")),
                    "body": str(record.get("body", "")),
                    "task_id": str(record.get("task_id", "") or ""),
                }
            )

        existing_user_task_ids = {
            str(item.get("task_id", ""))
            for item in items
            if str(item.get("sender", "")).strip().lower() == "user"
        }

        events = _read_jsonl_records(events_path)
        started_ts: dict[str, str] = {}
        for event in events:
            if str(event.get("event_type", "")) != "task_started":
                continue
            payload = event.get("payload", {})
            if not isinstance(payload, dict):
                continue
            task_id = str(payload.get("task_id", "") or "")
            if task_id and task_id not in started_ts:
                started_ts[task_id] = str(event.get("ts", ""))

        tasks_payload = _read_runtime_tasks_payload(runtime_dir)
        if isinstance(tasks_payload, list):
            for item in tasks_payload:
                if not isinstance(item, dict):
                    continue
                task_id = str(item.get("task_id", "") or "")
                if not task_id.endswith("::lead_intake"):
                    continue
                root_id = task_id.split("::", 1)[0]
                if task_id in existing_user_task_ids or root_id in existing_user_task_ids:
                    continue
                description = str(item.get("description", "") or "")
                extracted = _extract_user_message_from_task_description(description)
                if not extracted:
                    continue
                items.append(
                    {
                        "timestamp": started_ts.get(task_id, ""),
                        "sender": "user",
                        "recipient": "team_lead",
                        "subject": f"User input: {root_id}",
                        "body": extracted,
                        "task_id": root_id,
                    }
                )

        sorted_items = sorted(items, key=lambda item: str(item.get("timestamp", "")), reverse=True)
        top = sorted_items[: max(1, min(limit, 300))]
        latest_chat_run = _latest_chat_run_summary(events if isinstance(events, list) else [])
        latest_chat_run = {
            **latest_chat_run,
            **_load_chat_workflow_insights(
                runtime_dir,
                str(latest_chat_run.get("task_id", "") or ""),
            ),
        }
        return {
            "total": len(items),
            "items": top,
            "last_chat_run": latest_chat_run,
            "mcp_overview": _load_mcp_overview(request),
        }
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Unhandled error in aiteam router")
        return {"error": str(e)}


@router.get("/api/aiteam/logs")
async def get_aiteam_logs(request: Request, limit: int = 100):
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        events_path = runtime_dir / "events.jsonl"

        event_records = _read_jsonl_records(events_path)
        top_records = sorted(event_records, key=lambda item: str(item.get("ts", "")), reverse=True)[
            : max(1, min(limit, 400))
        ]

        event_logs: list[dict[str, object]] = []
        task_last_ts: dict[str, str] = {}
        task_started_ts: dict[str, str] = {}
        user_output_candidates: list[dict[str, object]] = []
        for record in event_records:
            event_type = str(record.get("event_type", ""))
            payload = record.get("payload", {})
            if event_type == "task_execution" and isinstance(payload, dict):
                task_id = str(payload.get("task_id", "") or "")
                if task_id:
                    task_last_ts[task_id] = str(record.get("ts", ""))
            if event_type == "task_started" and isinstance(payload, dict):
                task_id = str(payload.get("task_id", "") or "")
                if task_id and task_id not in task_started_ts:
                    task_started_ts[task_id] = str(record.get("ts", ""))
            if event_type == "user_input" and isinstance(payload, dict):
                user_output_candidates.append(
                    {
                        "task_id": str(payload.get("task_id", "") or ""),
                        "role": "user",
                        "state": "submitted",
                        "ts": str(record.get("ts", "")),
                        "output": str(payload.get("message", "") or ""),
                    }
                )

        for record in top_records:
            event_type = str(record.get("event_type", "unknown"))
            payload = record.get("payload", {})
            payload_dict = payload if isinstance(payload, dict) else {}
            event_logs.append(
                {
                    "ts": str(record.get("ts", "")),
                    "event_type": event_type,
                    "task_id": str(payload_dict.get("task_id", "") or ""),
                    "summary": _event_summary(event_type, payload_dict),
                }
            )

        tasks_payload = _read_runtime_tasks_payload(runtime_dir)
        synthetic_user_events: list[dict[str, object]] = []
        task_outputs: list[dict[str, object]] = []
        if isinstance(tasks_payload, list):
            for item in tasks_payload:
                if not isinstance(item, dict):
                    continue
                metadata = item.get("metadata", {})
                metadata_dict = metadata if isinstance(metadata, dict) else {}
                raw_output = metadata_dict.get("result") or metadata_dict.get("error") or metadata_dict.get("execution_plan_result")
                if not raw_output:
                    continue
                task_id = str(item.get("task_id", ""))
                task_outputs.append(
                    {
                        "task_id": task_id,
                        "role": str(item.get("role", "")),
                        "state": str(item.get("state", "")),
                        "ts": task_last_ts.get(task_id, ""),
                        "output": str(raw_output),
                    }
                )

            existing_user_task_ids = {
                str(item.get("task_id", ""))
                for item in user_output_candidates
            }
            existing_user_event_task_ids = {
                str(item.get("task_id", ""))
                for item in event_logs
                if str(item.get("event_type", "")) == "user_input"
            }
            for item in tasks_payload:
                if not isinstance(item, dict):
                    continue
                task_id = str(item.get("task_id", "") or "")
                if not task_id.endswith("::lead_intake"):
                    continue
                root_id = task_id.split("::", 1)[0]
                if root_id in existing_user_task_ids:
                    continue
                message = _extract_user_message_from_task_description(str(item.get("description", "") or ""))
                if not message:
                    continue
                ts_value = task_started_ts.get(task_id, task_last_ts.get(task_id, ""))
                user_output_candidates.append(
                    {
                        "task_id": root_id,
                        "role": "user",
                        "state": "submitted",
                        "ts": ts_value,
                        "output": message,
                    }
                )
                if root_id not in existing_user_event_task_ids:
                    synthetic_user_events.append(
                        {
                            "ts": ts_value,
                            "event_type": "user_input",
                            "task_id": root_id,
                            "summary": _event_summary(
                                "user_input",
                                {
                                    "task_id": root_id,
                                    "message": message,
                                },
                            ),
                        }
                    )

        task_outputs.extend(user_output_candidates)
        event_logs.extend(synthetic_user_events)
        event_logs.sort(key=lambda item: str(item.get("ts", "")), reverse=True)
        event_limit = max(1, min(limit, 400))
        
        return {
            "total": len(event_logs),
            "event_logs": event_logs[:event_limit],
            "task_outputs": task_outputs,
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Unhandled error in aiteam router")
        return {"error": str(e)}


# ── Session Audit Endpoints ─────────────────────────────────────────


@router.get("/api/aiteam/sessions")
async def list_sessions(
    request: Request,
    agent_id: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
):
    """Lista sesiones de agentes con filtros opcionales."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.agent_session import SessionStore
        store = SessionStore(runtime_dir)
        sessions = store.list_sessions(agent_id=agent_id, task_id=task_id, limit=limit)
        active = [s.to_summary_dict() for s in store.get_active_sessions()]
        return {"sessions": sessions, "active_sessions": active, "total": len(sessions)}
    except Exception as exc:
        return {"error": str(exc), "sessions": [], "active_sessions": []}


@router.get("/api/aiteam/sessions/{session_id}")
async def get_session_detail(request: Request, session_id: str):
    """Detalle completo de una sesion incluyendo todas las acciones."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.agent_session import SessionStore
        store = SessionStore(runtime_dir)
        session = store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        return session.to_dict()
    except HTTPException:
        raise
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/api/aiteam/agents/{agent_id}/activity")
async def agent_activity(request: Request, agent_id: str, limit: int = 20):
    """Timeline de actividad de un agente especifico."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.agent_session import SessionStore
        store = SessionStore(runtime_dir)
        activity = store.agent_activity(agent_id, limit=limit)
        return {"agent_id": agent_id, "activity": activity, "total": len(activity)}
    except Exception as exc:
        return {"error": str(exc), "activity": []}


@router.get("/api/aiteam/tools")
async def list_available_tools(request: Request, role: str | None = None):
    """Lista herramientas disponibles del catalogo."""
    _require_api_auth_request(request)
    try:
        catalog_path = PROJECT_ROOT / "config" / "tool_sources.catalog.json"
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.tool_dispatch import ToolDispatcher
        dispatcher = ToolDispatcher(catalog_path=catalog_path, runtime_dir=runtime_dir)
        all_tools = dispatcher.available_tools(role=role)
        enabled = [t for t in all_tools if t.enabled]
        return {
            "total": len(all_tools),
            "enabled": len(enabled),
            "tools": [
                {
                    "name": t.name,
                    "category": t.category,
                    "capabilities": t.capabilities,
                    "role_targets": t.role_targets,
                    "enabled": t.enabled,
                    "requires_approval": t.requires_approval,
                    "description": t.description,
                }
                for t in all_tools
            ],
        }
    except Exception as exc:
        return {"error": str(exc), "tools": []}


@router.get("/api/aiteam/tools/access-log")
async def tool_access_log(
    request: Request,
    agent_id: str | None = None,
    tool_name: str | None = None,
    limit: int = 50,
):
    """Historial de acceso a herramientas."""
    _require_api_auth_request(request)
    try:
        catalog_path = PROJECT_ROOT / "config" / "tool_sources.catalog.json"
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.tool_dispatch import ToolDispatcher
        dispatcher = ToolDispatcher(catalog_path=catalog_path, runtime_dir=runtime_dir)
        history = dispatcher.tool_access_history(agent_id=agent_id, tool_name=tool_name, limit=limit)
        return {"total": len(history), "access_log": history}
    except Exception as exc:
        return {"error": str(exc), "access_log": []}


@router.get("/api/aiteam/routing/catalog")
async def get_routing_catalog(request: Request):
    """Vista consultable del routing: catálogo, roles, primarios y fallbacks efectivos."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        return await asyncio.to_thread(_build_routing_catalog, runtime_dir)
    except HTTPException:
        raise
    except Exception as exc:
        return {"error": str(exc), "roles": [], "providers": [], "adapters": [], "role_matrix": []}


@router.get("/api/aiteam/skills/usage")
async def skill_usage_stats(request: Request, limit: int = 20):
    """Estadisticas de uso de skills: veces usado, tasa de exito, ranking."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.autotools import AutoToolIntegrator
        integrator = AutoToolIntegrator(
            runtime_dir=runtime_dir,
            project_root=PROJECT_ROOT,
        )
        stats = integrator.skill_usage_stats(limit=limit)
        return {"total": len(stats), "skills": stats}
    except Exception as exc:
        return {"error": str(exc), "skills": []}


@router.get("/api/aiteam/skills/ranking/{role}")
async def skill_ranking_for_role(role: str, request: Request, limit: int = 10):
    """Ranking de skills por tasa de exito para un rol."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.autotools import AutoToolIntegrator
        integrator = AutoToolIntegrator(
            runtime_dir=runtime_dir,
            project_root=PROJECT_ROOT,
        )
        ranking = integrator.skill_ranking_for_role(role=role, limit=limit)
        return {"role": role, "total": len(ranking), "ranking": ranking}
    except Exception as exc:
        return {"error": str(exc), "ranking": []}


@router.get("/api/aiteam/workflow-state")
async def get_workflow_state(request: Request):
    """Estado compartido del workflow (blackboard)."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        return {"workflows": _read_runtime_workflow_state(runtime_dir)}
    except Exception as exc:
        return {"error": str(exc), "workflows": {}}


# ── MCP Server Management ─────────────────────────────────────

def _get_mcp_manager(request: Request):
    """Helper para obtener el MCPServerManager."""
    from aiteam.mcp_manager import MCPServerManager
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = workspace / "runtime"
    catalog_path = workspace / "config" / "tool_sources.catalog.json"
    return MCPServerManager(
        runtime_dir=runtime_dir,
        catalog_path=catalog_path,
    )


@router.get("/api/aiteam/mcp/servers")
async def list_mcp_servers(request: Request):
    """Estado de todos los servidores MCP configurados."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        return {"servers": mgr.server_status()}
    except Exception as exc:
        return {"error": str(exc), "servers": []}


@router.post("/api/aiteam/mcp/sync-catalog")
async def sync_mcp_catalog(request: Request):
    """Sincroniza MCPs del catalogo a mcp_servers.json."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        new_count = mgr.sync_from_catalog()
        return {"synced": new_count, "total": len(mgr._configs)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/aiteam/mcp/bootstrap-opencode")
async def bootstrap_mcp_from_opencode(request: Request):
    """Importa MCPs visibles en OpenCode al runtime local."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        imported = mgr.bootstrap_from_opencode()
        return {
            "imported": imported,
            "total": len(mgr._configs),
            "opencode": mgr.opencode_bootstrap_status(),
            "servers": mgr.server_status(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/aiteam/mcp/refresh-health")
async def refresh_mcp_health(request: Request):
    """Re-ejecuta probes MCP y devuelve overview actualizado."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        integrator = AutoToolIntegrator(
            runtime_dir=runtime_dir,
            project_root=PROJECT_ROOT,
            catalog_path=workspace / "config" / "tool_sources.catalog.json",
        )
        report = integrator.mcp_doctor(
            timeout=12,
            enable_healthy=False,
            enable_sensitive=False,
            quarantine_package_unavailable=True,
        )
        mgr = _get_mcp_manager(request)
        return {
            "refreshed": True,
            "report": report,
            "overview": _load_mcp_overview(request),
            "servers": mgr.server_status(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/aiteam/mcp/servers/{server_name}/start")
async def start_mcp_server(server_name: str, request: Request):
    """Inicia un servidor MCP."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        ok, reason = await asyncio.to_thread(mgr.start_server, server_name, 30)
        if not ok:
            raise HTTPException(status_code=400, detail=reason)
        tools = [t.name for t in mgr.list_tools(server_name)]
        return {"status": "running", "server": server_name, "tools": tools}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/aiteam/mcp/servers/{server_name}/stop")
async def stop_mcp_server(server_name: str, request: Request):
    """Detiene un servidor MCP."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        mgr.stop_server(server_name)
        return {"status": "stopped", "server": server_name}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/aiteam/mcp/servers/{server_name}/enable")
async def enable_mcp_server(server_name: str, request: Request):
    """Habilita un servidor MCP."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        enabled = mgr.enable_servers([server_name])
        if not enabled:
            raise HTTPException(status_code=404, detail=f"server '{server_name}' not found")
        return {"enabled": enabled}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/aiteam/mcp/servers/{server_name}/disable")
async def disable_mcp_server(server_name: str, request: Request):
    """Deshabilita un servidor MCP."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        disabled = mgr.disable_servers([server_name])
        if not disabled:
            raise HTTPException(status_code=404, detail=f"server '{server_name}' not found")
        return {"disabled": disabled}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/aiteam/mcp/tools")
async def list_mcp_tools(request: Request, server: str | None = None):
    """Lista herramientas disponibles en servidores MCP activos."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        tools = mgr.list_tools(server)
        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "server": t.server_name,
                    "input_schema": t.input_schema,
                }
                for t in tools
            ]
        }
    except Exception as exc:
        return {"error": str(exc), "tools": []}


@router.post("/api/aiteam/mcp/invoke")
async def invoke_mcp_tool(request: Request):
    """Invoca una herramienta MCP. Body: {server, tool, arguments?}"""
    _require_api_auth_request(request)
    try:
        body = await request.json()
        server_name = str(body.get("server", "")).strip()
        tool_name = str(body.get("tool", "")).strip()
        arguments = body.get("arguments", {})

        if not server_name or not tool_name:
            raise HTTPException(status_code=400, detail="server and tool are required")

        mgr = _get_mcp_manager(request)
        result = await asyncio.to_thread(
            mgr.invoke_tool, server_name, tool_name, arguments, None, 120
        )
        return {
            "success": result.success,
            "server": result.server_name,
            "tool": result.tool_name,
            "content": result.content,
            "text": result.text,
            "error": result.error,
            "duration_ms": result.duration_ms,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/aiteam/mcp/events")
async def mcp_event_history(request: Request, server: str | None = None, limit: int = 50):
    """Historial de eventos MCP."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        events = mgr.event_history(server_name=server, limit=limit)
        return {"events": events}
    except Exception as exc:
        return {"error": str(exc), "events": []}

