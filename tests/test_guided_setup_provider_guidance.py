from __future__ import annotations

import json
from typing import Any

from aiteam.guided_setup_needs import build_needs_submission
from aiteam.guided_setup_preparation import build_preparation_plan
from aiteam.guided_setup_provider_guidance import build_provider_guidance


def _needs(**overrides: object) -> dict[str, Any]:
    answers: dict[str, object] = {
        "goal": "Crear una aplicación React",
        "objective_kind": "software",
        "languages": ["React"],
        "data_sensitivity": "internal",
        "budget_priority": "prefer_free",
        "subscriptions": ["codex", "antigravity"],
        "api_access": "willing",
        "local_models": "not_wanted",
        "autonomy": "supervised",
        "criticality": "medium",
        "team_preference": "solo_lead",
        "external_tools": "optional",
    }
    answers.update(overrides)
    return build_needs_submission("machine_onboarding", answers)


def _inventory() -> dict[str, Any]:
    return {
        "schema_version": "machine_doctor_v1",
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
        },
        "runtimes": [],
        "adapters": [],
    }


def test_guidance_covers_only_requested_provider_channels() -> None:
    plan = build_preparation_plan(_needs(), _inventory())
    guidance = build_provider_guidance(plan)

    assert {row["adapter_id"] for row in guidance["providers"]} == {
        "codex_subscription",
        "antigravity_subscription",
        "opencode_zen_free",
        "personal_api",
    }
    assert guidance["policy"]["execution"] == "manual_only"
    assert guidance["policy"]["action_completion_grants_ready"] is False


def test_every_action_requires_human_confirmation_and_never_grants_ready() -> None:
    guidance = build_provider_guidance(
        build_preparation_plan(_needs(), _inventory())
    )
    actions = [
        action
        for provider in guidance["providers"]
        for action in provider["actions"]
    ]

    assert actions
    assert all(action["execution"] == "manual_only" for action in actions)
    assert all(action["confirmation_required"] is True for action in actions)
    assert all(action["automatic"] is False for action in actions)
    assert all(action["completion_grants_ready"] is False for action in actions)


def test_cli_guides_expose_versions_login_and_risks_without_running_them() -> None:
    guidance = build_provider_guidance(
        build_preparation_plan(_needs(), _inventory())
    )
    codex = next(
        row
        for row in guidance["providers"]
        if row["adapter_id"] == "codex_subscription"
    )
    opencode = next(
        row
        for row in guidance["providers"]
        if row["adapter_id"] == "opencode_zen_free"
    )

    assert codex["minimum_version"] == "0.146.0-alpha.6"
    assert any(action["id"].endswith(":authenticate") for action in codex["actions"])
    assert any(
        action["risk"] == "remote_script_execution"
        for action in codex["actions"]
    )
    assert any("non_confidential_only" in note for note in opencode["notes"])
    assert any(
        "API key personal" in note for note in opencode["notes"]
    )


def test_personal_api_uses_secret_reference_and_never_echoes_a_value() -> None:
    guidance = build_provider_guidance(
        build_preparation_plan(_needs(), _inventory())
    )
    personal_api = next(
        row
        for row in guidance["providers"]
        if row["adapter_id"] == "personal_api"
    )
    serialized = json.dumps(personal_api)

    assert "/api/user-adapters/secrets" in serialized
    assert "secret_ref_only" in serialized
    assert '"api_key"' not in serialized
    assert all(action["copyable_command"] is None for action in personal_api["actions"])


def test_local_guidance_never_appears_without_opt_in() -> None:
    normal = build_provider_guidance(
        build_preparation_plan(_needs(), _inventory())
    )
    local = build_provider_guidance(
        build_preparation_plan(
            _needs(local_models="willing"),
            _inventory(),
        )
    )

    assert not {
        "ollama",
        "lmstudio",
    } & {row["adapter_id"] for row in normal["providers"]}
    local_rows = [
        row
        for row in local["providers"]
        if row["adapter_id"] in {"ollama", "lmstudio"}
    ]
    assert len(local_rows) == 2
    assert all(row["requirement"] == "optional" for row in local_rows)
    assert all(
        action["automatic"] is False
        for row in local_rows
        for action in row["actions"]
    )
