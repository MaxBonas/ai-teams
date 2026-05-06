from __future__ import annotations

from aiteam.run_profiles import (
    FULL_TEAM,
    LEAD_QUORUM,
    SOLO_LEAD,
    build_default_team_blueprint,
    normalize_run_profile,
    profile_config,
)


def test_run_profile_normalization_maps_legacy_names_to_v2_profiles() -> None:
    assert normalize_run_profile("solo_lead") == SOLO_LEAD
    assert normalize_run_profile("direct") == SOLO_LEAD
    assert normalize_run_profile("", chat_mode="direct") == SOLO_LEAD
    assert normalize_run_profile("lead_quorum") == LEAD_QUORUM
    assert normalize_run_profile("architecture_review") == LEAD_QUORUM
    assert normalize_run_profile("team_advanced") == FULL_TEAM
    assert normalize_run_profile("ai_team_basic") == FULL_TEAM
    assert normalize_run_profile("ai_teams_full") == FULL_TEAM


def test_solo_lead_blueprint_has_no_worker_delegation() -> None:
    blueprint = build_default_team_blueprint("CHAT-SOLO", "solo_lead")

    assert blueprint.profile == SOLO_LEAD
    assert [agent.agent_id for agent in blueprint.agents] == ["role:team_lead"]
    assert blueprint.cost_policy["delegation_allowed"] is False
    assert blueprint.cost_policy["cheap_delegate_roles"] == []
    assert profile_config(SOLO_LEAD).requires_review_gate is False
    assert profile_config(SOLO_LEAD).requires_qa_gate is False


def test_lead_quorum_blueprint_uses_senior_auditors_without_worker_hiring() -> None:
    blueprint = build_default_team_blueprint("CHAT-QUORUM", "lead_quorum")

    assert blueprint.profile == LEAD_QUORUM
    assert [agent.agent_id for agent in blueprint.agents] == [
        "role:team_lead",
        "role:quorum_auditor_1",
        "role:quorum_auditor_2",
    ]
    assert all(agent.seniority in {"lead", "senior"} for agent in blueprint.agents)
    assert blueprint.cost_policy["delegation_allowed"] is False
    assert profile_config(LEAD_QUORUM).uses_quorum is True


def test_full_team_blueprint_models_programming_team_and_cost_delegation() -> None:
    blueprint = build_default_team_blueprint(
        "CHAT-FULL",
        "full_team",
        objective="Implement a feature with tests",
    )

    roles = [agent.role for agent in blueprint.agents]
    assert roles == ["team_lead", "engineer", "reviewer"]
    assert blueprint.cost_policy["delegation_allowed"] is True
    assert blueprint.cost_policy["cheap_delegate_roles"] == ["engineer"]
    assert blueprint.cost_policy["senior_control_roles"] == ["team_lead", "reviewer"]
    assert profile_config(FULL_TEAM).requires_review_gate is True
    assert profile_config(FULL_TEAM).requires_qa_gate is False
    assert "programming team" in blueprint.rationale.lower()
