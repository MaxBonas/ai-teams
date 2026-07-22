"""Selección contextual y explicable de pares modelo + adapter.

M.6 proyecta el catálogo completo para un rol sin mutar el score base ni los
defaults. Los gates contextuales se recalculan antes de ordenar; una ausencia
de candidatos auto-elegibles nunca se disfraza como recomendación.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from aiteam.model_compatibility import compatibility_decision
from aiteam.model_role_scoring import rank_model_role_scores
from aiteam.policies import canonical_role
from aiteam.tools.catalog import default_capabilities_for_role
from aiteam.user_config import ROLE_CAPABILITY_PROFILES


MODEL_SELECTION_VERSION = "model_selection_v1"
_CONTEXT_GATE_BY_CODE = {
    "confidential_data_forbidden": "privacy",
    "data_classification_required": "privacy",
    "external_mcp_unsupported": "tools",
    "model_capability_missing": "tools",
    "workspace_read_required": "workspace",
    "workspace_write_required": "workspace",
    "structured_output_required": "structured_output",
    "structured_output_insufficient": "structured_output",
}


def build_contextual_model_selection(
    read_model: Mapping[str, Any],
    *,
    role: str,
    profiles: Iterable[Mapping[str, Any]],
    options_by_profile: Mapping[str, Iterable[Mapping[str, Any]]],
    run_profile: str = "",
    criticality: str = "medium",
    data_class: str = "public",
    required_capabilities: Iterable[str] = (),
    capacity_by_profile: Mapping[str, Mapping[str, Any]] | None = None,
    budget_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Devuelve un ranking global; todos los pares aparecen una sola vez."""
    role_key = canonical_role(role)
    required = sorted(
        {
            *(str(item).strip().lower() for item in required_capabilities if str(item).strip()),
            *default_capabilities_for_role(role_key),
        }
    )
    profile_map = {
        str(item.get("id") or ""): dict(item)
        for item in profiles
        if str(item.get("id") or "")
    }
    option_map: dict[tuple[str, str], dict[str, Any]] = {}
    capacity_map = capacity_by_profile or {}
    budget = dict(budget_evidence or {"status": "unknown", "source": "not_observed"})
    profile_options: dict[str, list[dict[str, Any]]] = {}
    for profile_id, raw_options in options_by_profile.items():
        rows = [dict(item) for item in raw_options]
        profile_options[str(profile_id)] = rows
        for option in rows:
            model = str(option.get("value") or option.get("model") or "")
            if model:
                option_map[(str(profile_id), model)] = option
    # El perfil cargado contiene disponibilidad/health por máquina. Se superpone
    # al catálogo comercial sin perder metadata declarada.
    for profile_id, profile in profile_map.items():
        merged_rows = {str(item.get("value") or item.get("model") or ""): dict(item)
                       for item in profile_options.get(profile_id, ())}
        for raw_option in profile.get("model_options") or ():
            if not isinstance(raw_option, Mapping):
                continue
            model = str(raw_option.get("value") or raw_option.get("model") or "")
            if not model:
                continue
            merged_rows[model] = {**merged_rows.get(model, {}), **dict(raw_option)}
        profile_options[profile_id] = list(merged_rows.values())
        for model, option in merged_rows.items():
            if model:
                option_map[(profile_id, model)] = option

    prepared: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    scores: list[dict[str, Any]] = []
    for raw_candidate in read_model.get("candidates") or ():
        candidate = deepcopy(dict(raw_candidate))
        candidate_id = str(candidate.get("candidate_id") or "")
        identity = candidate.get("identity") or {}
        profile_id = str(identity.get("profile_id") or "")
        model_id = str(identity.get("model_id") or "")
        if not candidate_id or not profile_id or not model_id:
            continue
        profile = profile_map.get(profile_id, {"id": profile_id, "status": "retired"})
        option = option_map.get(
            (profile_id, model_id),
            {"value": model_id, "selectable": False, "availability_reason": "model_not_declared"},
        )
        decision = compatibility_decision(
            profile=profile,
            model=option,
            role=role_key,
            run_profile=run_profile,
            criticality=criticality,
            data_class=data_class,
            required_capabilities=required,
            role_profile=ROLE_CAPABILITY_PROFILES.get(role_key, {}),
            candidate_models=profile_options.get(profile_id, ()),
        )
        base_row = next(
            (
                deepcopy(dict(item))
                for item in candidate.get("roles") or ()
                if canonical_role(str(item.get("canonical_role") or "")) == role_key
            ),
            None,
        )
        base_score = deepcopy((base_row or {}).get("score"))
        capacity = dict(capacity_map.get(profile_id) or {
            "profile_id": profile_id,
            "state": "capacity_unknown",
            "source": "not_observed",
        })
        selection_score = _contextual_score(
            base_score,
            candidate_id=candidate_id,
            role=role_key,
            compatibility=decision,
            channel=str(identity.get("channel") or "unknown"),
            capacity=capacity,
            budget=budget,
        )
        states = candidate.get("states") or {}
        selectable_state = _state_value(states, "selectable") is True
        configured = _state_value(states, "configured") is True
        green = _state_value(states, "adapter_green") is True
        capacity_state = str(capacity.get("state") or "capacity_unknown")
        capacity_blocked = capacity_state in {"exhausted_observed", "limit_reached"}
        budget_blocked = (
            str(identity.get("channel") or "") == "api"
            and budget.get("status") == "limit_reached"
        )
        owner_selectable = bool(
            decision.get("allowed") and selectable_state
            and not capacity_blocked and not budget_blocked
        )
        disabled_reason = _disabled_reason(
            decision=decision,
            selectable=selectable_state,
            configured=configured,
            green=green,
            capacity_state=capacity_state,
            budget_blocked=budget_blocked,
        )
        row = {
            **candidate,
            "roles": [base_row] if base_row else [],
            "canonical_role": role_key,
            "base_role_evaluation": base_row,
            "base_score": base_score,
            "selection_score": selection_score,
            "contextual_compatibility": decision,
            "capacity_evidence": capacity,
            "budget_evidence": budget,
            "owner_selectable": owner_selectable,
            "requires_configuration": owner_selectable and not (configured and green),
            "disabled_reason": disabled_reason,
        }
        prepared[candidate_id] = (row, selection_score)
        scores.append(selection_score)

    ranked: list[dict[str, Any]] = []
    for rank, score in enumerate(rank_model_role_scores(scores), start=1):
        candidate_id = str(score.get("candidate_id") or "")
        row, _ = prepared[candidate_id]
        ranked.append({**row, "rank": rank, "selection_reason": _selection_reason(row)})

    winner = next(
        (item for item in ranked if item["selection_score"].get("auto_eligible") is True),
        None,
    )
    runner = next(
        (
            item
            for item in ranked
            if winner is not None and item["candidate_id"] != winner["candidate_id"]
            and item["selection_score"].get("auto_eligible") is True
        ),
        None,
    )
    return {
        "selection_version": MODEL_SELECTION_VERSION,
        "schema_version": read_model.get("schema_version"),
        "score_version": read_model.get("score_version"),
        "content_hash": read_model.get("content_hash"),
        "rollout": "shadow_only",
        "canonical_role": role_key,
        "context": {
            "run_profile": run_profile or None,
            "criticality": criticality,
            "data_class": data_class or None,
            "required_capabilities": required,
            "budget": budget,
        },
        "default": _default_projection(winner, runner),
        "counts": {
            "candidates": len(ranked),
            "auto_eligible": sum(
                item["selection_score"].get("auto_eligible") is True for item in ranked
            ),
            "owner_selectable": sum(item["owner_selectable"] for item in ranked),
        },
        "candidates": ranked,
    }


def same_profile_fallback(
    projection: Mapping[str, Any],
    *,
    profile_id: str,
    failed_model: str,
) -> dict[str, Any] | None:
    """Select recovery from the canonical ranking without crossing adapters.

    Recovery is narrower than owner selection: manual-only candidates are not
    proposed automatically, and family/tier continuity wins before global rank.
    """
    failed_value = str(failed_model or "").strip()
    rows = [dict(item) for item in projection.get("candidates") or ()]
    failed = next(
        (
            item for item in rows
            if str((item.get("identity") or {}).get("profile_id") or "") == profile_id
            and str((item.get("identity") or {}).get("model_id") or "") == failed_value
        ),
        {},
    )
    candidates = [
        item for item in rows
        if str((item.get("identity") or {}).get("profile_id") or "") == profile_id
        and str((item.get("identity") or {}).get("model_id") or "") != failed_value
        and item.get("owner_selectable") is True
        and (
            ((item.get("selection_score") or {}).get("hard_gates") or {})
            .get("automatic_policy", {})
            .get("passed")
            is True
        )
    ]
    if not candidates:
        return None
    failed_tier = str((failed.get("model_metadata") or {}).get("tier") or "")
    failed_family = _model_family(failed_value)
    selected = min(
        candidates,
        key=lambda item: (
            _model_family(str((item.get("identity") or {}).get("model_id") or ""))
            != failed_family,
            bool(failed_tier)
            and str((item.get("model_metadata") or {}).get("tier") or "") != failed_tier,
            int(item.get("rank") or 10**9),
        ),
    )
    identity = selected.get("identity") or {}
    selected_model = str(identity.get("model_id") or "")
    selected_tier = str((selected.get("model_metadata") or {}).get("tier") or "")
    selected_family = _model_family(selected_model)
    return {
        "value": selected_model,
        "profile_id": profile_id,
        "candidate_id": selected.get("candidate_id"),
        "rank": selected.get("rank"),
        "tier": selected_tier or None,
        "selection_reason": selected.get("selection_reason"),
        "failed_model": failed_value,
        "failed_tier": failed_tier or None,
        "failed_family": failed_family or None,
        "changes_tier": bool(failed_tier and selected_tier != failed_tier),
        "changes_family": bool(failed_family and selected_family != failed_family),
    }


def _model_family(model: str) -> str:
    normalized = str(model or "").strip().lower()
    for family, prefixes in {
        "gpt": ("gpt-", "openai/gpt-"),
        "claude": ("claude-", "claude "),
        "gemini": ("gemini-", "gemini "),
        "qwen": ("qwen",),
        "gemma": ("gemma", "google/gemma"),
    }.items():
        if normalized.startswith(prefixes):
            return family
    return normalized.split("/", 1)[-1].split("-", 1)[0]


def _contextual_score(
    base_score: Mapping[str, Any] | None,
    *,
    candidate_id: str,
    role: str,
    compatibility: Mapping[str, Any],
    channel: str,
    capacity: Mapping[str, Any],
    budget: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(base_score, Mapping):
        return {
            "score_version": "model_role_score_v1",
            "candidate_id": candidate_id,
            "canonical_role": role,
            "score": None,
            "score_range": {"minimum": 0.0, "maximum": 100.0},
            "known_weight_percent": 0,
            "unknown_components": ["quality", "capability", "reliability", "economy", "speed"],
            "breakdown": {},
            "confidence": {"value": 0.0, "evidence_rank": 0.0},
            "hard_gates": {},
            "auto_eligible": False,
            "auto_ineligible_reasons": ["role_score_missing"],
            "tie_break": {"evidence_rank": 0.0, "quality": None},
            "rollout": "shadow_only",
            "context_adjustments": {
                "base_score_preserved": True,
                "numeric_components_changed": [],
                "reason": "no_role_score_or_normalized_context_metrics",
            },
        }
    score = deepcopy(dict(base_score))
    gates = score.setdefault("hard_gates", {})
    allowed = compatibility.get("allowed") is True
    gates["compatible"] = {
        "passed": allowed,
        "reason": str(compatibility.get("code") or "compatibility_unknown"),
        "source": MODEL_SELECTION_VERSION,
    }
    context_gate = _CONTEXT_GATE_BY_CODE.get(str(compatibility.get("code") or ""))
    if context_gate:
        gates[context_gate] = {
            "passed": False,
            "reason": str(compatibility.get("code")),
            "source": MODEL_SELECTION_VERSION,
        }
    capacity_state = str(capacity.get("state") or "capacity_unknown")
    capacity_block = capacity_state in {"exhausted_observed", "limit_reached"}
    budget_block = channel == "api" and budget.get("status") == "limit_reached"
    if capacity_block or budget_block:
        reason = "daily_cost_cap_reached" if budget_block else f"capacity:{capacity_state}"
        gates["capacity_available"] = {
            "passed": False,
            "reason": reason,
            "source": str(
                budget.get("source") if budget_block else capacity.get("source") or "subscription_quota_snapshot"
            ),
        }
    numeric_changes = _apply_contextual_economy(
        score, channel=channel, capacity=capacity
    )
    preserved = [
        str(reason)
        for reason in score.get("auto_ineligible_reasons") or ()
        if not str(reason).startswith("gate:")
        and not str(reason).startswith("score_components_unknown:")
        and not str(reason).startswith("confidence_below_")
    ]
    gate_reasons = [
        f"gate:{name}:{gate.get('reason') or 'not_proven'}"
        for name, gate in gates.items()
        if isinstance(gate, Mapping) and gate.get("passed") is not True
    ]
    unknown = list(score.get("unknown_components") or ())
    metric_reasons = ["score_components_unknown:" + ",".join(unknown)] if unknown else []
    confidence = float((score.get("confidence") or {}).get("value") or 0.0)
    confidence_reasons = [f"confidence_below_75:{confidence:g}"] if confidence < 75.0 else []
    score["auto_ineligible_reasons"] = preserved + gate_reasons + metric_reasons + confidence_reasons
    score["auto_eligible"] = not score["auto_ineligible_reasons"]
    score["context_adjustments"] = {
        "base_score_preserved": True,
        "numeric_components_changed": numeric_changes,
        "hard_gates_recomputed": sorted({
            "compatible",
            *([context_gate] if context_gate else []),
            *(["capacity_available"] if capacity_block or budget_block else []),
        }),
        "reason": (
            "owner_quota_policy_normalized_for_exact_profile"
            if numeric_changes
            else "context_changes_gates_only_without_comparable_normalized_metrics"
        ),
    }
    return score


def _apply_contextual_economy(
    score: dict[str, Any], *, channel: str, capacity: Mapping[str, Any]
) -> list[str]:
    """Sustituye economía solo con una política de cuota explícita del owner."""
    forecast = capacity.get("forecast") if isinstance(capacity.get("forecast"), Mapping) else {}
    utilization = forecast.get("utilization")
    if (
        channel != "subscription"
        or forecast.get("source") != "owner_config"
        or utilization is None
    ):
        return []
    numeric = max(0.0, min(1.0, float(utilization)))
    breakdown = score.get("breakdown")
    if not isinstance(breakdown, dict):
        return []
    breakdown["economy"] = {
        "status": "known",
        "value": round((1.0 - numeric) * 100.0, 4),
        "weight_percent": 20,
        "weighted_points": round((1.0 - numeric) * 20.0, 4),
        "reason": "subscription_quota_headroom_from_owner_policy",
        "source": "subscription_quota_snapshot:owner_config",
        "basis": "subscription_quota_pressure",
        "comparison_group": f"subscription_quota:{capacity.get('profile_id')}",
        "burden": round(numeric, 4),
    }
    known_points = 0.0
    known_weight = 0
    unknown: list[str] = []
    for name in ("quality", "capability", "reliability", "economy", "speed"):
        component = breakdown.get(name)
        if isinstance(component, Mapping) and component.get("status") == "known":
            known_weight += int(component.get("weight_percent") or 0)
            known_points += float(component.get("weighted_points") or 0.0)
        else:
            unknown.append(name)
    score["known_weight_percent"] = known_weight
    score["unknown_components"] = unknown
    score["score"] = round(known_points, 4) if known_weight == 100 else None
    score["score_range"] = {
        "minimum": round(known_points, 4),
        "maximum": round(known_points + (100 - known_weight), 4),
    }
    confidence = score.get("confidence")
    if isinstance(confidence, dict):
        evidence_value = float(confidence.get("evidence_value", confidence.get("value", 0)) or 0)
        confidence["metric_completeness_percent"] = known_weight
        confidence["value"] = round(min(evidence_value, float(known_weight)), 4)
    tie = score.get("tie_break")
    if isinstance(tie, dict):
        tie["economy_comparison_group"] = f"subscription_quota:{capacity.get('profile_id')}"
        tie["economic_burden"] = round(numeric, 4)
    return ["economy"]


def _state_value(states: Mapping[str, Any], name: str) -> Any:
    row = states.get(name)
    return row.get("value") if isinstance(row, Mapping) else None


def _disabled_reason(
    *, decision: Mapping[str, Any], selectable: bool, configured: bool,
    green: bool, capacity_state: str, budget_blocked: bool,
) -> str | None:
    if decision.get("allowed") is not True:
        return str(decision.get("reason") or decision.get("code") or "incompatible")
    if not selectable:
        return "El modelo no está marcado como seleccionable en este adapter."
    if capacity_state == "exhausted_observed":
        return "La cuota de este adapter está agotada según el último error observado."
    if capacity_state == "limit_reached":
        return "El límite configurado o reportado para este adapter está alcanzado."
    if budget_blocked:
        return "El presupuesto diario de API está agotado."
    if not configured:
        return "El adapter debe configurarse antes de ejecutar."
    if not green:
        return "El adapter debe superar su health check antes de ser default."
    return None


def _selection_reason(candidate: Mapping[str, Any]) -> str:
    score = candidate.get("selection_score") or {}
    if score.get("auto_eligible") is True:
        return "auto_eligible_contextual_shadow"
    if candidate.get("contextual_compatibility", {}).get("allowed") is not True:
        return f"compatibility:{candidate['contextual_compatibility'].get('code') or 'denied'}"
    return str(next(iter(score.get("auto_ineligible_reasons") or ()), "not_auto_eligible"))


def _default_projection(winner: Mapping[str, Any] | None, runner: Mapping[str, Any] | None) -> dict[str, Any]:
    if winner is None:
        return {
            "candidate_id": None,
            "action": "preserve_explicit_or_require_owner",
            "reason": "no_auto_eligible_candidate",
            "runner_up_candidate_id": None,
            "advantage": None,
        }
    winner_score = winner.get("selection_score") or {}
    runner_score = (runner or {}).get("selection_score") or {}
    advantage: dict[str, Any]
    if winner_score.get("score") is not None and runner_score.get("score") is not None:
        advantage = {
            "kind": "score_delta",
            "value": round(float(winner_score["score"]) - float(runner_score["score"]), 4),
        }
    elif runner is None:
        advantage = {"kind": "only_auto_eligible", "value": None}
    else:
        advantage = {"kind": "canonical_tie_break", "value": None}
    return {
        "candidate_id": winner.get("candidate_id"),
        "action": "recommend_shadow_only",
        "reason": "highest_ranked_auto_eligible_candidate",
        "score": winner_score.get("score"),
        "confidence": (winner_score.get("confidence") or {}).get("value"),
        "breakdown": winner_score.get("breakdown") or {},
        "runner_up_candidate_id": (runner or {}).get("candidate_id"),
        "advantage": advantage,
    }
