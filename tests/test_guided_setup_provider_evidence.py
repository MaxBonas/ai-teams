from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from aiteam.guided_setup_needs import build_needs_submission
from aiteam.guided_setup_preparation import build_preparation_plan
from aiteam.guided_setup_provider_evidence import (
    build_canonical_provider_evidence,
)

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _needs() -> dict[str, Any]:
    return build_needs_submission(
        "machine_onboarding",
        {
            "goal": "Crear una aplicación",
            "objective_kind": "software",
            "languages": ["Python"],
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


def _inventory(version: str = "codex-cli 0.146.0-alpha.6") -> dict[str, Any]:
    return {
        "schema_version": "machine_doctor_v1",
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
        },
        "runtimes": [],
        "adapters": [
            {
                "id": "codex_subscription",
                "cli": {"installed": True, "version": version},
                "authentication_status": "authenticated",
                "health_status": "ok",
            }
        ],
    }


def _profile(
    *,
    checked_at: datetime = NOW,
    probe_version: str = "0.146.0-alpha.6",
    receipts: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": "codex_subscription",
        "health": {
            "status": "ok",
            "checked_at": checked_at.isoformat(),
            "verified_models": ["gpt-5.6-sol"],
        },
        "model_catalog": {
            "status": "current",
            "source": "codex models cache",
            "count": 3,
        },
        "model_options": [
            {
                "value": "gpt-5.6-sol",
                "availability": "verified",
                "structured_output": "json_schema",
                "probe_status": "completed",
                "probe_version": probe_version,
                "probe_evaluated_at": NOW.isoformat(),
                "probe_receipts": (
                    ["receipts/codex-sol.json"] if receipts is None else receipts
                ),
            }
        ],
    }


def _initial_plan(inventory: dict[str, Any]) -> dict[str, Any]:
    return build_preparation_plan(_needs(), inventory)


def test_exact_current_evidence_passes_separate_stages() -> None:
    inventory = _inventory()
    projection = build_canonical_provider_evidence(
        _initial_plan(inventory),
        inventory,
        [_profile()],
        observed_at=NOW,
    )

    stages = projection["stage_evidence"]["codex_subscription"]
    assert stages == {
        "authentication": "passed",
        "catalog": "passed",
        "health": "passed",
        "contract": "passed",
    }
    assert projection["scope"]["discovery_is_quality"] is False


def test_verified_model_without_structured_receipt_does_not_pass_contract() -> None:
    inventory = _inventory()
    projection = build_canonical_provider_evidence(
        _initial_plan(inventory),
        inventory,
        [_profile(receipts=[])],
        observed_at=NOW,
    )

    assert projection["stage_evidence"]["codex_subscription"]["catalog"] == "passed"
    assert projection["stage_evidence"]["codex_subscription"]["contract"] == (
        "not_checked"
    )


def test_probe_receipt_must_match_exact_cli_version() -> None:
    inventory = _inventory("codex-cli 0.147.0")
    projection = build_canonical_provider_evidence(
        _initial_plan(inventory),
        inventory,
        [_profile()],
        observed_at=NOW,
    )

    assert projection["stage_evidence"]["codex_subscription"]["contract"] == (
        "not_checked"
    )


def test_stale_health_does_not_grant_auth_or_health() -> None:
    inventory = _inventory()
    projection = build_canonical_provider_evidence(
        _initial_plan(inventory),
        inventory,
        [_profile(checked_at=NOW - timedelta(days=2))],
        observed_at=NOW,
    )
    stages = projection["stage_evidence"]["codex_subscription"]

    assert stages["authentication"] == "not_checked"
    assert stages["health"] == "not_checked"
    assert stages["catalog"] == "passed"


def test_canonical_evidence_controls_final_preparation_readiness() -> None:
    inventory = _inventory()
    initial = _initial_plan(inventory)
    projection = build_canonical_provider_evidence(
        initial,
        inventory,
        [_profile()],
        observed_at=NOW,
    )
    final = build_preparation_plan(
        _needs(),
        inventory,
        provider_evidence=projection["stage_evidence"],
    )

    assert final["adapters"][0]["state"] == "ready"
    assert final["lead_channel"]["state"] == "ready"


def test_stale_api_catalog_and_unsafe_receipt_fail_closed() -> None:
    inventory = _inventory()
    profile = _profile(receipts=["C:/Users/private/probe.json"])
    profile["model_catalog"]["checked_at"] = (
        NOW - timedelta(days=2)
    ).isoformat()
    projection = build_canonical_provider_evidence(
        _initial_plan(inventory),
        inventory,
        [profile],
        observed_at=NOW,
    )
    detail = projection["details"][0]

    assert detail["stages"]["catalog"] == "not_checked"
    assert detail["stages"]["contract"] == "not_checked"
    assert detail["receipt_refs"] == []
