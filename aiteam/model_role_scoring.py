"""Scorer puro y auditable por candidato operacional + rol canónico.

La política ``model_role_score_v1`` funciona únicamente en shadow. No consulta
DB, filesystem, secrets o red y no modifica defaults. Las métricas de entrada
ya deben estar normalizadas contra candidatos comparables del mismo contexto.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from aiteam.policies import canonical_role


MODEL_ROLE_SCORE_VERSION = "model_role_score_v1"
MODEL_ROLE_SCORE_WEIGHTS = {
    "quality": 40,
    "capability": 15,
    "reliability": 15,
    "economy": 20,
    "speed": 10,
}
AUTO_CONFIDENCE_MINIMUM = 75.0

REQUIRED_AUTO_GATES = (
    "configured",
    "adapter_green",
    "model_verified",
    "selectable",
    "compatible",
    "automatic_policy",
    "calibrated",
    "fresh",
    "privacy",
    "tools",
    "workspace",
    "structured_output",
    "capacity_available",
)

_ECONOMY_BASIS_BY_CHANNEL = {
    "api": "api_cost_per_accepted_task",
    "subscription": "subscription_quota_pressure",
    "local": "local_resource_throughput",
    "free_gateway": "gateway_capacity_pressure",
}

_EVIDENCE_CLASS_SCORE = {
    "behavioral_deterministic": 100.0,
    "causal_judge": 90.0,
    "human_sampled": 90.0,
    "static_analysis": 80.0,
    "structural_contract": 65.0,
    "lexical_rubric": 40.0,
    "provider_self_report": 20.0,
    "unknown": 0.0,
}

_EVIDENCE_STATUS_SCORE = {
    "calibrated": 100.0,
    "partial": 45.0,
    "requires_canary": 0.0,
    "requires_tool_fixture": 0.0,
    "manual_candidate": 0.0,
    "blocked": 0.0,
    "untested": 0.0,
}

_GOODHART_SCORE = {
    "low": 100.0,
    "moderate": 70.0,
    "material": 40.0,
    "high": 0.0,
    "unknown": 0.0,
}

_CONFIDENCE_WEIGHTS = {
    "evidence_status": 20,
    "evidence_class": 25,
    "seeds": 15,
    "cases": 10,
    "tool_coverage": 10,
    "freshness": 10,
    "goodhart": 10,
}


def score_model_role(
    *,
    candidate: Mapping[str, Any],
    role: str,
    components: Mapping[str, Mapping[str, Any] | None],
    evidence: Mapping[str, Any],
    hard_gates: Mapping[str, Any],
) -> dict[str, Any]:
    """Calcula score, confianza y gates sin seleccionar ni mutar estado."""
    candidate_id, identity = _candidate_identity(candidate)
    role_key = canonical_role(role)
    if not role_key:
        raise ValueError("canonical role is required")

    breakdown: dict[str, dict[str, Any]] = {}
    known_weight = 0
    known_points = 0.0
    unknown_components: list[str] = []
    channel = str(identity.get("channel") or "unknown")
    for name, weight in MODEL_ROLE_SCORE_WEIGHTS.items():
        normalized = _normalize_component(
            name,
            components.get(name),
            weight=weight,
            channel=channel,
        )
        breakdown[name] = normalized
        if normalized["status"] == "known":
            known_weight += weight
            known_points += float(normalized["weighted_points"])
        else:
            unknown_components.append(name)

    complete = known_weight == 100
    score = round(known_points, 4) if complete else None
    confidence = _confidence(evidence)
    evidence_confidence = float(confidence["value"])
    confidence["evidence_value"] = evidence_confidence
    confidence["metric_completeness_percent"] = known_weight
    confidence["value"] = round(min(evidence_confidence, float(known_weight)), 4)
    confidence["caps"] = ["unknown_score_components"] if known_weight < 100 else []
    gates = _normalize_gates(hard_gates, evidence=evidence)
    ineligible_reasons = [
        f"gate:{name}:{gate['reason']}"
        for name, gate in gates.items()
        if gate["passed"] is not True
    ]
    if not complete:
        ineligible_reasons.append(
            "score_components_unknown:" + ",".join(unknown_components)
        )
    if confidence["value"] < AUTO_CONFIDENCE_MINIMUM:
        ineligible_reasons.append(
            f"confidence_below_{AUTO_CONFIDENCE_MINIMUM:g}:{confidence['value']:g}"
        )

    return {
        "score_version": MODEL_ROLE_SCORE_VERSION,
        "candidate_id": candidate_id,
        "identity": identity,
        "canonical_role": role_key,
        "score": score,
        "score_range": {
            "minimum": round(known_points, 4),
            "maximum": round(known_points + (100 - known_weight), 4),
        },
        "known_weight_percent": known_weight,
        "unknown_components": unknown_components,
        "breakdown": breakdown,
        "confidence": confidence,
        "hard_gates": gates,
        "auto_eligible": not ineligible_reasons,
        "auto_ineligible_reasons": ineligible_reasons,
        "tie_break": {
            "evidence_rank": confidence["evidence_rank"],
            "quality": breakdown["quality"]["value"],
            "economy_comparison_group": breakdown["economy"].get("comparison_group"),
            "economic_burden": breakdown["economy"].get("burden"),
            "speed_comparison_group": breakdown["speed"].get("comparison_group"),
            "latency_ms": breakdown["speed"].get("latency_ms"),
            "canonical_identity": candidate_id,
        },
        "rollout": "shadow_only",
    }


def rank_model_role_scores(scores: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Orden estable sin comparar economía/latencia de grupos incompatibles.

    Primero aplica elegibilidad, score completo, evidencia y calidad. Economía
    y latencia solo desempatan dentro de un grupo base cuando todos sus miembros
    declaran la misma unidad/contexto comparable. La identidad rompe el empate
    final, por lo que el orden no depende del orden de entrada.
    """
    rows = [dict(item) for item in scores]
    rows.sort(key=_base_rank_key)
    output: list[dict[str, Any]] = []
    start = 0
    while start < len(rows):
        base = _base_rank_key(rows[start])[:-1]
        end = start + 1
        while end < len(rows) and _base_rank_key(rows[end])[:-1] == base:
            end += 1
        output.extend(_rank_tied_group(rows[start:end]))
        start = end
    return output


def _normalize_component(
    name: str,
    component: Mapping[str, Any] | None,
    *,
    weight: int,
    channel: str,
) -> dict[str, Any]:
    raw = dict(component or {})
    value = raw.get("value")
    reason = str(raw.get("reason") or "metric_not_observed")
    if name == "economy" and value is not None:
        expected = _ECONOMY_BASIS_BY_CHANNEL.get(channel)
        basis = str(raw.get("basis") or "")
        if expected is None or basis != expected:
            value = None
            reason = f"economy_basis_mismatch:{basis or 'missing'}:{expected or 'unknown_channel'}"
    if value is None:
        return {
            **raw,
            "status": "unknown",
            "value": None,
            "weight_percent": weight,
            "weighted_points": None,
            "reason": reason,
            "source": str(raw.get("source") or "unknown"),
        }
    numeric = float(value)
    if not 0.0 <= numeric <= 100.0:
        raise ValueError(f"{name} component must be between 0 and 100")
    return {
        **raw,
        "status": "known",
        "value": numeric,
        "weight_percent": weight,
        "weighted_points": round(numeric * weight / 100.0, 4),
        "reason": reason if reason != "metric_not_observed" else "metric_observed",
        "source": str(raw.get("source") or "unknown"),
    }


def _confidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    status = str(evidence.get("status") or "untested").lower()
    classes = evidence.get("classes") or [evidence.get("class") or "unknown"]
    if isinstance(classes, str):
        classes = [classes]
    class_names = sorted({str(item).lower() for item in classes})
    class_value = max(
        (_EVIDENCE_CLASS_SCORE.get(item, 0.0) for item in class_names), default=0.0
    )
    seeds = max(0, int(evidence.get("seeds") or 0))
    cases = max(0, int(evidence.get("cases") or 0))
    required_tools = {str(item) for item in evidence.get("required_tools") or ()}
    covered_tools = {str(item) for item in evidence.get("covered_tools") or ()}
    tool_value = (
        100.0
        if not required_tools
        else 100.0 * len(required_tools & covered_tools) / len(required_tools)
    )
    fresh = evidence.get("fresh") is True
    goodhart = str(evidence.get("goodhart_risk") or "unknown").lower()
    factors = {
        "evidence_status": _EVIDENCE_STATUS_SCORE.get(status, 0.0),
        "evidence_class": class_value,
        "seeds": min(100.0, seeds / 3 * 100.0),
        "cases": min(100.0, cases / 2 * 100.0),
        "tool_coverage": tool_value,
        "freshness": 100.0 if fresh else 0.0,
        "goodhart": _GOODHART_SCORE.get(goodhart, 0.0),
    }
    weighted = {
        name: {
            "value": round(value, 4),
            "weight_percent": _CONFIDENCE_WEIGHTS[name],
            "weighted_points": round(value * _CONFIDENCE_WEIGHTS[name] / 100.0, 4),
        }
        for name, value in factors.items()
    }
    value = round(sum(item["weighted_points"] for item in weighted.values()), 4)
    return {
        "value": value,
        "minimum_for_auto": AUTO_CONFIDENCE_MINIMUM,
        "breakdown": weighted,
        "evidence_status": status,
        "evidence_classes": class_names,
        "evidence_rank": round((factors["evidence_status"] + class_value) / 2.0, 4),
        "seeds": seeds,
        "cases": cases,
        "required_tools": sorted(required_tools),
        "covered_tools": sorted(covered_tools),
        "fresh": fresh,
        "provider_version": evidence.get("provider_version"),
        "evaluated_at": evidence.get("evaluated_at"),
        "receipts": sorted(str(item) for item in evidence.get("receipts") or ()),
        "unmeasured_constructs": sorted(
            str(item) for item in evidence.get("unmeasured_constructs") or ()
        ),
        "goodhart_risk": goodhart,
    }


def _normalize_gates(
    hard_gates: Mapping[str, Any], *, evidence: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    gates: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_AUTO_GATES:
        raw = hard_gates.get(name)
        if isinstance(raw, Mapping):
            passed = raw.get("passed")
            reason = str(
                raw.get("reason") or ("passed" if passed is True else "not_proven")
            )
            source = str(raw.get("source") or "caller")
        else:
            passed = raw if isinstance(raw, bool) else None
            reason = (
                "passed"
                if passed is True
                else "failed"
                if passed is False
                else "not_proven"
            )
            source = "caller"
        gates[name] = {"passed": passed, "reason": reason, "source": source}

    if str(evidence.get("status") or "untested").lower() != "calibrated":
        gates["calibrated"] = {
            "passed": False,
            "reason": "exact_role_evidence_not_calibrated",
            "source": "evidence",
        }
    if evidence.get("fresh") is not True:
        gates["fresh"] = {
            "passed": False,
            "reason": "evidence_stale_or_unproven",
            "source": "evidence",
        }
    return gates


def _candidate_identity(candidate: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    identity = candidate.get("identity")
    if not candidate_id or not isinstance(identity, Mapping):
        raise ValueError("candidate_id and operational identity are required")
    return candidate_id, dict(identity)


def _base_rank_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    score = row.get("score")
    tie = row.get("tie_break") if isinstance(row.get("tie_break"), Mapping) else {}
    quality = tie.get("quality")
    return (
        0 if row.get("auto_eligible") is True else 1,
        0 if score is not None else 1,
        -float(
            score if score is not None else row.get("score_range", {}).get("minimum", 0)
        ),
        -float(tie.get("evidence_rank") or 0),
        -float(quality or 0),
        str(row.get("candidate_id") or ""),
    )


def _rank_tied_group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) < 2:
        return rows
    economic_groups = {
        row.get("tie_break", {}).get("economy_comparison_group") for row in rows
    }
    compare_economy = len(economic_groups) == 1 and None not in economic_groups
    speed_groups = {
        row.get("tie_break", {}).get("speed_comparison_group") for row in rows
    }
    compare_speed = len(speed_groups) == 1 and None not in speed_groups

    def key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        tie = row.get("tie_break") if isinstance(row.get("tie_break"), Mapping) else {}
        burden = tie.get("economic_burden")
        latency = tie.get("latency_ms")
        return (
            float(burden) if compare_economy and burden is not None else float("inf"),
            float(latency) if compare_speed and latency is not None else float("inf"),
            str(row.get("candidate_id") or ""),
        )

    return sorted(rows, key=key)
