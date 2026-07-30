"""Cobertura elegible por tier, rol y carril de autoridad.

Tier 1 es una banda de calidad única. ``lead_ready`` y ``quorum_ready`` son
habilitaciones independientes: un auditor excelente no obtiene autoridad de
Lead por pertenecer al mismo tier.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiteam.provider_identity import profile_identity

TIER_COVERAGE_POLICY_VERSION = "tier_role_coverage_v1"
TIER_1_LANES: dict[str, tuple[str, ...]] = {
    "lead_ready": ("lead",),
    "quorum_ready": ("quorum_auditor",),
    "tier1_support": ("architect", "lead_executor", "team_lead"),
}
TIER_2_ROLES = ("engineer", "mcp_operator", "qa", "reviewer", "test_designer")
TIER_1_ROLE_TO_LANE = {
    role: lane for lane, roles in TIER_1_LANES.items() for role in roles
}
TIER_1_CALIBRATION_CONTRACTS = {
    "lead_ready": {
        "version": "tier1_lead_authority_v1",
        "required_constructs": [
            "durable_planning",
            "hiring",
            "delegation",
            "accountability",
            "recovery",
            "tool_and_workspace_governance",
        ],
    },
    "quorum_ready": {
        "version": "tier1_quorum_authority_v1",
        "required_constructs": [
            "independent_critique",
            "causal_retention",
            "go_no_go_judgment",
            "verifiable_structured_output",
        ],
    },
    "tier1_support": {
        "version": "tier1_support_authority_v1",
        "required_constructs": ["exact_role_contract"],
    },
}


def tier1_authority_gate(
    *, role: str, authority: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Valida el gate ya proyectado sin volver a inferir autoridad.

    Los consumidores reciben ``tier1_authority`` del read model. Para roles
    Tier 1 gobernados, cualquier ausencia, versión antigua, carril distinto o
    estado no habilitado falla cerrado. Los demás roles no quedan afectados.
    """
    role_key = str(role or "").strip().lower()
    lane = TIER_1_ROLE_TO_LANE.get(role_key)
    if lane is None:
        return {
            "applicable": False,
            "allowed": True,
            "policy_version": TIER_COVERAGE_POLICY_VERSION,
            "lane": None,
            "code": "tier1_authority_not_applicable",
            "reason": "El rol no requiere una habilitación Tier 1.",
        }
    row = authority if isinstance(authority, Mapping) else {}
    if not row:
        code = "tier1_authority_missing"
        reason = "Falta la habilitación Tier 1 exacta; la asignación falla cerrada."
    elif row.get("policy_version") != TIER_COVERAGE_POLICY_VERSION:
        code = "tier1_authority_policy_mismatch"
        reason = "La habilitación Tier 1 usa una política distinta o antigua."
    elif row.get("lane") != lane:
        code = "tier1_authority_lane_mismatch"
        reason = f"El rol exige `{lane}` y el modelo presenta otro carril."
    elif row.get("enabled") is not True or row.get("status") != "enabled":
        code = str(row.get("reason_code") or "tier1_authority_blocked")
        reason = f"La habilitación `{lane}` no está activa para este par exacto."
    else:
        code = "tier1_authority_verified"
        reason = f"La habilitación `{lane}` está activa para este par exacto."
    return {
        "applicable": True,
        "allowed": code == "tier1_authority_verified",
        "policy_version": TIER_COVERAGE_POLICY_VERSION,
        "lane": lane,
        "code": code,
        "reason": reason,
        "authority_reason_code": row.get("reason_code"),
    }


def tier1_authority_for_role(
    *,
    role: str,
    model_tier: str,
    evaluation: dict[str, Any],
    compatibility: dict[str, Any],
) -> dict[str, Any]:
    """Deriva autoridad Tier 1 desde evidencia exacta; ausencia falla cerrada."""
    lane = TIER_1_ROLE_TO_LANE.get(str(role))
    if lane is None:
        return {
            "policy_version": TIER_COVERAGE_POLICY_VERSION,
            "lane": None,
            "status": "not_applicable",
            "enabled": None,
            "reason_code": "role_not_tier1",
            "scope": "exact_profile_model_role",
            "evidence_receipts": [],
        }
    declared_contract = TIER_1_CALIBRATION_CONTRACTS[lane]
    calibration_contract = {
        "version": declared_contract["version"],
        "required_constructs": list(declared_contract["required_constructs"]),
    }

    status = str(evaluation.get("status") or "missing")
    stale_reasons = list(evaluation.get("stale_reasons") or ())
    receipts = sorted(
        str(item) for item in evaluation.get("evidence_receipts") or ()
    )
    if str(model_tier or "") != "premium":
        reason = "tier1_capability_band_required"
    elif compatibility.get("allowed") is not True:
        reason = "role_incompatible"
    elif status != "calibrated":
        reason = "exact_role_calibration_required"
    elif stale_reasons:
        reason = "exact_role_calibration_stale"
    elif not receipts:
        reason = "exact_role_receipt_required"
    else:
        reason = "exact_role_calibration_verified"
    enabled = reason == "exact_role_calibration_verified"
    return {
        "policy_version": TIER_COVERAGE_POLICY_VERSION,
        "lane": lane,
        "calibration_contract": calibration_contract,
        "status": "enabled" if enabled else "blocked",
        "enabled": enabled,
        "reason_code": reason,
        "scope": "exact_profile_model_role",
        "evaluation_status": status,
        "evaluated_at": evaluation.get("evaluated_at"),
        "provider_version": evaluation.get("provider_version"),
        "prompt_version": evaluation.get("prompt_version"),
        "stale_reasons": stale_reasons,
        "evidence_receipts": receipts,
    }


def audit_model_tier_coverage(
    evaluation_report: dict[str, Any],
    *,
    profiles: list[dict[str, Any]],
    target_per_role: int = 2,
) -> dict[str, Any]:
    """Proyecta únicamente pares que ya superan todos los gates conservadores."""
    profile_by_id = {
        str(profile.get("id") or ""): profile for profile in profiles
    }
    role_candidates: dict[str, list[dict[str, Any]]] = {}
    excluded: list[dict[str, Any]] = []

    for row in evaluation_report.get("rows") or []:
        profile_id = str(row.get("profile_id") or "")
        model = str(row.get("model") or "")
        tier = str(row.get("tier") or "")
        profile = profile_by_id.get(profile_id, {"id": profile_id})
        identity = profile_identity(profile, selected_model=model)
        base_eligible = (
            row.get("automatic") is True
            and row.get("executable") is True
            and bool(row.get("maintenance_allowed", True))
            and str((row.get("owner_preference") or {}).get("state") or "")
            != "archived"
        )
        for role_row in row.get("roles") or []:
            role = role_row.get("role")
            if not role:
                continue
            expected_tier = (
                "premium"
                if role in {
                    role_name
                    for roles in TIER_1_LANES.values()
                    for role_name in roles
                }
                else "standard"
                if role in TIER_2_ROLES
                else None
            )
            if expected_tier is None or tier != expected_tier:
                continue
            eligible = base_eligible and role_row.get("status") == "calibrated"
            candidate = {
                "profile_id": profile_id,
                "model": model,
                "role": role,
                "tier": tier,
                "status": role_row.get("status"),
                **identity,
            }
            if eligible:
                role_candidates.setdefault(str(role), []).append(candidate)
            else:
                reasons = []
                if row.get("automatic") is not True:
                    reasons.append("not_automatic")
                if row.get("executable") is not True:
                    reasons.append("not_executable")
                if not row.get("maintenance_allowed", True):
                    reasons.append("maintenance_not_allowed")
                if role_row.get("status") != "calibrated":
                    reasons.append(f"status_{role_row.get('status')}")
                excluded.append({**candidate, "reasons": reasons})

    def project_role(role: str) -> dict[str, Any]:
        candidates = sorted(
            role_candidates.get(role, []),
            key=lambda item: (item["profile_id"], item["model"]),
        )
        perspectives = sorted({item["perspective_key"] for item in candidates})
        pools = sorted({item["capacity_pool"] for item in candidates})
        count = len(candidates)
        if count == 0:
            status = "no_eligible"
        elif count < target_per_role:
            status = "single_point"
        elif len(perspectives) < 2 or len(pools) < 2:
            status = "diversity_gap"
        else:
            status = "covered"
        return {
            "role": role,
            "target": target_per_role,
            "eligible_count": count,
            "perspective_count": len(perspectives),
            "capacity_pool_count": len(pools),
            "status": status,
            "perspectives": perspectives,
            "capacity_pools": pools,
            "candidates": candidates,
        }

    tier1_lanes = {
        lane: {
            "roles": [project_role(role) for role in roles],
        }
        for lane, roles in TIER_1_LANES.items()
    }
    tier2 = [project_role(role) for role in TIER_2_ROLES]
    all_roles = [
        role
        for lane in tier1_lanes.values()
        for role in lane["roles"]
    ] + tier2
    gaps = [
        {
            "role": role["role"],
            "status": role["status"],
            "missing_candidates": max(0, target_per_role - role["eligible_count"]),
            "missing_perspectives": max(0, 2 - role["perspective_count"]),
            "missing_capacity_pools": max(0, 2 - role["capacity_pool_count"]),
        }
        for role in all_roles
        if role["status"] != "covered"
    ]
    return {
        "schema_version": 1,
        "policy_version": TIER_COVERAGE_POLICY_VERSION,
        "source_observed_at": evaluation_report.get("observed_at"),
        "policy": {
            "tier_1_quality": "maximum_only_no_numeric_promotion",
            "lead_ready_gate": "exact_role_lead_calibrated",
            "quorum_ready_gate": "exact_role_quorum_auditor_calibrated",
            "authority_invariant": "quorum_ready_never_implies_lead_ready",
            "eligibility": (
                "automatic_and_executable_and_not_archived_and_exact_role_calibrated"
            ),
            "target_per_role": target_per_role,
            "diversity_target": "two_perspectives_and_two_capacity_pools",
        },
        "tier_1": {"lanes": tier1_lanes},
        "tier_2": {"roles": tier2},
        "gaps": gaps,
        "excluded": excluded,
        "complete": not gaps,
    }
