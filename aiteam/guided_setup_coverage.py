"""Cobertura explicable de perfiles de equipo para el asistente guiado."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiteam.model_selection import candidate_is_automation_eligible
from aiteam.policies import canonical_role

SCHEMA_VERSION = "guided_setup_coverage_v1"
ADVISORY_ROLES = ("worker",)
PROFILE_REQUIREMENTS: dict[str, tuple[dict[str, Any], ...]] = {
    "solo_lead": (
        {"role": "team_lead", "count": 1, "diversity": False},
    ),
    "lead_quorum": (
        {"role": "team_lead", "count": 1, "diversity": False},
        {"role": "quorum_auditor", "count": 2, "diversity": True},
    ),
    "full_team": (
        {"role": "team_lead", "count": 1, "diversity": False},
        {"role": "engineer", "count": 1, "diversity": False},
        {"role": "reviewer", "count": 1, "diversity": False},
    ),
}


def build_guided_setup_coverage(
    selections_by_role: Mapping[str, Mapping[str, Any]],
    *,
    ready_profile_ids: set[str] | None = None,
    recommended_profile: str = "solo_lead",
) -> dict[str, Any]:
    if recommended_profile not in PROFILE_REQUIREMENTS:
        raise ValueError("guided_setup_coverage_profile_invalid")
    allowed_profiles = (
        {str(item) for item in ready_profile_ids}
        if ready_profile_ids is not None
        else None
    )
    role_rows: dict[str, dict[str, Any]] = {}
    all_roles = {
        requirement["role"]
        for requirements in PROFILE_REQUIREMENTS.values()
        for requirement in requirements
    } | set(ADVISORY_ROLES)
    for role in sorted(all_roles):
        projection = selections_by_role.get(role)
        if not isinstance(projection, Mapping):
            role_rows[role] = _missing_role(role)
            continue
        if projection.get("selection_version") != "model_selection_v1":
            raise ValueError("guided_setup_coverage_selection_schema_mismatch")
        if canonical_role(str(projection.get("canonical_role") or "")) != role:
            raise ValueError("guided_setup_coverage_selection_role_mismatch")
        candidates = [
            dict(row)
            for row in projection.get("candidates", [])
            if isinstance(row, Mapping)
        ]
        eligible = []
        excluded = []
        for candidate in candidates:
            automation_eligible = candidate_is_automation_eligible(candidate)
            adapter_prepared = (
                allowed_profiles is None
                or _profile_id(candidate) in allowed_profiles
            )
            if automation_eligible and adapter_prepared:
                eligible.append(candidate)
            else:
                excluded.append((candidate, automation_eligible, adapter_prepared))
        role_rows[role] = {
            "role": role,
            "candidate_count": len(candidates),
            "eligible_count": len(eligible),
            "candidates": [
                _candidate_summary(row, coverage_eligible=True)
                for row in eligible
            ],
            "excluded_candidates": [
                _candidate_summary(
                    row,
                    coverage_eligible=False,
                    adapter_prepared=adapter_prepared,
                    automation_eligible=automation_eligible,
                )
                for row, automation_eligible, adapter_prepared in excluded
            ],
            "excluded_count": len(excluded),
            "status": "covered" if eligible else "no_eligible",
        }

    profiles: dict[str, dict[str, Any]] = {}
    for profile_name, requirements in PROFILE_REQUIREMENTS.items():
        requirement_rows = []
        for requirement in requirements:
            role = requirement["role"]
            candidates = role_rows[role]["candidates"]
            needed = int(requirement["count"])
            perspectives = {
                str(row["perspective_key"])
                for row in candidates
                if row["perspective_key"]
            }
            pools = {
                str(row["capacity_pool"])
                for row in candidates
                if row["capacity_pool"]
            }
            count_ready = len(candidates) >= needed
            diversity_ready = (
                not requirement["diversity"]
                or len(perspectives) >= needed
                and len(pools) >= needed
            )
            status = (
                "covered"
                if count_ready and diversity_ready
                else "diversity_gap"
                if count_ready
                else "missing"
            )
            requirement_rows.append(
                {
                    "role": role,
                    "required_count": needed,
                    "eligible_count": len(candidates),
                    "requires_diversity": bool(requirement["diversity"]),
                    "perspective_count": len(perspectives),
                    "capacity_pool_count": len(pools),
                    "status": status,
                    "missing_count": max(0, needed - len(candidates)),
                }
            )
        ready = all(row["status"] == "covered" for row in requirement_rows)
        profiles[profile_name] = {
            "profile": profile_name,
            "ready": ready,
            "status": "covered" if ready else "blocked",
            "requirements": requirement_rows,
            "blockers": [
                f"{row['role']}:{row['status']}"
                for row in requirement_rows
                if row["status"] != "covered"
            ],
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": {
            "source": "model_selection_v1",
            "eligibility": "candidate_is_automation_eligible",
            "discovery_grants_coverage": False,
            "manual_selection_grants_coverage": False,
            "quorum_requires_distinct_perspectives_and_capacity_pools": True,
            "local_marginal_cost": "zero",
        },
        "recommended_profile": recommended_profile,
        "recommended_profile_ready": profiles[recommended_profile]["ready"],
        "profiles": profiles,
        "roles": role_rows,
    }


def _missing_role(role: str) -> dict[str, Any]:
    return {
        "role": role,
        "candidate_count": 0,
        "eligible_count": 0,
        "candidates": [],
        "excluded_candidates": [],
        "excluded_count": 0,
        "status": "no_projection",
    }


def _candidate_summary(
    candidate: Mapping[str, Any],
    *,
    coverage_eligible: bool,
    adapter_prepared: bool = True,
    automation_eligible: bool = True,
) -> dict[str, Any]:
    identity = candidate.get("identity") or {}
    model = candidate.get("model_metadata") or {}
    compatibility = candidate.get("contextual_compatibility") or {}
    score = candidate.get("selection_score") or {}
    hard_gates = score.get("hard_gates") or {}
    channel = str(identity.get("channel") or "unknown")
    exclusion_reasons = [
        str(item)
        for item in score.get("auto_ineligible_reasons") or ()
        if str(item)
    ]
    if not adapter_prepared:
        exclusion_reasons.append("adapter_not_prepared_in_setup")
    if not automation_eligible and not exclusion_reasons:
        exclusion_reasons.append(
            str(candidate.get("disabled_reason") or "automation_gate_failed")
        )
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "profile_id": str(identity.get("profile_id") or ""),
        "model_id": str(identity.get("model_id") or ""),
        "provider": str(
            identity.get("provider_org")
            or identity.get("model_vendor")
            or ""
        ),
        "channel": channel,
        "tier": model.get("tier"),
        "rank": candidate.get("rank"),
        "score": score.get("score"),
        "selection_reason": candidate.get("selection_reason"),
        "coverage_eligible": coverage_eligible,
        "owner_selectable": candidate.get("owner_selectable") is True,
        "disabled_reason": candidate.get("disabled_reason"),
        "exclusion_reasons": sorted(set(exclusion_reasons)),
        "perspective_key": identity.get("perspective_key"),
        "capacity_pool": identity.get("capacity_pool"),
        "economics": {
            "class": _economy_class(channel, candidate),
            "marginal_cost": "zero"
            if _economy_class(channel, candidate) == "zero_marginal"
            else "metered_or_quota",
            "price_note": model.get("price_note"),
        },
        "privacy": {
            "allowed": compatibility.get("allowed") is True,
            "code": compatibility.get("code"),
        },
        "capabilities": sorted(str(item) for item in model.get("caps") or ()),
        "gates": {
            str(name): bool((gate or {}).get("passed"))
            for name, gate in hard_gates.items()
            if isinstance(gate, Mapping)
        },
    }


def _economy_class(channel: str, candidate: Mapping[str, Any]) -> str:
    model = candidate.get("model_metadata") or {}
    if channel in {"local", "subscription", "free_gateway"}:
        return "zero_marginal"
    if model.get("free_tier") is True or model.get("cost_class") == "free":
        return "zero_marginal"
    return "metered"


def _profile_id(candidate: Mapping[str, Any]) -> str:
    return str((candidate.get("identity") or {}).get("profile_id") or "")
