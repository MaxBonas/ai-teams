from __future__ import annotations

from copy import deepcopy

import pytest

from aiteam.guided_setup_needs import build_needs_submission
from aiteam.guided_setup_project_preflight import build_project_preflight
from aiteam.guided_setup_project_preflight_execution import (
    build_project_preflight_execution_plan,
    validate_project_preflight_execution_plan,
)


def _needs(
    kind: str,
    *,
    languages: list[str] | None = None,
) -> dict:
    return build_needs_submission(
        "project_setup",
        {
            "goal": f"Fixture {kind}",
            "objective_kind": kind,
            "languages": languages or ["TypeScript", "React"],
            "data_sensitivity": "internal",
            "budget_priority": "balanced",
            "subscriptions": ["codex"],
            "api_access": "not_willing",
            "local_models": "not_wanted",
            "autonomy": "supervised",
            "criticality": "medium",
            "team_preference": "solo_lead",
            "external_tools": "optional",
        },
    )


def _proposal(
    needs: dict,
    *,
    detected: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "guided_setup_project_proposal_v1",
        "proposal_hash": "a" * 64,
        "project": {
            "mode": "create",
            "objective": needs["answers"]["goal"],
            "objective_kind": needs["answers"]["objective_kind"],
        },
        "ecosystems": {
            "detected_ids": detected or [],
            "scan_truncated": False,
        },
        "team": {
            "assignments": [
                {
                    "candidate": {
                        "profile_id": "codex_subscription",
                        "model_id": "gpt-exact",
                    }
                }
            ]
        },
        "save_gate": {"allowed": True},
    }


def _preparation(*, adapter_ready: bool = True) -> dict:
    return {
        "schema_version": "guided_setup_preparation_v1",
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
            "installations_attempted": False,
            "terms_accepted": False,
        },
        "runtimes": [{"id": "python", "state": "ready"}],
        "adapters": [
            {
                "id": "codex_subscription",
                "state": "ready" if adapter_ready else "unverified",
                "stages": {
                    "contract": "passed"
                    if adapter_ready
                    else "not_checked"
                },
            }
        ],
    }


def _inventory(
    detected: list[str],
    *,
    toolchain_ready: bool = True,
) -> dict:
    return {
        "schema_version": "machine_doctor_v1",
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
        },
        "toolchains": [
            {
                "id": ecosystem_id,
                "binary_installed": toolchain_ready,
            }
            for ecosystem_id in detected
        ],
    }


def _path() -> dict:
    return {
        "schema_version": "guided_setup_project_path_observation_v1",
        "mode": "create",
        "target_exists": False,
        "target_is_dir": False,
        "target_readable": False,
        "target_writable": False,
        "parent_exists": True,
        "parent_writable": True,
        "confined_to_projects_root": True,
    }


def _context(
    kind: str,
    *,
    languages: list[str] | None = None,
    detected: list[str] | None = None,
    adapter_ready: bool = True,
    toolchain_ready: bool = True,
) -> tuple[dict, dict, dict]:
    needs = _needs(kind, languages=languages)
    detected_ids = detected or []
    proposal = _proposal(needs, detected=detected_ids)
    preflight = build_project_preflight(
        needs,
        proposal,
        _preparation(adapter_ready=adapter_ready),
        _inventory(detected_ids, toolchain_ready=toolchain_ready),
        _path(),
    )
    return needs, proposal, preflight


def test_research_with_green_adapter_has_nothing_to_execute() -> None:
    plan = build_project_preflight_execution_plan(*_context("research"))

    assert plan["summary"]["status"] == "nothing_to_run"
    assert plan["actions"] == []
    assert plan["scope"]["tests_executed"] is False


def test_research_never_gets_software_fixture_but_can_plan_exact_probe() -> None:
    plan = build_project_preflight_execution_plan(
        *_context("research", adapter_ready=False)
    )

    assert [row["id"] for row in plan["actions"]] == [
        "exact_adapter_probe"
    ]
    assert plan["actions"][0]["profile_id"] == "codex_subscription"
    assert plan["actions"][0]["model_id"] == "gpt-exact"


def test_software_typescript_selects_one_allowlisted_local_fixture() -> None:
    plan = build_project_preflight_execution_plan(*_context("software"))

    assert plan["summary"]["status"] == "ready"
    assert [row["id"] for row in plan["actions"]] == ["local_fixture"]
    assert plan["actions"][0]["case_id"] == "web_vite_react_typescript"
    assert plan["actions"][0]["max_attempts"] == 1
    assert plan["actions"][0]["remote"] is False


def test_local_fixture_precedes_remote_probe_and_each_is_bounded_once() -> None:
    plan = build_project_preflight_execution_plan(
        *_context("software", adapter_ready=False)
    )

    assert [row["id"] for row in plan["actions"]] == [
        "local_fixture",
        "exact_adapter_probe",
    ]
    assert all(row["max_attempts"] == 1 for row in plan["actions"])
    assert plan["summary"]["remote_action_count"] == 1


def test_mixed_without_software_surface_does_not_invent_tests() -> None:
    plan = build_project_preflight_execution_plan(*_context("mixed"))

    assert plan["actions"] == []
    assert plan["summary"]["status"] == "nothing_to_run"


def test_missing_detected_toolchain_blocks_before_any_execution() -> None:
    plan = build_project_preflight_execution_plan(
        *_context(
            "software",
            detected=["javascript_typescript"],
            toolchain_ready=False,
        )
    )

    assert plan["summary"]["status"] == "blocked"
    assert plan["actions"] == []
    assert plan["planning_blockers"][0]["code"] == "toolchains_missing"


def test_unknown_software_stack_requires_confirmation() -> None:
    plan = build_project_preflight_execution_plan(
        *_context("software", languages=["unknown"])
    )

    assert plan["summary"]["status"] == "blocked"
    assert plan["planning_blockers"][0]["code"] == (
        "software_fixture_case_unknown"
    )


def test_unknown_local_fixture_blocks_remote_probe_to_preserve_order() -> None:
    plan = build_project_preflight_execution_plan(
        *_context(
            "software",
            languages=["unknown"],
            adapter_ready=False,
        )
    )

    assert plan["summary"]["status"] == "blocked"
    assert plan["actions"] == []


def test_execution_plan_rejects_input_and_hash_tampering() -> None:
    needs, proposal, preflight = _context("software")
    with pytest.raises(ValueError, match="proposal_hash_mismatch"):
        build_project_preflight_execution_plan(
            needs,
            {**proposal, "proposal_hash": "b" * 64},
            preflight,
        )

    plan = build_project_preflight_execution_plan(needs, proposal, preflight)
    tampered = deepcopy(plan)
    tampered["actions"][0]["max_attempts"] = 2
    with pytest.raises(ValueError, match="action_bounds_drift"):
        validate_project_preflight_execution_plan(tampered)

    unsafe = build_project_preflight_execution_plan(
        needs,
        proposal,
        preflight,
    )
    unsafe["actions"][0]["remote"] = True
    unsafe["plan_hash"] = "0" * 64
    with pytest.raises(ValueError, match="local_action_drift"):
        validate_project_preflight_execution_plan(unsafe)
