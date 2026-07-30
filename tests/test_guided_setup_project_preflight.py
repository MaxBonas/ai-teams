from __future__ import annotations

from copy import deepcopy

import pytest

from aiteam.guided_setup_needs import build_needs_submission
from aiteam.guided_setup_project_preflight import (
    build_project_preflight,
    validate_project_preflight,
)


def _needs(kind: str = "research") -> dict:
    return build_needs_submission(
        "project_setup",
        {
            "goal": (
                "Analizar necesidades de una empresa de limpieza"
                if kind == "research"
                else "Construir un portal React accesible"
            ),
            "objective_kind": kind,
            "languages": ["React", "TypeScript"],
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
    kind: str = "research",
    *,
    detected: bool = False,
    mode: str = "create",
) -> dict:
    needs = _needs(kind)
    return {
        "schema_version": "guided_setup_project_proposal_v1",
        "proposal_hash": "a" * 64,
        "project": {
            "mode": mode,
            "objective": needs["answers"]["goal"],
            "objective_kind": kind,
        },
        "ecosystems": {
            "detected_ids": ["javascript_typescript"] if detected else [],
            "scan_truncated": False,
        },
        "team": {
            "assignments": [{
                "candidate": {
                    "profile_id": "codex_subscription",
                    "model_id": "gpt-fixture",
                }
            }]
        },
        "save_gate": {"allowed": True},
    }


def _preparation(*, adapter_ready: bool = True) -> dict:
    stage = "passed" if adapter_ready else "not_checked"
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
        "adapters": [{
            "id": "codex_subscription",
            "state": "ready" if adapter_ready else "unverified",
            "stages": {"contract": stage},
        }],
    }


def _inventory(*, toolchain_ready: bool = True) -> dict:
    return {
        "schema_version": "machine_doctor_v1",
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
        },
        "toolchains": [{
            "id": "javascript_typescript",
            "binary_installed": toolchain_ready,
        }],
    }


def _path(**overrides: bool | str) -> dict:
    value: dict[str, bool | str] = {
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
    value.update(overrides)
    return value


def _software_fixture(**overrides: object) -> dict:
    value = {
        "schema_version": "guided_setup_fixture_evidence_v1",
        "kind": "software_toolchain_smoke",
        "status": "passed",
        "receipt_ref": "benchmarks/results/guided_setup/software-smoke.json",
        "commands_executed": True,
        "tests_executed": True,
        "remote_calls": False,
        "quota_consumed": False,
        "workspace_mutated": False,
    }
    value.update(overrides)
    return value


def test_research_goes_without_commands_or_test_loop() -> None:
    result = build_project_preflight(
        _needs(),
        _proposal(),
        _preparation(),
        _inventory(),
        _path(),
    )

    assert result["summary"]["status"] == "go"
    assert result["summary"]["commit_allowed"] is True
    assert result["summary"]["enter_project_allowed"] is False
    assert result["fixture_policy"]["kind"] == "research_evidence_contract"
    fixture = next(
        row for row in result["gates"] if row["id"] == "proportional_fixture"
    )
    assert fixture["evidence"]["commands_executed"] is False
    assert fixture["evidence"]["tests_executed"] is False
    assert result["scope"]["tests_executed"] is False


def test_software_blocks_until_exact_smoke_receipt_passes() -> None:
    blocked = build_project_preflight(
        _needs("software"),
        _proposal("software"),
        _preparation(),
        _inventory(),
        _path(),
    )
    ready = build_project_preflight(
        _needs("software"),
        _proposal("software"),
        _preparation(),
        _inventory(),
        _path(),
        fixture_evidence=[_software_fixture()],
    )

    assert blocked["summary"]["status"] == "no_go"
    assert blocked["summary"]["next_action"] == "run_proportional_fixture"
    assert ready["summary"]["status"] == "go"
    assert ready["fixture_policy"]["max_attempts"] == 1


def test_detected_software_toolchain_is_a_hard_gate() -> None:
    result = build_project_preflight(
        _needs("software"),
        _proposal("software", detected=True),
        _preparation(),
        _inventory(toolchain_ready=False),
        _path(),
        fixture_evidence=[_software_fixture()],
    )

    assert result["summary"]["status"] == "no_go"
    assert result["summary"]["blockers"][0]["gate"] == "project_toolchains"
    assert result["summary"]["blockers"][0]["code"] == "toolchains_missing"


def test_discovery_or_installation_without_contract_never_readies_adapter() -> None:
    result = build_project_preflight(
        _needs(),
        _proposal(),
        _preparation(adapter_ready=False),
        _inventory(),
        _path(),
    )

    adapter = next(
        row for row in result["gates"] if row["id"] == "selected_adapters"
    )
    assert adapter["status"] == "blocked"
    assert adapter["evidence"]["discovery_grants_ready"] is False
    assert result["summary"]["status"] == "no_go"


def test_unsafe_path_or_create_collision_fails_closed() -> None:
    unsafe = build_project_preflight(
        _needs(),
        _proposal(),
        _preparation(),
        _inventory(),
        _path(confined_to_projects_root=False),
    )
    collision = build_project_preflight(
        _needs(),
        _proposal(),
        _preparation(),
        _inventory(),
        _path(target_exists=True),
    )

    assert unsafe["summary"]["blockers"][0]["code"] == (
        "project_path_outside_projects_root"
    )
    assert collision["summary"]["blockers"][0]["code"] == (
        "project_create_path_blocked"
    )


def test_non_programming_objective_rejects_software_execution_evidence() -> None:
    with pytest.raises(ValueError, match="non_programming_fixture_unsafe"):
        build_project_preflight(
            _needs(),
            _proposal(),
            _preparation(),
            _inventory(),
            _path(),
            fixture_evidence=[_software_fixture()],
        )


@pytest.mark.parametrize(
    ("kind", "fixture_kind"),
    [
        ("operations", "operations_receipt_contract"),
        ("mixed", "mixed_scope_contract"),
    ],
)
def test_non_programming_contracts_do_not_invent_software_tests(
    kind: str,
    fixture_kind: str,
) -> None:
    result = build_project_preflight(
        _needs(kind),
        _proposal(kind),
        _preparation(),
        _inventory(),
        _path(),
    )

    assert result["summary"]["status"] == "go"
    assert result["fixture_policy"]["kind"] == fixture_kind
    assert result["scope"]["tests_executed"] is False


def test_mixed_with_detected_software_requires_the_software_smoke() -> None:
    blocked = build_project_preflight(
        _needs("mixed"),
        _proposal("mixed", detected=True),
        _preparation(),
        _inventory(),
        _path(),
    )
    ready = build_project_preflight(
        _needs("mixed"),
        _proposal("mixed", detected=True),
        _preparation(),
        _inventory(),
        _path(),
        fixture_evidence=[_software_fixture()],
    )

    assert blocked["summary"]["status"] == "no_go"
    assert blocked["fixture_policy"]["software_fixture_required"] is True
    assert ready["summary"]["status"] == "go"


def test_import_requires_existing_readable_and_writable_directory() -> None:
    result = build_project_preflight(
        _needs(),
        _proposal(mode="import"),
        _preparation(),
        _inventory(),
        _path(
            mode="import",
            target_exists=True,
            target_is_dir=True,
            target_readable=True,
            target_writable=True,
        ),
    )

    assert result["summary"]["status"] == "go"
    path_gate = next(
        row for row in result["gates"] if row["id"] == "project_path"
    )
    assert path_gate["code"] == "project_import_path_ready"


def test_local_fixture_cannot_hide_remote_calls_or_quota() -> None:
    with pytest.raises(ValueError, match="fixture_remote_side_effect"):
        build_project_preflight(
            _needs("software"),
            _proposal("software"),
            _preparation(),
            _inventory(),
            _path(),
            fixture_evidence=[
                _software_fixture(remote_calls=True, quota_consumed=True)
            ],
        )


def test_fixture_receipt_must_be_relative_and_safe() -> None:
    with pytest.raises(ValueError, match="fixture_receipt_unsafe"):
        build_project_preflight(
            _needs("software"),
            _proposal("software"),
            _preparation(),
            _inventory(),
            _path(),
            fixture_evidence=[
                _software_fixture(receipt_ref="../outside.json")
            ],
        )


def test_preflight_is_deterministic_and_does_not_mutate_inputs() -> None:
    values = (
        _needs("software"),
        _proposal("software"),
        _preparation(),
        _inventory(),
        _path(),
        [_software_fixture()],
    )
    before = deepcopy(values)
    first = build_project_preflight(*values[:5], fixture_evidence=values[5])
    second = build_project_preflight(*values[:5], fixture_evidence=values[5])

    assert values == before
    assert first == second
    assert len(first["preflight_hash"]) == 64


def test_preflight_validator_rejects_gate_and_hash_tampering() -> None:
    result = build_project_preflight(
        _needs(),
        _proposal(),
        _preparation(),
        _inventory(),
        _path(),
    )
    tampered = deepcopy(result)
    tampered["gates"][0]["status"] = "blocked"

    with pytest.raises(ValueError, match="summary_drift|hash_drift"):
        validate_project_preflight(tampered)
