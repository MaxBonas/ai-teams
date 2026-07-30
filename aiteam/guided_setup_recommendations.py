"""Recomendaciones progresivas y no redundantes para el asistente guiado."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiteam.guided_setup_coverage import SCHEMA_VERSION as COVERAGE_SCHEMA_VERSION
from aiteam.guided_setup_preparation import SCHEMA_VERSION as PREPARATION_SCHEMA_VERSION

SCHEMA_VERSION = "guided_setup_recommendations_v1"
_STAGE_ORDER = (
    "installation",
    "version",
    "authentication",
    "catalog",
    "health",
    "contract",
)


def build_progressive_recommendations(
    coverage: Mapping[str, Any],
    preparation: Mapping[str, Any],
) -> dict[str, Any]:
    """Order the minimum viable route before optional coverage expansion."""
    if coverage.get("schema_version") != COVERAGE_SCHEMA_VERSION:
        raise ValueError("guided_setup_recommendations_coverage_schema_mismatch")
    if preparation.get("schema_version") != PREPARATION_SCHEMA_VERSION:
        raise ValueError("guided_setup_recommendations_preparation_schema_mismatch")

    phases = [
        _minimum_lead_phase(coverage, preparation),
        _profile_phase(
            coverage,
            profile="lead_quorum",
            code="expand_quorum_diversity",
            label="Añadir quorum independiente",
            priority=20,
        ),
        _profile_phase(
            coverage,
            profile="full_team",
            code="complete_full_team",
            label="Completar equipo de implementación",
            priority=30,
        ),
        _economic_worker_phase(coverage),
    ]
    actions = [
        action
        for phase in phases
        for action in phase["actions"]
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": {
            "order": [
                "minimum_lead",
                "quorum_diversity",
                "full_team",
                "economic_workers",
            ],
            "configure_everything": False,
            "automatic_install": False,
            "automatic_default_change": False,
            "ready_adapter_reinstall_allowed": False,
            "optional_expansion_requires_owner_consent": True,
        },
        "recommended_profile": coverage["recommended_profile"],
        "ready_to_continue": coverage["recommended_profile_ready"],
        "phases": phases,
        "actions": actions,
        "next_action": actions[0] if actions else None,
    }


def _minimum_lead_phase(
    coverage: Mapping[str, Any],
    preparation: Mapping[str, Any],
) -> dict[str, Any]:
    profile = coverage["profiles"]["solo_lead"]
    lead_candidates = coverage["roles"]["team_lead"]["candidates"]
    if profile["ready"]:
        return _phase(
            "minimum_lead",
            10,
            "ready",
            [],
            recommendation=lead_candidates[0] if lead_candidates else None,
        )

    adapters = [
        dict(row)
        for row in preparation.get("adapters", ())
        if isinstance(row, Mapping) and row.get("primary_candidate") is True
    ]
    incomplete = [row for row in adapters if row.get("state") != "ready"]
    if incomplete:
        preferred = incomplete[0]
        pending_stages = [
            stage
            for stage in _STAGE_ORDER
            if (preferred.get("stages") or {}).get(stage)
            not in {"passed", "not_applicable"}
        ]
        action = {
            "code": "complete_lead_adapter",
            "phase": "minimum_lead",
            "priority": 10,
            "required": True,
            "profile_id": str(preferred.get("id") or ""),
            "pending_stages": pending_stages,
            "alternative_profile_ids": [
                str(row.get("id") or "") for row in incomplete[1:]
            ],
            "reason": "lead_adapter_not_ready",
        }
    elif adapters:
        action = {
            "code": "restore_lead_model_eligibility",
            "phase": "minimum_lead",
            "priority": 10,
            "required": True,
            "profile_id": None,
            "pending_stages": [],
            "alternative_profile_ids": [],
            "reason": "ready_adapter_without_auto_eligible_lead",
        }
    else:
        action = {
            "code": "choose_lead_channel",
            "phase": "minimum_lead",
            "priority": 10,
            "required": True,
            "profile_id": None,
            "pending_stages": [],
            "alternative_profile_ids": [],
            "reason": "no_lead_channel_declared",
        }
    return _phase("minimum_lead", 10, "blocked", [action])


def _profile_phase(
    coverage: Mapping[str, Any],
    *,
    profile: str,
    code: str,
    label: str,
    priority: int,
) -> dict[str, Any]:
    row = coverage["profiles"][profile]
    if row["ready"]:
        return _phase(profile, priority, "ready", [])
    gaps = [
        {
            "role": requirement["role"],
            "status": requirement["status"],
            "missing_count": requirement["missing_count"],
            "perspective_count": requirement["perspective_count"],
            "capacity_pool_count": requirement["capacity_pool_count"],
        }
        for requirement in row["requirements"]
        if requirement["status"] != "covered"
    ]
    return _phase(
        profile,
        priority,
        "optional_blocked",
        [
            {
                "code": code,
                "phase": profile,
                "priority": priority,
                "required": coverage["recommended_profile"] == profile,
                "profile_id": None,
                "gaps": gaps,
                "reason": label,
            }
        ],
    )


def _economic_worker_phase(coverage: Mapping[str, Any]) -> dict[str, Any]:
    candidates = coverage["roles"].get("worker", {}).get("candidates", [])
    free = [
        row
        for row in candidates
        if (row.get("economics") or {}).get("class") == "zero_marginal"
    ]
    if not free:
        return _phase("economic_workers", 40, "unavailable", [])
    return _phase(
        "economic_workers",
        40,
        "available",
        [
            {
                "code": "consider_economic_worker",
                "phase": "economic_workers",
                "priority": 40,
                "required": False,
                "profile_id": free[0]["profile_id"],
                "candidate": free[0],
                "reason": "zero_marginal_auto_eligible_worker",
            }
        ],
    )


def _phase(
    phase_id: str,
    priority: int,
    status: str,
    actions: list[dict[str, Any]],
    *,
    recommendation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": phase_id,
        "priority": priority,
        "status": status,
        "actions": actions,
        "recommendation": dict(recommendation) if recommendation else None,
    }
