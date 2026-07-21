from __future__ import annotations

from aiteam.run_profiles import (
    FULL_TEAM,
    LEAD_QUORUM,
    SOLO_LEAD,
    build_default_team_blueprint,
    normalize_run_profile,
    profile_config,
    select_execution_profile,
)
from aiteam.lead_intake import build_team_proposal
from aiteam.policies import (
    QUORUM_MAX_CONTRIBUTIONS,
    QUORUM_MAX_SYNTHESIS_ATTEMPTS,
    QUORUM_MIN_VALID_CONTRIBUTIONS,
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
    assert [agent.role for agent in blueprint.agents[1:]] == ["quorum_auditor", "quorum_auditor"]
    assert blueprint.cost_policy["delegation_allowed"] is False
    assert profile_config(LEAD_QUORUM).uses_quorum is True
    assert profile_config(LEAD_QUORUM).phase == "planning"
    assert profile_config(LEAD_QUORUM).completion_artifact == "accepted_plan"
    assert profile_config(LEAD_QUORUM).next_profile is None
    assert QUORUM_MIN_VALID_CONTRIBUTIONS == 2
    assert QUORUM_MAX_CONTRIBUTIONS >= QUORUM_MIN_VALID_CONTRIBUTIONS
    assert QUORUM_MAX_SYNTHESIS_ATTEMPTS == 2


def test_lead_quorum_assigns_distinct_providers_when_available() -> None:
    proposal = build_team_proposal(
        {"id": "issue:q", "title": "Plan", "metadata": {"profile": "lead_quorum"}},
        adapter_profiles=[
            {"id": "codex_subscription", "provider": "openai", "channel": "subscription", "adapter_type": "subscription_cli"},
            {"id": "antigravity_subscription", "provider": "google-antigravity", "channel": "subscription", "adapter_type": "subscription_cli"},
        ],
    )
    assert [member["adapter_profile_id"] for member in proposal["proposed_team"]] == [
        "codex_subscription", "antigravity_subscription",
    ]
    assert proposal["proposed_team"][1]["model"] == "gemini-3.1-pro-high"
    assert len(proposal["suggested_issues"]) == 2


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
    assert profile_config(FULL_TEAM).phase == "execution"
    assert profile_config(FULL_TEAM).next_profile is None
    assert "programming team" in blueprint.rationale.lower()


def test_execution_selector_uses_solo_for_bounded_reversible_work() -> None:
    selected = select_execution_profile(
        criticality="medium",
        ambiguity="low",
        independent_verification=False,
        parallel_workstreams=1,
        reversible=True,
    )
    assert selected.profile == SOLO_LEAD
    assert selected.reason == "bounded_reversible_single_agent_work"


def test_execution_selector_uses_team_for_each_material_team_signal() -> None:
    base = {
        "criticality": "medium",
        "ambiguity": "low",
        "independent_verification": False,
        "parallel_workstreams": 1,
        "reversible": True,
    }
    cases = (
        ({"criticality": "high"}, "high_or_critical_risk_requires_team_controls"),
        ({"ambiguity": "high"}, "high_ambiguity_requires_separate_planning_and_execution"),
        ({"independent_verification": True}, "independent_verification_requested"),
        ({"parallel_workstreams": 2}, "multiple_parallel_workstreams"),
        ({"reversible": False}, "irreversible_change_uses_team_default"),
    )
    for override, reason in cases:
        selected = select_execution_profile(**{**base, **override})
        assert selected.profile == FULL_TEAM
        assert selected.reason == reason


def test_execution_selector_is_conservative_when_signals_are_missing() -> None:
    selected = select_execution_profile(criticality="low")
    assert selected.profile == FULL_TEAM
    assert selected.reason == "incomplete_signals_use_safe_team_default"


def test_execution_selector_never_auto_selects_quorum_but_honours_override() -> None:
    automatic = select_execution_profile(
        criticality="critical",
        ambiguity="high",
        independent_verification=True,
        parallel_workstreams=3,
        reversible=False,
    )
    explicit = select_execution_profile(explicit_profile="lead_quorum")
    assert automatic.profile == FULL_TEAM
    assert explicit.profile == LEAD_QUORUM
    assert explicit.source == "explicit_override"
