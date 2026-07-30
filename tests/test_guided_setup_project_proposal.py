from __future__ import annotations

from copy import deepcopy

import pytest

from aiteam.guided_setup_coverage import build_guided_setup_coverage
from aiteam.guided_setup_needs import build_needs_submission
from aiteam.guided_setup_project_proposal import (
    build_project_team_proposal,
    normalize_project_identity_intent,
)


def _needs(*, team: str = "full_team") -> dict:
    return build_needs_submission(
        "project_setup",
        {
            "goal": "Construir un portal React accesible",
            "objective_kind": "software",
            "languages": ["React", "TypeScript"],
            "data_sensitivity": "internal",
            "budget_priority": "balanced",
            "subscriptions": ["codex", "antigravity"],
            "api_access": "not_willing",
            "local_models": "not_wanted",
            "autonomy": "supervised",
            "criticality": "medium",
            "team_preference": team,
            "external_tools": "optional",
        },
    )


def _raw_candidate(
    role: str,
    name: str,
    *,
    profile: str,
    perspective: str,
    pool: str,
    automatic: bool = True,
    owner_selectable: bool = True,
) -> dict:
    return {
        "candidate_id": name,
        "identity": {
            "profile_id": profile,
            "model_id": f"model-{name}",
            "provider_org": perspective,
            "channel": "subscription",
            "perspective_key": perspective,
            "capacity_pool": pool,
        },
        "model_metadata": {
            "tier": "premium",
            "caps": ["reasoning", "structured_output"],
        },
        "owner_selectable": owner_selectable,
        "rank": 1,
        "selection_reason": f"fixture:{role}",
        "contextual_compatibility": {
            "allowed": True,
            "code": "compatible",
        },
        "selection_score": {
            "score": 90,
            "auto_eligible": automatic,
            "auto_ineligible_reasons": (
                [] if automatic else ["calibration_missing"]
            ),
            "hard_gates": {
                "adapter_green": {"passed": True},
                "calibrated": {"passed": automatic},
            },
        },
    }


def _selection(role: str, *candidates: dict) -> dict:
    return {
        "selection_version": "model_selection_v1",
        "canonical_role": role,
        "candidates": list(candidates),
    }


def _coverage(*, include_reviewer: bool = True, manual_lead: bool = False) -> dict:
    selections = {
        "team_lead": _selection(
            "team_lead",
            _raw_candidate(
                "team_lead",
                "lead-openai",
                profile="codex",
                perspective="openai",
                pool="codex",
            ),
            *(
                [
                    _raw_candidate(
                        "team_lead",
                        "lead-manual",
                        profile="manual",
                        perspective="owner",
                        pool="manual",
                        automatic=False,
                    )
                ]
                if manual_lead
                else []
            ),
        ),
        "quorum_auditor": _selection(
            "quorum_auditor",
            _raw_candidate(
                "quorum_auditor",
                "audit-openai",
                profile="codex",
                perspective="openai",
                pool="codex",
            ),
            _raw_candidate(
                "quorum_auditor",
                "audit-google",
                profile="antigravity",
                perspective="google",
                pool="antigravity",
            ),
        ),
        "engineer": _selection(
            "engineer",
            _raw_candidate(
                "engineer",
                "engineer-openai",
                profile="codex",
                perspective="openai",
                pool="codex",
            ),
        ),
        "worker": _selection("worker"),
    }
    if include_reviewer:
        selections["reviewer"] = _selection(
            "reviewer",
            _raw_candidate(
                "reviewer",
                "reviewer-google",
                profile="antigravity",
                perspective="google",
                pool="antigravity",
            ),
        )
    return build_guided_setup_coverage(
        selections,
        ready_profile_ids={"codex", "antigravity", "manual"},
        recommended_profile="full_team",
    )


def _identity(*, mode: str = "create") -> dict:
    return {
        "mode": mode,
        "name": "Portal",
        "target": "C:/projects/Portal",
        "target_exists": mode == "import",
        "target_is_dir": mode == "import",
    }


def _ecosystems(*, truncated: bool = False) -> dict:
    return {
        "schema_version": "ecosystem_registry_v1",
        "workspace_observed": True,
        "scan_truncated": truncated,
        "files_observed": 5,
        "ecosystems": [
            {
                "id": "javascript_typescript",
                "label": "JavaScript / TypeScript",
                "status": "verified",
                "categories": ["web"],
                "manifests": ["package.json"],
                "extension_count": 3,
                "extension_samples": ["src/App.tsx"],
                "available_actions": ["install", "test", "build"],
                "support_claim": False,
                "source": "ecosystem_registry_v1",
            }
        ],
        "detected_ids": ["javascript_typescript"],
        "support_claims": [],
        "commands_executed": False,
        "installation_performed": False,
        "mutated": False,
    }


def test_full_team_proposal_is_lead_first_sealed_and_read_only() -> None:
    proposal = build_project_team_proposal(
        _needs(),
        _identity(),
        _ecosystems(),
        _coverage(),
        instructions="Usar componentes accesibles.",
    )

    assert proposal["save_gate"]["allowed"] is True
    assert proposal["team"]["creation_order"] == [
        "role:team_lead",
        "role:engineer",
        "role:reviewer",
    ]
    assert [row["candidate"]["candidate_id"] for row in proposal["team"]["assignments"]] == [
        "lead-openai",
        "engineer-openai",
        "reviewer-google",
    ]
    assert proposal["project"]["instructions_target"] == (
        ".aiteam/instructions.md"
    )
    assert proposal["scope"] == {
        "read_only": True,
        "filesystem_mutated": False,
        "database_mutated": False,
        "project_created": False,
        "agents_created": False,
        "wakeups_created": False,
    }
    assert len(proposal["proposal_hash"]) == 64


def test_quorum_proposal_selects_distinct_perspective_and_pool() -> None:
    proposal = build_project_team_proposal(
        _needs(team="lead_quorum"),
        _identity(),
        _ecosystems(),
        _coverage(),
        requested_profile="lead_quorum",
    )

    diversity = proposal["team"]["quorum_diversity"]
    assert diversity == {
        "required_count": 2,
        "assigned_count": 2,
        "perspective_count": 2,
        "capacity_pool_count": 2,
        "ready": True,
    }
    assert proposal["save_gate"]["allowed"] is True


def test_manual_owner_override_is_allowed_without_becoming_coverage() -> None:
    proposal = build_project_team_proposal(
        _needs(),
        _identity(),
        _ecosystems(),
        _coverage(manual_lead=True),
        overrides_by_agent_id={"role:team_lead": "lead-manual"},
    )

    lead = proposal["team"]["assignments"][0]
    assert lead["selection_mode"] == "owner_explicit"
    assert lead["candidate"]["coverage_eligible"] is False
    assert proposal["team"]["manual_override_count"] == 1
    assert "owner_override_does_not_grant_automatic_coverage" in (
        proposal["degradations"]
    )
    assert proposal["save_gate"]["allowed"] is True
    assert proposal["save_gate"]["requires_owner_confirmation"] is True


def test_unprepared_or_unknown_override_fails_closed() -> None:
    coverage = _coverage(manual_lead=True)
    manual = coverage["roles"]["team_lead"]["excluded_candidates"][0]
    manual["exclusion_reasons"].append("adapter_not_prepared_in_setup")

    with pytest.raises(ValueError, match="override_not_selectable"):
        build_project_team_proposal(
            _needs(),
            _identity(),
            _ecosystems(),
            coverage,
            overrides_by_agent_id={"role:team_lead": "lead-manual"},
        )

    with pytest.raises(ValueError, match="override_candidate_missing"):
        build_project_team_proposal(
            _needs(),
            _identity(),
            _ecosystems(),
            _coverage(),
            overrides_by_agent_id={"role:team_lead": "forged"},
        )


def test_quorum_override_cannot_reuse_the_same_candidate() -> None:
    with pytest.raises(ValueError, match="override_candidate_reused"):
        build_project_team_proposal(
            _needs(team="lead_quorum"),
            _identity(),
            _ecosystems(),
            _coverage(),
            requested_profile="lead_quorum",
            overrides_by_agent_id={
                "role:quorum_auditor_1": "audit-openai",
                "role:quorum_auditor_2": "audit-openai",
            },
        )


def test_missing_role_blocks_save_and_keeps_automatic_coverage_reason() -> None:
    proposal = build_project_team_proposal(
        _needs(),
        _identity(),
        _ecosystems(),
        _coverage(include_reviewer=False),
    )

    assert proposal["save_gate"]["allowed"] is False
    assert proposal["save_gate"]["blockers"] == [
        "assignment:role:reviewer"
    ]
    assert proposal["profile"]["automatic_coverage_ready"] is False


def test_profile_change_and_truncated_detection_require_confirmation() -> None:
    proposal = build_project_team_proposal(
        _needs(team="solo_lead"),
        _identity(mode="import"),
        _ecosystems(truncated=True),
        _coverage(),
        requested_profile="full_team",
    )

    assert proposal["profile"]["owner_override"] is True
    assert proposal["ecosystems"]["scan_truncated"] is True
    assert proposal["degradations"] == [
        "ecosystem_scan_truncated",
        "owner_changed_recommended_profile",
    ]
    assert proposal["save_gate"]["requires_owner_confirmation"] is True


def test_project_identity_rejects_collision_and_invalid_import() -> None:
    collision = _identity()
    collision["target_exists"] = True
    with pytest.raises(ValueError, match="target_collision"):
        build_project_team_proposal(
            _needs(),
            collision,
            _ecosystems(),
            _coverage(),
        )

    missing_import = _identity(mode="import")
    missing_import["target_exists"] = False
    with pytest.raises(ValueError, match="import_target_invalid"):
        build_project_team_proposal(
            _needs(),
            missing_import,
            _ecosystems(),
            _coverage(),
        )


def test_project_identity_intent_is_strict_and_import_requires_path() -> None:
    assert normalize_project_identity_intent(
        {"mode": "create", "name": "Portal", "path": ""}
    ) == {"mode": "create", "name": "Portal", "path": ""}

    with pytest.raises(ValueError, match="path_invalid"):
        normalize_project_identity_intent(
            {"mode": "import", "name": "Portal", "path": ""}
        )
    with pytest.raises(ValueError, match="fields_invalid"):
        normalize_project_identity_intent(
            {
                "mode": "create",
                "name": "Portal",
                "path": "",
                "target_exists": False,
            }
        )


def test_proposal_does_not_mutate_any_input() -> None:
    needs = _needs()
    identity = _identity()
    ecosystems = _ecosystems()
    coverage = _coverage()
    before = deepcopy((needs, identity, ecosystems, coverage))

    build_project_team_proposal(
        needs,
        identity,
        ecosystems,
        coverage,
    )

    assert (needs, identity, ecosystems, coverage) == before
