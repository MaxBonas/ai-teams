from __future__ import annotations

from copy import deepcopy

import pytest

from aiteam.provider_change_detection import (
    build_provider_snapshot,
    compare_provider_snapshots,
    run_read_only_probe,
)
from aiteam.provider_change_intelligence import (
    build_provider_change_inventory,
)

NOW = "2026-07-30T12:00:00+00:00"
LATER = "2026-07-30T13:00:00+00:00"


def _component(surface: str) -> dict:
    return next(
        row
        for row in build_provider_change_inventory()["components"]
        if row["surface"] == surface
    )


def _snapshot(
    surface: str,
    *,
    installed: str = "1.0.0",
    latest: str = "1.0.0",
    compatibility: dict | None = None,
    lifecycle: dict | None = None,
    dimensions: dict | None = None,
    observed_at: str = NOW,
) -> dict:
    return build_provider_snapshot(
        _component(surface),
        {
            "status": "observed",
            "installed_version": installed,
            "latest_known_version": latest,
            "compatibility": compatibility or {},
            "lifecycle": lifecycle or {},
            "dimensions": dimensions or {},
        },
        observed_at=observed_at,
    )


def test_probe_calls_reader_once_and_is_idempotent() -> None:
    calls = 0

    def reader() -> dict:
        nonlocal calls
        calls += 1
        return {
            "status": "observed",
            "installed_version": "1.0.0",
            "latest_known_version": "1.0.0",
        }

    component = _component("cli_package")
    first = run_read_only_probe(component, reader, observed_at=NOW)
    second = run_read_only_probe(component, reader, observed_at=NOW)

    assert calls == 2
    assert first == second
    assert first["scope"]["update_attempted"] is False
    assert first["scope"]["routing_authority_granted"] is False


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), "offline"),
        (PermissionError(), "auth_required"),
        (OSError(), "failed"),
    ],
)
def test_probe_failures_remain_unknown(error: Exception, expected: str) -> None:
    def reader() -> dict:
        raise error

    snapshot = run_read_only_probe(
        _component("cli_package"), reader, observed_at=NOW
    )

    assert snapshot["probe_status"] == expected
    assert snapshot["facts"]["installed_version"]["state"] == "unknown"
    assert snapshot["facts"]["latest_known_version"]["state"] == "unknown"


def test_rate_limit_is_unknown_not_no_change() -> None:
    previous = _snapshot("cli_package")
    current = build_provider_snapshot(
        _component("cli_package"),
        {"status": "rate_limited"},
        observed_at=LATER,
    )

    diff = compare_provider_snapshots(previous, current)

    assert diff["summary"]["status"] == "unknown"
    assert diff["summary"]["decision"] == "unknown"
    assert diff["changes"][0]["kind"] == "observation_unavailable"


def test_probe_rejects_secret_shaped_fields() -> None:
    with pytest.raises(ValueError, match="unsupported fields"):
        build_provider_snapshot(
            _component("sdk_api"),
            {"status": "observed", "api_key": "do-not-store"},
            observed_at=NOW,
        )


def test_probe_rejects_source_outside_fact_authority() -> None:
    with pytest.raises(
        ValueError, match="cannot establish latest_known_version"
    ):
        build_provider_snapshot(
            _component("cli_package"),
            {
                "status": "observed",
                "installed_version": "1.0.0",
                "latest_known_version": "1.1.0",
                "source_overrides": {
                    "latest_known_version": {
                        "kind": "local_resolution",
                        "official": False,
                    }
                },
            },
            observed_at=NOW,
        )


def test_new_release_without_compatibility_is_only_informative() -> None:
    previous = _snapshot("cli_package")
    current = _snapshot(
        "cli_package",
        latest="1.1.0",
        observed_at=LATER,
    )

    diff = compare_provider_snapshots(previous, current)

    assert diff["summary"]["decision"] == "newer_available"
    assert diff["summary"]["automatic_update_allowed"] is False
    assert diff["summary"]["routing_change_allowed"] is False


def test_compatible_release_can_be_recommended_but_not_applied() -> None:
    previous = _snapshot("cli_package")
    current = _snapshot(
        "cli_package",
        latest="1.1.0",
        compatibility={"latest_known": "compatible"},
        observed_at=LATER,
    )

    diff = compare_provider_snapshots(previous, current)

    assert diff["summary"]["decision"] == "update_recommended"
    assert diff["summary"]["automatic_update_allowed"] is False


def test_required_release_and_incompatible_install_are_distinct() -> None:
    previous = _snapshot("cli_package")
    required = _snapshot(
        "cli_package",
        latest="1.1.0",
        compatibility={"latest_known": "compatible"},
        lifecycle={"latest_known": "required"},
        observed_at=LATER,
    )
    incompatible = _snapshot(
        "cli_package",
        compatibility={"installed": "incompatible"},
        observed_at=LATER,
    )

    assert (
        compare_provider_snapshots(previous, required)["summary"]["decision"]
        == "update_required"
    )
    assert (
        compare_provider_snapshots(previous, incompatible)["summary"][
            "decision"
        ]
        == "blocked"
    )


def test_prerelease_is_explicit_and_impacts_calibration() -> None:
    previous = _snapshot("cli_package")
    current = _snapshot(
        "cli_package",
        installed="1.1.0-beta.1",
        latest="1.1.0-beta.1",
        compatibility={"installed": "compatible"},
        observed_at=LATER,
    )

    diff = compare_provider_snapshots(previous, current)

    kinds = {row["kind"] for row in diff["changes"]}
    assert "prerelease_installed" in kinds
    assert diff["summary"]["calibration_impacted"] is True


def test_downgrade_and_opaque_release_order_are_explicit() -> None:
    previous = _snapshot(
        "cli_package", installed="1.1.0", latest="1.1.0"
    )
    downgraded = _snapshot(
        "cli_package",
        installed="1.0.0",
        latest="1.1.0",
        observed_at=LATER,
    )
    opaque_previous = _snapshot(
        "sdk_api", installed="api-v1", latest="api-v1"
    )
    opaque_current = _snapshot(
        "sdk_api",
        installed="api-v1",
        latest="api-next",
        observed_at=LATER,
    )

    downgrade_diff = compare_provider_snapshots(previous, downgraded)
    opaque_diff = compare_provider_snapshots(
        opaque_previous, opaque_current
    )

    assert "installed_downgraded" in {
        row["kind"] for row in downgrade_diff["changes"]
    }
    assert "release_order_unknown" in {
        row["kind"] for row in opaque_diff["changes"]
    }
    assert opaque_diff["summary"]["decision"] == "newer_available"


def test_unchanged_snapshots_are_stable() -> None:
    previous = _snapshot("cli_package")
    current = _snapshot("cli_package", observed_at=LATER)

    diff = compare_provider_snapshots(previous, current)

    assert diff["summary"] == {
        "status": "no_change",
        "decision": "none",
        "change_count": 0,
        "calibration_impacted": False,
        "routing_change_allowed": False,
        "automatic_update_allowed": False,
    }


@pytest.mark.parametrize(
    ("surface", "before", "after", "kind"),
    [
        (
            "mcp_server",
            {"tools": ["read"]},
            {"tools": ["read", "write"]},
            "mcp_server_tools_changed",
        ),
        (
            "sdk_api",
            {"auth_schema": "bearer-v1"},
            {"auth_schema": "oauth-v2"},
            "sdk_api_auth_schema_changed",
        ),
        (
            "internal_adapter",
            {"translation_contract": "v1"},
            {"translation_contract": "v2"},
            "internal_adapter_translation_contract_changed",
        ),
    ],
)
def test_contract_dimension_changes_fail_closed(
    surface: str,
    before: dict,
    after: dict,
    kind: str,
) -> None:
    previous = _snapshot(surface, dimensions=before)
    current = _snapshot(surface, dimensions=after, observed_at=LATER)

    diff = compare_provider_snapshots(previous, current)

    assert kind in {row["kind"] for row in diff["changes"]}
    assert diff["summary"]["decision"] in {"blocked", "update_required"}
    assert diff["summary"]["calibration_impacted"] is True


def test_model_catalog_detects_add_remove_rename_and_metadata() -> None:
    base_model = {
        "id": "model-a",
        "aliases": [],
        "context": 1000,
        "tools": False,
        "structured_output": True,
        "price": "1",
        "quota": "standard",
        "lifecycle": "active",
    }
    previous = _snapshot(
        "model_catalog",
        dimensions={
            "model_id": [
                base_model,
                {**base_model, "id": "model-old"},
                {**base_model, "id": "model-removed"},
            ]
        },
    )
    current = _snapshot(
        "model_catalog",
        dimensions={
            "model_id": [
                {**base_model, "context": 2000, "price": "2"},
                {
                    **base_model,
                    "id": "model-new",
                    "aliases": ["model-old"],
                },
                {**base_model, "id": "model-added"},
            ]
        },
        observed_at=LATER,
    )

    diff = compare_provider_snapshots(previous, current)
    kinds = {row["kind"] for row in diff["changes"]}

    assert {
        "model_renamed",
        "model_added",
        "model_removed",
        "model_context_changed",
        "model_price_changed",
    } <= kinds
    assert diff["summary"]["decision"] == "blocked"
    assert diff["summary"]["calibration_impacted"] is True


def test_model_capability_change_blocks_until_exact_revalidation() -> None:
    previous = _snapshot(
        "model_catalog",
        dimensions={
            "model_id": [
                {
                    "id": "model",
                    "aliases": [],
                    "context": 1000,
                    "tools": True,
                    "structured_output": True,
                    "price": "1",
                    "quota": "standard",
                    "lifecycle": "active",
                }
            ]
        },
    )
    current = _snapshot(
        "model_catalog",
        dimensions={
            "model_id": [
                {
                    "id": "model",
                    "aliases": [],
                    "context": 500,
                    "tools": False,
                    "structured_output": False,
                    "price": "1",
                    "quota": "standard",
                    "lifecycle": "retired",
                }
            ]
        },
        observed_at=LATER,
    )

    diff = compare_provider_snapshots(previous, current)

    assert diff["summary"]["decision"] == "blocked"
    assert diff["summary"]["calibration_impacted"] is True


def test_model_addition_never_promotes_or_changes_routing() -> None:
    previous = _snapshot(
        "model_catalog", dimensions={"model_id": []}
    )
    current = _snapshot(
        "model_catalog",
        dimensions={
            "model_id": [
                {
                    "id": "new-model",
                    "aliases": [],
                    "context": 1000,
                    "tools": False,
                    "structured_output": False,
                    "price": "unknown",
                    "quota": "unknown",
                    "lifecycle": "active",
                }
            ]
        },
        observed_at=LATER,
    )

    diff = compare_provider_snapshots(previous, current)

    assert diff["changes"][0]["actionability"] == "owner_unclassified"
    assert diff["summary"]["decision"] == "newer_available"
    assert diff["summary"]["routing_change_allowed"] is False


def test_snapshot_identity_mismatch_is_rejected() -> None:
    previous = _snapshot("cli_package")
    current = deepcopy(previous)
    current["identity"]["component_id"] = "other"

    with pytest.raises(ValueError, match="digest drift"):
        compare_provider_snapshots(previous, current)
