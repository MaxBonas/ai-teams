import asyncio
import copy
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from fastapi import APIRouter, HTTPException, Request
from api.utils import (
    _require_api_auth_request,
    _workspace_from_request,
    _build_project_continuity_context,
    _display_ts_local,
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
    resolve_runtime_dir,
    PROJECT_ROOT,
    get_current_workspace,
)

from aiteam.dashboard import build_dashboard_payload
from aiteam.cli import build_default_orchestrator
from aiteam.config import build_default_router_policy
from aiteam.pilot import compute_pilot_metrics
from aiteam.phase_verdicts import derive_run_verdict_from_phase_verdicts
from aiteam.provider_ops import provider_ops_status
from aiteam.autotools import AutoToolIntegrator
from aiteam.lead_close_policy import derive_lead_close_policy
from aiteam.routing_overrides import (
    RoutingOverrides,
    apply_overrides_to_policy,
    load_overrides,
    reset_overrides,
    save_overrides,
    validate_overrides,
)
from aiteam.types import Complexity, Criticality, Role, RoutingRequest
from api.chat_observability import (
    _build_task_operational_summary,
    _coerce_lead_close_policy,
    _coerce_phase_contracts,
    _coerce_phase_verdicts,
    _coerce_run_verdict,
)

router = APIRouter()
logger = logging.getLogger(__name__)


_ROUTING_PAYLOAD_VERSION = 1
_ROUTING_CATALOG_CACHE_TTL_SECONDS = 60.0
_ROUTING_CATALOG_CACHE: dict[str, dict[str, object]] = {}
_ROUTING_CATALOG_CACHE_LOCK = Lock()
_STATE_PAYLOAD_CACHE_TTL_SECONDS = 20.0
_STATE_PAYLOAD_CACHE: dict[str, dict[str, object]] = {}
_STATE_PAYLOAD_CACHE_LOCK = Lock()
_STATE_PAYLOAD_INFLIGHT: dict[str, Event] = {}

_ROUTING_BLOCKER_META: dict[str, dict[str, str]] = {
    "role_targets": {
        "label": "adapter restringido a otros roles",
        "reason": "El adapter limita explícitamente qué roles pueden usarlo.",
        "severity": "hard",
    },
    "team_lead_guard": {
        "label": "reservado para team_lead",
        "reason": "La política del Team Lead no permite este adapter para decisiones soberanas.",
        "severity": "hard",
    },
    "adapter_unavailable": {
        "label": "adapter no disponible",
        "reason": "El adapter no se reporta como disponible en esta máquina.",
        "severity": "hard",
    },
    "provider_unhealthy": {
        "label": "provider con problemas",
        "reason": "El provider o adapter está degradado operacionalmente según provider_ops.",
        "severity": "soft",
    },
    "cost_exceeded": {
        "label": "excede límite de coste",
        "reason": "El budget manager no permite usar este adapter API con el presupuesto actual.",
        "severity": "soft",
    },
    "capability_missing": {
        "label": "falta capacidad requerida",
        "reason": "El adapter no satisface las capacidades requeridas para esta resolución.",
        "severity": "hard",
    },
    "channel_excluded": {
        "label": "canal no permitido para este rol",
        "reason": "El canal del adapter no está permitido para este rol o entorno efectivo.",
        "severity": "hard",
    },
}


def _timed_call(
    timings: dict[str, int],
    key: str,
    fn,
):
    started = monotonic()
    result = fn()
    timings[key] = max(0, int((monotonic() - started) * 1000))
    return result


def _routing_cache_key(runtime_dir: Path) -> str:
    try:
        return str(runtime_dir.resolve()).lower()
    except Exception:
        return str(runtime_dir).lower()


def _state_cache_key(runtime_dir: Path, environment: str) -> str:
    return f"{_routing_cache_key(runtime_dir)}::{str(environment or 'dev').strip().lower()}"


def _routing_cache_snapshot(
    payload: dict[str, object],
    *,
    status: str,
    age_seconds: float,
) -> dict[str, object]:
    snapshot = copy.deepcopy(payload)
    snapshot["cache"] = {
        "status": status,
        "age_ms": max(0, int(age_seconds * 1000)),
        "ttl_ms": int(_ROUTING_CATALOG_CACHE_TTL_SECONDS * 1000),
    }
    return snapshot


def _state_cache_snapshot(
    payload: dict[str, object],
    *,
    status: str,
    age_seconds: float,
) -> dict[str, object]:
    snapshot = copy.deepcopy(payload)
    startup = dict(snapshot.get("startup_diagnostics", {}) or {})
    startup["cache"] = {
        "status": status,
        "age_ms": max(0, int(age_seconds * 1000)),
        "ttl_ms": int(_STATE_PAYLOAD_CACHE_TTL_SECONDS * 1000),
    }
    snapshot["startup_diagnostics"] = startup
    return snapshot


def _get_cached_routing_catalog(
    runtime_dir: Path,
    *,
    allow_stale: bool,
) -> dict[str, object] | None:
    cache_key = _routing_cache_key(runtime_dir)
    with _ROUTING_CATALOG_CACHE_LOCK:
        cached = dict(_ROUTING_CATALOG_CACHE.get(cache_key, {}) or {})
    payload = cached.get("payload")
    built_at = cached.get("built_at")
    if not isinstance(payload, dict) or not isinstance(built_at, (int, float)):
        return None
    age_seconds = max(0.0, float(monotonic() - float(built_at)))
    if age_seconds <= _ROUTING_CATALOG_CACHE_TTL_SECONDS:
        return _routing_cache_snapshot(payload, status="hit", age_seconds=age_seconds)
    if allow_stale:
        return _routing_cache_snapshot(payload, status="stale_fallback", age_seconds=age_seconds)
    return None


def _peek_cached_state_payload(
    runtime_dir: Path,
    *,
    environment: str,
    allow_stale: bool,
) -> dict[str, object] | None:
    cache_key = _state_cache_key(runtime_dir, environment)
    with _STATE_PAYLOAD_CACHE_LOCK:
        cached = dict(_STATE_PAYLOAD_CACHE.get(cache_key, {}) or {})
    payload = cached.get("payload")
    built_at = cached.get("built_at")
    if not isinstance(payload, dict) or not isinstance(built_at, (int, float)):
        return None
    age_seconds = max(0.0, float(monotonic() - float(built_at)))
    if age_seconds <= _STATE_PAYLOAD_CACHE_TTL_SECONDS:
        return _state_cache_snapshot(payload, status="hit", age_seconds=age_seconds)
    if allow_stale:
        return _state_cache_snapshot(payload, status="stale_fallback", age_seconds=age_seconds)
    return None


def _store_cached_routing_catalog(runtime_dir: Path, payload: dict[str, object]) -> None:
    cache_key = _routing_cache_key(runtime_dir)
    with _ROUTING_CATALOG_CACHE_LOCK:
        _ROUTING_CATALOG_CACHE[cache_key] = {
            "payload": copy.deepcopy(payload),
            "built_at": monotonic(),
        }


def _store_cached_state_payload(
    runtime_dir: Path,
    *,
    environment: str,
    payload: dict[str, object],
) -> None:
    cache_key = _state_cache_key(runtime_dir, environment)
    with _STATE_PAYLOAD_CACHE_LOCK:
        _STATE_PAYLOAD_CACHE[cache_key] = {
            "payload": copy.deepcopy(payload),
            "built_at": monotonic(),
        }


def _invalidate_routing_catalog_cache(runtime_dir: Path | None = None) -> None:
    with _ROUTING_CATALOG_CACHE_LOCK:
        if runtime_dir is None:
            _ROUTING_CATALOG_CACHE.clear()
            return
        _ROUTING_CATALOG_CACHE.pop(_routing_cache_key(runtime_dir), None)


def _acquire_state_compute_slot(
    runtime_dir: Path,
    *,
    environment: str,
) -> tuple[bool, Event | None]:
    cache_key = _state_cache_key(runtime_dir, environment)
    with _STATE_PAYLOAD_CACHE_LOCK:
        inflight = _STATE_PAYLOAD_INFLIGHT.get(cache_key)
        if inflight is not None:
            return False, inflight
        event = Event()
        _STATE_PAYLOAD_INFLIGHT[cache_key] = event
        return True, event


def _release_state_compute_slot(
    runtime_dir: Path,
    *,
    environment: str,
    event: Event | None,
) -> None:
    cache_key = _state_cache_key(runtime_dir, environment)
    with _STATE_PAYLOAD_CACHE_LOCK:
        current = _STATE_PAYLOAD_INFLIGHT.get(cache_key)
        if current is event:
            _STATE_PAYLOAD_INFLIGHT.pop(cache_key, None)
    if event is not None:
        event.set()


def _truncate_phase_delivery_text(value: object, limit: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _is_primary_workflow_phase(phase_id: str) -> bool:
    normalized = str(phase_id or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith(("scout_", "delegate_", "delegated_", "checkpoint_")):
        return False
    return normalized not in {"lead_intake", "lead_close"}


def _build_phase_delivery_summary(entry: object) -> list[dict[str, object]]:
    if not isinstance(entry, dict):
        return []

    phase_contracts = _coerce_phase_contracts(entry.get("phase_contracts", {}))
    phase_verdicts = _coerce_phase_verdicts(entry.get("phase_verdicts", {}))
    phase_outputs = dict(entry.get("phase_outputs", {}) or {})
    phase_context_summaries = dict(entry.get("phase_context_summaries", {}) or {})

    ordered_phase_ids: list[str] = []
    for source in (
        phase_contracts.keys(),
        phase_verdicts.keys(),
        phase_context_summaries.keys(),
        phase_outputs.keys(),
    ):
        for raw_phase_id in source:
            phase_id = str(raw_phase_id or "").strip()
            if (
                phase_id
                and phase_id not in ordered_phase_ids
                and _is_primary_workflow_phase(phase_id)
            ):
                ordered_phase_ids.append(phase_id)

    items: list[dict[str, object]] = []
    for phase_id in ordered_phase_ids:
        contract = dict(phase_contracts.get(phase_id, {}) or {})
        verdict = dict(phase_verdicts.get(phase_id, {}) or {})
        objective = str(contract.get("objective", "") or "").strip()
        delivery_summary = str(phase_context_summaries.get(phase_id, "") or "").strip()
        delivery_source = "phase_context_summary"
        raw_output = str(phase_outputs.get(phase_id, "") or "").strip()
        if not delivery_summary and raw_output:
            delivery_summary = _truncate_phase_delivery_text(raw_output, limit=220)
            delivery_source = "phase_output"
        verdict_summary = str(verdict.get("summary", "") or "").strip()
        reason_codes = [
            str(item).strip()
            for item in list(verdict.get("reason_codes", []) or [])
            if str(item).strip()
        ][:6]
        depends_on = [
            str(item).strip()
            for item in list(contract.get("depends_on", []) or [])
            if str(item).strip()
        ][:6]
        items.append(
            {
                "phase_id": phase_id,
                "role": str(contract.get("role", "") or "").strip(),
                "objective": objective,
                "objective_missing": objective in {"", "|"},
                "depends_on": depends_on,
                "verdict_status": str(verdict.get("status", "") or "").strip(),
                "contract_status": str(verdict.get("contract_status", "") or "").strip(),
                "reason_codes": reason_codes,
                "verdict_summary": verdict_summary,
                "delivery_summary": delivery_summary,
                "delivery_source": delivery_source if delivery_summary else "",
                "has_delivery": bool(delivery_summary),
                "has_output": bool(raw_output),
            }
        )
    return items


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
    phase_verdicts = _coerce_phase_verdicts(entry.get("phase_verdicts", {}))
    run_verdict = _coerce_run_verdict(entry.get("run_verdict", {}))
    if not run_verdict and phase_verdicts:
        run_verdict = _coerce_run_verdict(
            derive_run_verdict_from_phase_verdicts(phase_verdicts)
        )
    lead_close_policy = _coerce_lead_close_policy(
        derive_lead_close_policy(
            phase_verdicts=phase_verdicts,
            run_verdict=run_verdict,
        )
    )
    return {
        "workflow_run_status": str(entry.get("run_status", "") or "").strip().lower(),
        "phase_contracts": _coerce_phase_contracts(entry.get("phase_contracts", {})),
        "phase_verdicts": phase_verdicts,
        "phase_delivery_summary": _build_phase_delivery_summary(entry),
        "phase_evidence_plan": dict(entry.get("phase_evidence_plan", {}) or {}),
        "delegate_batches": list(entry.get("delegate_batches", []) or []),
        "delegate_economics": dict(entry.get("delegate_economics_summary", {}) or {}),
        "run_verdict": run_verdict,
        "lead_close_policy": lead_close_policy,
        "continuation_requested": bool(entry.get("continuation_requested", False)),
        "continuation_effective": bool(entry.get("continuation_effective", False)),
        "continuation_block_reason": str(entry.get("continuation_block_reason", "") or ""),
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


def _routing_blocker_details(blockers: list[str]) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    for blocker in blockers:
        meta = dict(_ROUTING_BLOCKER_META.get(str(blocker).strip(), {}) or {})
        details.append(
            {
                "code": str(blocker).strip(),
                "label": str(meta.get("label", blocker)).strip(),
                "reason": str(meta.get("reason", "")).strip(),
                "severity": str(meta.get("severity", "hard")).strip() or "hard",
            }
        )
    return details


def _routing_cost_class(adapter, profile) -> str:
    tier = str(getattr(profile, "tier", "") or "").strip().lower()
    if tier == "senior_cloud":
        return "high"
    if tier == "advanced_api":
        return "medium"
    if tier in {"budget_api", "local"}:
        return "low"
    cost_tier = int(getattr(adapter, "cost_tier", 1) or 1)
    if cost_tier >= 2:
        return "high"
    if cost_tier <= 0:
        return "low"
    return "medium"


def _routing_long_context(adapter, profile) -> bool:
    blob = " ".join(
        [
            str(getattr(adapter, "model", "") or ""),
            str(getattr(profile, "notes", "") or ""),
        ]
    ).lower()
    return any(token in blob for token in ("131k", "128k", "200k", "long context", "ctx"))


def _routing_capability_profile(adapter, profile) -> dict[str, object]:
    capabilities = set(getattr(adapter, "capabilities", set()) or set())
    return {
        "channel": adapter.channel.value,
        "tier": str(getattr(profile, "tier", "") or "").strip(),
        "cost_class": _routing_cost_class(adapter, profile),
        "tool_support": "tools" in capabilities,
        "stream_support": "stream" in capabilities,
        "vision": "vision" in capabilities,
        "thinking": "thinking" in capabilities,
        "long_context": _routing_long_context(adapter, profile),
    }


def _routing_resolution_entry(adapter, profile) -> dict[str, object]:
    return {
        "adapter": adapter.name,
        "provider": adapter.provider,
        "model": adapter.model,
        "channel": adapter.channel.value,
        "tier": str(getattr(profile, "tier", "") or "").strip(),
    }


def _collect_role_blockers(
    *,
    router_obj,
    adapter,
    request: RoutingRequest,
    available: bool,
    operational: bool,
    budget_signal,
) -> list[str]:
    blockers: list[str] = []
    role_name = request.role.value
    profile = router_obj._profile_for(adapter)
    if adapter.role_targets and role_name not in adapter.role_targets:
        blockers.append("role_targets")
    if role_name == Role.TEAM_LEAD.value and not router_obj._team_lead_allowed(adapter):
        blockers.append("team_lead_guard")
    if (
        request.required_capabilities
        and not request.required_capabilities.issubset(set(adapter.capabilities or set()))
    ):
        blockers.append("capability_missing")
    if not available:
        blockers.append("adapter_unavailable")
    if not operational:
        blockers.append("provider_unhealthy")
    if (
        adapter.channel.value == "api"
        and budget_signal is not None
        and (
            not bool(getattr(budget_signal, "can_use_api", True))
            or int(getattr(adapter, "cost_tier", 0) or 0)
            > int(getattr(budget_signal, "max_api_cost_tier", 999) or 999)
        )
    ):
        blockers.append("cost_exceeded")
    if (
        role_name == Role.TEAM_LEAD.value
        and adapter.channel.value == "api"
        and profile is not None
        and not bool(getattr(profile, "api_allowed_for_team_lead", False))
    ):
        blockers.append("channel_excluded")
    return sorted(set(blockers))


def _build_routing_catalog(runtime_dir: Path) -> dict[str, object]:
    default_policy = build_default_router_policy()
    overrides = load_overrides(runtime_dir)
    override_local_present = overrides.has_entries()
    override_local_payload = overrides.to_dict() if override_local_present else None
    orchestrator = build_default_orchestrator(runtime_dir=runtime_dir, environment="dev")
    router_obj = orchestrator.router
    effective_policy = router_obj.policy
    ops_status = provider_ops_status(runtime_dir)
    budget_signal = (
        router_obj.budget_manager.api_signal()
        if getattr(router_obj, "budget_manager", None) is not None
        else None
    )

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
            "capability_profile": _routing_capability_profile(adapter, profile),
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
            "supports_tools": bool(_routing_capability_profile(adapter, profile).get("tool_support")),
            "supports_streaming": bool(_routing_capability_profile(adapter, profile).get("stream_support")),
            "supports_vision": bool(_routing_capability_profile(adapter, profile).get("vision")),
            "supports_thinking": bool(_routing_capability_profile(adapter, profile).get("thinking")),
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
        default_provider_order = list(default_policy.role_provider_preferences.get(role_name, []) or [])
        default_model_order = list(default_policy.role_model_preferences.get(role_name, []) or [])
        configured_provider_order = list(
            effective_policy.role_provider_preferences.get(role_name, []) or []
        )
        configured_model_order = list(
            effective_policy.role_model_preferences.get(role_name, []) or []
        )
        configured_provider_set = {
            str(item).strip().lower() for item in configured_provider_order if str(item).strip()
        }
        configured_model_set = {
            str(item).strip().lower() for item in configured_model_order if str(item).strip()
        }
        role_override = overrides.overrides_by_role.get(role_name)

        effective_rows: list[dict[str, object]] = []
        for adapter in adapters:
            profile = router_obj._profile_for(adapter)
            ops_row = dict(ops_status.get(adapter.name, {}) or {})
            try:
                available = bool(adapter.available())
            except Exception:
                available = False
            operational = bool(ops_row.get("operational", available))
            allowed = adapter.name in eligible_names
            blockers = _collect_role_blockers(
                router_obj=router_obj,
                adapter=adapter,
                request=request,
                available=available,
                operational=operational,
                budget_signal=budget_signal,
            )
            effective_rows.append(
                {
                    "adapter_name": adapter.name,
                    "provider": adapter.provider,
                    "model": adapter.model,
                    "channel": adapter.channel.value,
                    "tier": str(getattr(profile, "tier", "") or ""),
                    "configured_provider_preferred": adapter.provider.strip().lower() in configured_provider_set,
                    "configured_model_preferred": adapter.model.strip().lower() in configured_model_set,
                    "eligible": allowed,
                    "available": available,
                    "operational": operational,
                    "role_targets": sorted(adapter.role_targets),
                    "capability_profile": _routing_capability_profile(adapter, profile),
                    "blockers": blockers,
                    "blocker_details": _routing_blocker_details(blockers),
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
        eligible_count = sum(1 for item in effective_rows if bool(item.get("eligible")))
        blocked_count = sum(1 for item in effective_rows if not bool(item.get("eligible")))
        operational_count = sum(1 for item in effective_rows if bool(item.get("operational")))
        available_count = sum(1 for item in effective_rows if bool(item.get("available")))
        role_matrix.append(
            {
                "role": role_name,
                "defaults": {
                    "providers": default_provider_order,
                    "models": default_model_order,
                },
                "override_local": role_override.to_dict() if role_override is not None else None,
                "effective": {
                    "primary": (
                        _routing_resolution_entry(primary, router_obj._profile_for(primary))
                        if primary is not None
                        else None
                    ),
                    "fallbacks": [
                        _routing_resolution_entry(adapter, router_obj._profile_for(adapter))
                        for adapter in eligible[1:6]
                    ],
                },
                "configured_provider_order": configured_provider_order,
                "configured_model_order": configured_model_order,
                "effective_provider_order": effective_providers,
                "configured_vs_effective_gap": configured_provider_order != effective_providers,
                "eligibility_summary": {
                    "eligible_count": eligible_count,
                    "blocked_count": blocked_count,
                    "available_count": available_count,
                    "operational_count": operational_count,
                },
                "primary_resolution": {
                    "status": "resolved" if primary is not None else "no_eligible_adapter",
                    "reason": (
                        "first_eligible_after_policy_sort"
                        if primary is not None
                        else "no_eligible_adapter"
                    ),
                    "strict_role_policy": bool(
                        effective_policy.enforce_role_model_preferences
                        or "dev" in set(
                            str(item).strip().lower()
                            for item in list(effective_policy.strict_role_policy_environments or [])
                            if str(item).strip()
                        )
                    ),
                },
                "primary": (
                    _routing_resolution_entry(primary, router_obj._profile_for(primary))
                    if primary is not None
                    else {}
                ),
                "fallbacks": [_routing_resolution_entry(adapter, router_obj._profile_for(adapter)) for adapter in eligible[1:6]],
                "adapters": effective_rows,
            }
        )

    return {
        "payload_version": _ROUTING_PAYLOAD_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "role_count": len(role_matrix),
            "provider_count": len(provider_index),
            "adapter_count": len(adapter_rows),
            "operational_provider_count": sum(
                1 for item in provider_index.values() if int(item.get("operational_count", 0)) > 0
            ),
        },
        "policy": {
            "source": (
                "defaults_repo_plus_local_override"
                if override_local_present
                else "defaults_repo"
            ),
            "override_local_present": override_local_present,
            "override_local": override_local_payload,
            "preferred_subscription_providers": list(
                effective_policy.preferred_subscription_providers or []
            ),
            "preferred_api_providers": list(effective_policy.preferred_api_providers or []),
            "enforce_role_model_preferences": bool(
                effective_policy.enforce_role_model_preferences
            ),
            "strict_role_policy_environments": list(
                effective_policy.strict_role_policy_environments or []
            ),
        },
        "providers": sorted(provider_index.values(), key=lambda item: str(item.get("provider", ""))),
        "roles": role_order,
        "adapters": adapter_rows,
        "role_matrix": role_matrix,
    }


def _routing_overrides_response_payload(overrides: RoutingOverrides) -> dict[str, object]:
    payload = overrides.to_dict()
    override_local_present = overrides.has_entries()
    return {
        "override_local_present": override_local_present,
        "override_local": payload if override_local_present else None,
        **payload,
    }


def _routing_overrides_from_payload(payload: object) -> RoutingOverrides:
    if not isinstance(payload, dict):
        return RoutingOverrides()
    if "overrides_by_role" in payload:
        return RoutingOverrides.from_dict(payload)
    return RoutingOverrides.from_dict({"overrides_by_role": payload})


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


def _latest_chat_run_summary(
    recent_events: list[dict],
    workflow_state: dict[str, object] | None = None,
) -> dict[str, object]:
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
    degraded_delivery = False
    degrade_scope = ""
    degrade_reason = ""
    skipped_phase_ids: list[str] = []
    skipped_phase_reasons: dict[str, str] = {}
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
        if directive == "degrade" and not degraded_delivery:
            degraded_delivery = True
            degrade_scope = str(payload.get("scope", "") or "")
            degrade_reason = str(payload.get("reason", "") or "")
        if directive == "skip_phase":
            target_phase = str(payload.get("target_phase", "") or "").strip()
            if target_phase and target_phase not in skipped_phase_ids:
                skipped_phase_ids.append(target_phase)
            if target_phase:
                skipped_phase_reasons[target_phase] = str(payload.get("reason", "") or "")
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

    artifact_created = 0
    artifact_modified = 0
    artifact_file_count = 0
    artifact_files_truncated = False
    artifact_files: list[str] = []
    authoritative_state = ""
    workflow_run_status = ""
    failed_phases: list[str] = []
    pending_phases: list[str] = []
    next_action_hint = ""
    policy_review_required = False
    for record in reversed(recent_events):
        event_type = str(record.get("event_type", "") or "")
        if event_type not in {"chat_artifacts_detected", "chat_probe_completed"}:
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        artifact_created = _safe_int(
            payload.get("created", payload.get("artifact_created", 0)),
            0,
        )
        artifact_modified = _safe_int(
            payload.get("modified", payload.get("artifact_modified", 0)),
            0,
        )
        raw_files = payload.get("files", payload.get("artifact_files", []))
        if isinstance(raw_files, list):
            artifact_files = sorted(
                {
                    str(item or "").strip()
                    for item in raw_files
                    if str(item or "").strip()
                }
            )
        artifact_file_count = max(
            artifact_created + artifact_modified,
            _safe_int(payload.get("file_count", payload.get("artifact_file_count", 0)), 0),
            len(artifact_files),
        )
        artifact_files_truncated = bool(
            payload.get("files_truncated", payload.get("artifact_files_truncated", False))
        ) or artifact_file_count > len(artifact_files)
        break

    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_run_verdict_persisted":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        authoritative_state = str(payload.get("state", "") or "").strip().lower()
        failed_phases = [
            str(item or "").strip()
            for item in list(payload.get("failed_phases", []) or [])
            if str(item or "").strip()
        ][:12]
        pending_phases = [
            str(item or "").strip()
            for item in list(payload.get("pending_phases", []) or [])
            if str(item or "").strip()
        ][:12]
        next_action_hint = str(payload.get("next_action_hint", "") or "").strip()
        policy_review_required = bool(payload.get("policy_review_required", False))
        break

    workflow_entry = {}
    if isinstance(workflow_state, dict):
        maybe_entry = workflow_state.get(task_id, {})
        if isinstance(maybe_entry, dict):
            workflow_entry = maybe_entry
    workflow_run_status = str(
        workflow_entry.get("run_status", "") if isinstance(workflow_entry, dict) else ""
    ).strip().lower()

    status = workflow_run_status
    if not status:
        if authoritative_state in {"completed", "failed", "rejected", "waiting_user"}:
            status = authoritative_state
        elif exhausted:
            status = "window_exhausted"
        else:
            status = "running"
    has_product_artifacts = artifact_file_count > 0 or bool(artifact_files)
    if has_product_artifacts:
        artifact_message = (
            f"Se detectaron {artifact_file_count} artefactos de producto fuera de .aiteam."
        )
    else:
        artifact_message = "Esta run no genero artefactos de producto."
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
        "workflow_run_status": workflow_run_status,
        "authoritative_state": authoritative_state,
        "failed_phases": failed_phases,
        "pending_phases": pending_phases,
        "next_action_hint": next_action_hint,
        "policy_review_required": policy_review_required,
        "execution_mode": execution_mode,
        "placeholder_outputs": placeholder_outputs,
        "successful_checks": successful_checks,
        "successful_check_count": len(successful_checks),
        "live_mode_required": live_mode_required,
        "live_mode_rejected": live_mode_rejected,
        "evidence_gate_rejected": evidence_gate_rejected,
        "advisory_mode": advisory_mode,
        "advisory_reason": advisory_reason,
        "degraded_delivery": degraded_delivery,
        "degrade_scope": degrade_scope,
        "degrade_reason": degrade_reason,
        "skipped_phase_ids": skipped_phase_ids,
        "skipped_phase_reasons": skipped_phase_reasons,
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
        "artifact_created": artifact_created,
        "artifact_modified": artifact_modified,
        "artifact_file_count": artifact_file_count,
        "artifact_files_truncated": artifact_files_truncated,
        "artifact_files": artifact_files,
        "product_artifacts": {
            "has_artifacts": has_product_artifacts,
            "created": artifact_created,
            "modified": artifact_modified,
            "file_count": artifact_file_count,
            "files_preview_truncated": artifact_files_truncated,
            "files": artifact_files,
            "message": artifact_message,
            "internal_runtime_excluded": True,
        },
        "ts": _display_ts_local(latest_plan.get("ts", "")),
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
            "timestamp": _display_ts_local(record.get("timestamp", "")),
        }
    return {}

@router.get("/api/dashboard")
async def get_dashboard(request: Request):
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
        if not runtime_dir.exists():
            raise HTTPException(
                status_code=404,
                detail="No AI Team environment found (missing runtime directory: .aiteam/ or runtime/).",
            )

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
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
        if not runtime_dir.exists():
            raise HTTPException(
                status_code=404,
                detail="No AI Team runtime directory found in workspace (.aiteam/ or runtime/).",
            )

        cached_payload = _peek_cached_state_payload(
            runtime_dir,
            environment=environment,
            allow_stale=False,
        )
        if cached_payload is not None:
            return cached_payload

        is_owner, inflight_event = _acquire_state_compute_slot(
            runtime_dir,
            environment=environment,
        )
        if not is_owner:
            stale_payload = _peek_cached_state_payload(
                runtime_dir,
                environment=environment,
                allow_stale=True,
            )
            if stale_payload is not None:
                startup = dict(stale_payload.get("startup_diagnostics", {}) or {})
                startup["coalesced"] = True
                stale_payload["startup_diagnostics"] = startup
                return stale_payload
            if inflight_event is not None:
                inflight_event.wait(timeout=20.0)
            waited_payload = _peek_cached_state_payload(
                runtime_dir,
                environment=environment,
                allow_stale=True,
            )
            if waited_payload is not None:
                startup = dict(waited_payload.get("startup_diagnostics", {}) or {})
                startup["coalesced"] = True
                waited_payload["startup_diagnostics"] = startup
                return waited_payload

        def _load_state():
            timings: dict[str, int] = {}
            total_started = monotonic()
            orch = _timed_call(
                timings,
                "orchestrator_init_ms",
                lambda: build_default_orchestrator(
                    runtime_dir=runtime_dir,
                    browser_mode="basic",
                    environment=environment,
                ),
            )
            tasks = _timed_call(timings, "taskboard_list_ms", lambda: orch.taskboard.list_tasks())
            summary = _timed_call(timings, "event_summary_ms", lambda: orch.event_logger.summary())
            pilot_metrics = _timed_call(
                timings,
                "pilot_metrics_ms",
                lambda: compute_pilot_metrics(tasks, summary),
            )
            budget = orch.router.budget_manager
            budget_snapshot = _timed_call(
                timings,
                "budget_snapshot_ms",
                lambda: budget.snapshot() if budget is not None else {},
            )
            memory_counts = _timed_call(
                timings,
                "memory_counts_ms",
                lambda: {
                    agent: orch.memory.count(agent)
                    for agent in orch.memory.list_agents()
                },
            )
            payload = _timed_call(
                timings,
                "dashboard_payload_ms",
                lambda: build_dashboard_payload(
                    runtime_dir=runtime_dir,
                    tasks=tasks,
                    summary=summary,
                    pilot_metrics=pilot_metrics,
                    budget_snapshot=budget_snapshot,
                    memory_counts=memory_counts,
                    environment=environment,
                ),
            )
            continuity = _timed_call(
                timings,
                "project_continuity_ms",
                lambda: _build_project_continuity_context(runtime_dir),
            )
            recent = payload.get("recent_events", [])
            all_events = _timed_call(
                timings,
                "events_read_ms",
                lambda: _read_jsonl_records(runtime_dir / "events.jsonl", tail=500),
            )
            workflow_state_payload = _timed_call(
                timings,
                "workflow_insights_ms",
                lambda: _read_runtime_workflow_state(runtime_dir),
            )
            latest_chat_run = _timed_call(
                timings,
                "latest_chat_run_ms",
                lambda: _latest_chat_run_summary(
                    all_events if isinstance(all_events, list) else [],
                    workflow_state_payload if isinstance(workflow_state_payload, dict) else None,
                ),
            )
            latest_task_root = str(latest_chat_run.get("task_id", "") or "")
            tasks_payload = _timed_call(
                timings,
                "runtime_tasks_read_ms",
                lambda: _read_runtime_tasks_payload(runtime_dir),
            )
            workflow_insights = _load_chat_workflow_insights(runtime_dir, latest_task_root)
            task_operational_summary = _timed_call(
                timings,
                "task_operational_summary_ms",
                lambda: _build_task_operational_summary(
                    tasks_payload,
                    task_root=latest_task_root,
                    phase_verdicts=workflow_insights.get("phase_verdicts", {}),
                    run_verdict=workflow_insights.get("run_verdict", {}),
                ),
            )
            latest_chat_run = {
                **latest_chat_run,
                "task_operational_summary": task_operational_summary,
                **workflow_insights,
            }
            latest_lead_summary = _timed_call(
                timings,
                "latest_lead_summary_ms",
                lambda: _latest_lead_user_summary(runtime_dir, latest_task_root),
            )
            mcp_overview = _timed_call(
                timings,
                "mcp_overview_ms",
                lambda: _load_mcp_overview(request),
            )
            total_elapsed_ms = max(0, int((monotonic() - total_started) * 1000))
            timings["total_ms"] = total_elapsed_ms
            logger.info(
                "aiteam_state_timing workspace=%s total_ms=%s timings=%s",
                str(workspace),
                total_elapsed_ms,
                timings,
            )
            slow_steps = {
                key: value for key, value in timings.items()
                if key != "total_ms" and int(value) >= 750
            }
            if slow_steps:
                logger.warning(
                    "aiteam_state_slow workspace=%s total_ms=%s slow_steps=%s",
                    str(workspace),
                    total_elapsed_ms,
                    slow_steps,
                )
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
                "mcp_overview": mcp_overview,
                "startup_diagnostics": {
                    "workspace": str(workspace),
                    "timings_ms": timings,
                    "slow_steps": slow_steps,
                },
            }

        payload = await asyncio.to_thread(_load_state)
        _store_cached_state_payload(
            runtime_dir,
            environment=environment,
            payload=payload,
        )
        return _peek_cached_state_payload(
            runtime_dir,
            environment=environment,
            allow_stale=True,
        ) or payload
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Unhandled error in aiteam router")
        return {"error": str(e)}
    finally:
        if 'runtime_dir' in locals() and locals().get("is_owner"):
            _release_state_compute_slot(
                runtime_dir,
                environment=environment,
                event=locals().get("inflight_event"),
            )


@router.get("/api/aiteam/state-lite")
async def get_aiteam_state_lite(request: Request, environment: str = "dev"):
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
        if not runtime_dir.exists():
            raise HTTPException(
                status_code=404,
                detail="No AI Team runtime directory found in workspace (.aiteam/ or runtime/).",
            )

        started = monotonic()
        db_path = runtime_dir / "aiteam.db"
        has_events = (runtime_dir / "events.jsonl").exists()
        has_mailbox = (runtime_dir / "mailbox.jsonl").exists()
        cached = _peek_cached_state_payload(
            runtime_dir,
            environment=environment,
            allow_stale=True,
        )
        cached_last_run = {}
        cached_startup = {}
        if isinstance(cached, dict):
            cached_last_run = dict(cached.get("last_chat_run", {}) or {})
            cached_startup = dict(cached.get("startup_diagnostics", {}) or {})

        total_ms = max(0, int((monotonic() - started) * 1000))
        return {
            "workspace": str(workspace),
            "runtime_dir": str(runtime_dir),
            "runtime_exists": True,
            "db_exists": db_path.exists(),
            "events_exists": has_events,
            "mailbox_exists": has_mailbox,
            "last_chat_run": {
                "task_id": str(cached_last_run.get("task_id", "") or ""),
                "status": str(cached_last_run.get("status", "") or ""),
                "workflow_run_status": str(cached_last_run.get("workflow_run_status", "") or ""),
                "state": str(cached_last_run.get("state", "") or ""),
                "ts": str(cached_last_run.get("ts", "") or ""),
            },
            "startup_diagnostics": {
                "workspace": str(workspace),
                "timings_ms": {"total_ms": total_ms},
                "slow_steps": {},
                "cache": dict(cached_startup.get("cache", {}) or {}),
                "lite": True,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unhandled error in aiteam state-lite")
        return {"error": str(e)}


@router.get("/api/aiteam/conversations")
async def get_aiteam_conversations(request: Request, limit: int = 80):
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
        mailbox_path = runtime_dir / "mailbox.jsonl"
        events_path = runtime_dir / "events.jsonl"
        records = _read_jsonl_records(mailbox_path)
        items: list[dict[str, object]] = []
        for record in records:
            ts = str(record.get("timestamp", ""))
            items.append(
                {
                    "timestamp": _display_ts_local(ts),
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
                        "timestamp": _display_ts_local(started_ts.get(task_id, "")),
                        "sender": "user",
                        "recipient": "team_lead",
                        "subject": f"User input: {root_id}",
                        "body": extracted,
                        "task_id": root_id,
                    }
                )

        sorted_items = sorted(items, key=lambda item: str(item.get("timestamp", "")), reverse=True)
        top = sorted_items[: max(1, min(limit, 300))]
        workflow_state_payload = _read_runtime_workflow_state(runtime_dir)
        latest_chat_run = _latest_chat_run_summary(
            events if isinstance(events, list) else [],
            workflow_state_payload if isinstance(workflow_state_payload, dict) else None,
        )
        latest_task_root = str(latest_chat_run.get("task_id", "") or "")
        workflow_insights = _load_chat_workflow_insights(
            runtime_dir,
            latest_task_root,
        )
        latest_chat_run = {
            **latest_chat_run,
            "task_operational_summary": _build_task_operational_summary(
                tasks_payload,
                task_root=latest_task_root,
                phase_verdicts=workflow_insights.get("phase_verdicts", {}),
                run_verdict=workflow_insights.get("run_verdict", {}),
            ),
            **workflow_insights,
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
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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
                    task_last_ts[task_id] = _display_ts_local(record.get("ts", ""))
            if event_type == "task_started" and isinstance(payload, dict):
                task_id = str(payload.get("task_id", "") or "")
                if task_id and task_id not in task_started_ts:
                    task_started_ts[task_id] = _display_ts_local(record.get("ts", ""))
            if event_type == "user_input" and isinstance(payload, dict):
                user_output_candidates.append(
                    {
                        "task_id": str(payload.get("task_id", "") or ""),
                        "role": "user",
                        "state": "submitted",
                        "ts": _display_ts_local(record.get("ts", "")),
                        "output": str(payload.get("message", "") or ""),
                    }
                )

        for record in top_records:
            event_type = str(record.get("event_type", "unknown"))
            payload = record.get("payload", {})
            payload_dict = payload if isinstance(payload, dict) else {}
            event_logs.append(
                {
                    "ts": _display_ts_local(record.get("ts", "")),
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
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    refresh_requested = str(request.query_params.get("refresh", "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "force",
    }
    try:
        if not refresh_requested:
            cached = _get_cached_routing_catalog(runtime_dir, allow_stale=False)
            if cached is not None:
                return cached
        payload = await asyncio.to_thread(_build_routing_catalog, runtime_dir)
        _store_cached_routing_catalog(runtime_dir, payload)
        return _routing_cache_snapshot(payload, status="fresh", age_seconds=0.0)
    except HTTPException:
        raise
    except Exception as exc:
        cached = _get_cached_routing_catalog(runtime_dir, allow_stale=True)
        if cached is not None:
            cached["cache"] = {
                **dict(cached.get("cache", {}) or {}),
                "error": str(exc),
            }
            return cached
        return {"error": str(exc), "roles": [], "providers": [], "adapters": [], "role_matrix": []}


@router.get("/api/aiteam/routing/overrides")
async def get_routing_overrides(request: Request):
    """Devuelve overrides locales actuales del routing."""
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return _routing_overrides_response_payload(load_overrides(runtime_dir))


@router.put("/api/aiteam/routing/overrides")
async def update_routing_overrides(request: Request):
    """Actualiza overrides locales del routing con validación previa."""
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    payload = await request.json()
    overrides = _routing_overrides_from_payload(payload)
    errors = validate_overrides(overrides, build_default_router_policy())
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    if overrides.has_entries():
        save_overrides(runtime_dir, overrides)
    else:
        reset_overrides(runtime_dir)
    _invalidate_routing_catalog_cache(runtime_dir)
    return _routing_overrides_response_payload(load_overrides(runtime_dir))


@router.delete("/api/aiteam/routing/overrides")
async def delete_routing_overrides(request: Request):
    """Borra overrides locales del routing y vuelve a defaults del repo."""
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    reset_overrides(runtime_dir)
    _invalidate_routing_catalog_cache(runtime_dir)
    return {"ok": True, **_routing_overrides_response_payload(load_overrides(runtime_dir))}


@router.get("/api/aiteam/skills/usage")
async def skill_usage_stats(request: Request, limit: int = 20):
    """Estadisticas de uso de skills: veces usado, tasa de exito, ranking."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
        return {"workflows": _read_runtime_workflow_state(runtime_dir)}
    except Exception as exc:
        return {"error": str(exc), "workflows": {}}


# ── MCP Server Management ─────────────────────────────────────

def _get_mcp_manager(request: Request):
    """Helper para obtener el MCPServerManager."""
    from aiteam.mcp_manager import MCPServerManager
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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


@router.get("/api/aiteam/system/mode")
def get_system_mode():
    from aiteam.sim_mode import sim_mode_enabled
    import os
    live_api = os.getenv("AITEAM_ENABLE_LIVE_API", "0").strip().lower() in {"1", "true", "yes", "on"}
    return {
        "is_sim_mode": sim_mode_enabled(),
        "live_api_enabled": live_api,
    }


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
