from __future__ import annotations

from typing import Any

import pytest

from aiteam.guided_setup_needs import build_needs_submission
from aiteam.guided_setup_preparation import build_preparation_plan


def _needs(**overrides: object) -> dict[str, Any]:
    answers: dict[str, object] = {
        "goal": "Crear una aplicación React",
        "objective_kind": "software",
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
    }
    answers.update(overrides)
    return build_needs_submission("machine_onboarding", answers)


def _inventory(adapters: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "machine_doctor_v1",
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
        },
        "runtimes": [
            {
                "id": "python",
                "requirement": "required",
                "ready": True,
                "installed": True,
                "version": "3.12.10",
                "minimum_version": "3.10",
            }
        ],
        "adapters": adapters or [],
    }


def _codex_ready_observation() -> dict[str, Any]:
    return {
        "id": "codex_subscription",
        "cli": {
            "installed": True,
            "version": "codex-cli 0.146.0-alpha.6",
        },
        "authentication_status": "authenticated",
        "health_status": "ok",
    }


def test_installation_alone_never_makes_adapter_ready() -> None:
    plan = build_preparation_plan(
        _needs(),
        _inventory([_codex_ready_observation()]),
    )
    codex = plan["adapters"][0]

    assert codex["stages"]["installation"] == "passed"
    assert codex["stages"]["version"] == "passed"
    assert codex["stages"]["catalog"] == "not_checked"
    assert codex["stages"]["contract"] == "not_checked"
    assert codex["state"] == "unverified"
    assert plan["lead_channel"]["state"] == "blocked"


def test_exact_provider_evidence_can_make_selected_lead_channel_ready() -> None:
    plan = build_preparation_plan(
        _needs(),
        _inventory([_codex_ready_observation()]),
        provider_evidence={
            "codex_subscription": {
                "catalog": "passed",
                "contract": "passed",
            }
        },
    )

    assert plan["adapters"][0]["state"] == "ready"
    assert plan["lead_channel"]["state"] == "ready"
    assert plan["summary"]["ready"] is True


def test_local_runtimes_are_absent_without_opt_in_and_optional_with_it() -> None:
    normal = build_preparation_plan(_needs(), _inventory())
    local = build_preparation_plan(
        _needs(local_models="willing"),
        _inventory(),
    )

    assert not normal["summary"]["optional_local_present"]
    assert local["summary"]["optional_local_present"]
    assert {
        row["requirement"]
        for row in local["adapters"]
        if row["id"] in {"ollama", "lmstudio"}
    } == {"optional"}


def test_free_budget_recommends_opencode_but_never_requires_it() -> None:
    plan = build_preparation_plan(
        _needs(budget_priority="prefer_free"),
        _inventory(),
    )
    opencode = next(
        row for row in plan["adapters"] if row["id"] == "opencode_zen_free"
    )

    assert opencode["requirement"] == "recommended"
    assert opencode["automatic_install"] is False


def test_personal_api_stays_unverified_until_provider_is_selected() -> None:
    plan = build_preparation_plan(
        _needs(api_access="willing"),
        _inventory(),
    )
    api = next(row for row in plan["adapters"] if row["id"] == "personal_api")

    assert api["state"] == "unverified"
    assert api["stages"]["authentication"] == "not_checked"
    assert api["automatic_install"] is False


def test_unsafe_or_wrong_inventory_fails_closed() -> None:
    unsafe = _inventory()
    unsafe["scope"]["secrets_read"] = True
    with pytest.raises(ValueError, match="inventory_scope_unsafe"):
        build_preparation_plan(_needs(), unsafe)
    with pytest.raises(ValueError, match="inventory_schema_mismatch"):
        build_preparation_plan(_needs(), {"schema_version": "legacy"})


def test_explicit_valid_api_profile_can_become_lead_ready() -> None:
    profile = {
        "id": "openai_api",
        "channel": "api",
        "model_options": [
            {"value": "gpt-5.6-sol", "best_for": ["lead"]}
        ],
    }
    evidence = {
        "openai_api": {
            "authentication": "passed",
            "catalog": "passed",
            "health": "passed",
            "contract": "passed",
        }
    }
    plan = build_preparation_plan(
        _needs(api_access="existing", subscriptions=["none"]),
        _inventory(),
        selected_api_profiles=[profile],
        provider_evidence=evidence,
    )

    assert plan["adapters"][0]["id"] == "openai_api"
    assert plan["adapters"][0]["state"] == "ready"
    assert plan["lead_channel"]["state"] == "ready"
