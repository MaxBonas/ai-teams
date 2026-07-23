"""Taxonomía estable para no mezclar capacidad general y evidencia exacta."""

from __future__ import annotations

from typing import Any

from aiteam.policies import canonical_role
from aiteam.tools.catalog import default_capabilities_for_role


MODEL_EVIDENCE_TAXONOMY_VERSION = "model_evidence_taxonomy_v1"
GENERAL_CAPABILITY_BENCHMARK = "general_capability_benchmark"
EXACT_ROLE_CANARY = "exact_role_canary"
EXACT_TOOL_FIXTURE = "exact_tool_fixture"
MIN_CASE_FAMILIES_FOR_AUTO = 2

_FAMILIES_BY_CONTRACT = {
    "critical_role_hidden_causal_v2": (
        "tenant_queue_migration",
        "auth_rollout_incident",
    ),
    "context_curator_two_slice_v1": ("auth_migration", "queue_rollout"),
    "context_curator_two_slice_v3": ("auth_migration", "queue_rollout"),
    "durable_review_reject_approve_v1": (
        "faulty_close_rejection",
        "fixed_close_approval",
    ),
    "governed_mcp_allow_deny_recovery_v1": ("advisory_recovery_governance",),
    "independent_test_designer_mutation_v2": ("hidden_mutation_suite",),
    "independent_test_designer_two_family_v3": (
        "pricing_boundary_mutation",
        "job_state_machine_mutation",
    ),
    "adversarial_qa_fix_cycle_v2": ("adversarial_fault_fix_cycle",),
    "adversarial_qa_two_family_v3": (
        "authorization_boundary",
        "webhook_replay_boundary",
    ),
    "coding_hidden_suite_v1": ("tenant_checkout_hidden_suite",),
    "coding_hidden_suite_v3": ("tenant_checkout_hidden_suite",),
    "coding_hidden_suite_two_family_v4": (
        "cli_conversor",
        "config_redactor",
    ),
}

_TIER3_FAMILY_BY_ROLE = {
    "worker": "release_rollback_checklist",
    "file_scout": "tenant_checkout_inspection",
    "web_scout": "governed_advisory_lookup",
    "context_curator": "causal_context_extraction",
}

_TIER3_DIVERSITY_FAMILIES_BY_ROLE = {
    "worker": ("release_rollback_checklist", "incident_dependency_handoff"),
    "file_scout": (
        "tenant_checkout_inspection",
        "payment_idempotency_inspection",
    ),
    "web_scout": (
        "governed_advisory_lookup",
        "governed_queue_advisory_lookup",
    ),
    "mcp_operator": (
        "advisory_recovery_governance",
        "dependency_policy_governance",
    ),
}


def exact_evidence_kind(role: str) -> str:
    role_key = canonical_role(role)
    return (
        EXACT_TOOL_FIXTURE
        if "external_mcp" in default_capabilities_for_role(role_key)
        else EXACT_ROLE_CANARY
    )


def contract_case_families(contract: str, role: str) -> tuple[str, ...]:
    contract_id = str(contract or "")
    if contract_id == "tier3_causal_report_v2":
        family = _TIER3_FAMILY_BY_ROLE.get(canonical_role(role))
        return (family,) if family else ()
    if contract_id == "tier3_two_family_causal_report_v3":
        return _TIER3_DIVERSITY_FAMILIES_BY_ROLE.get(
            canonical_role(role), ()
        )
    return tuple(_FAMILIES_BY_CONTRACT.get(contract_id, ()))


def diversity_status(families: tuple[str, ...] | list[str]) -> str:
    count = len({str(item) for item in families if str(item)})
    return "multi_family" if count >= MIN_CASE_FAMILIES_FOR_AUTO else "single_family"


def evidence_taxonomy_contract() -> dict[str, Any]:
    return {
        "version": MODEL_EVIDENCE_TAXONOMY_VERSION,
        "kinds": {
            GENERAL_CAPABILITY_BENCHMARK: {
                "scope": "model_general",
                "may_supply": ["capability"],
                "may_not_supply": ["exact_role_quality", "tool_contract"],
            },
            EXACT_ROLE_CANARY: {
                "scope": "exact_profile_model_role_contract",
                "may_supply": ["quality", "role_fit"],
                "may_not_supply": ["other_roles", "tool_contract"],
            },
            EXACT_TOOL_FIXTURE: {
                "scope": "exact_profile_model_role_tool_contract",
                "may_supply": ["quality", "role_fit", "tool_contract"],
                "may_not_supply": ["other_roles", "general_capability"],
            },
        },
        "automatic_minimum_case_families": MIN_CASE_FAMILIES_FOR_AUTO,
        "single_family_policy": "quality_visible_but_automatic_blocked",
    }
