from aiteam.model_evidence_taxonomy import (
    EXACT_ROLE_CANARY,
    EXACT_TOOL_FIXTURE,
    GENERAL_CAPABILITY_BENCHMARK,
    MODEL_EVIDENCE_TAXONOMY_VERSION,
    contract_case_families,
    diversity_status,
    evidence_taxonomy_contract,
    exact_evidence_kind,
)


def test_taxonomy_separates_general_role_and_tool_evidence() -> None:
    contract = evidence_taxonomy_contract()

    assert contract["version"] == MODEL_EVIDENCE_TAXONOMY_VERSION
    assert set(contract["kinds"]) == {
        GENERAL_CAPABILITY_BENCHMARK,
        EXACT_ROLE_CANARY,
        EXACT_TOOL_FIXTURE,
    }
    assert exact_evidence_kind("engineer") == EXACT_ROLE_CANARY
    assert exact_evidence_kind("web_scout") == EXACT_TOOL_FIXTURE
    assert exact_evidence_kind("mcp_operator") == EXACT_TOOL_FIXTURE


def test_case_families_are_contract_and_role_specific() -> None:
    assert contract_case_families(
        "critical_role_hidden_causal_v2", "lead"
    ) == ("tenant_queue_migration", "auth_rollout_incident")
    assert contract_case_families(
        "tier3_causal_report_v2", "worker"
    ) == ("release_rollback_checklist",)
    assert contract_case_families("tier3_causal_report_v2", "web_scout") == (
        "governed_advisory_lookup",
    )
    assert diversity_status(["a", "b"]) == "multi_family"
    assert diversity_status(["a", "a"]) == "single_family"
