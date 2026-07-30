from __future__ import annotations

from copy import deepcopy

import pytest

from aiteam.provider_change_intelligence import (
    build_provider_change_inventory,
    load_provider_change_contract,
    validate_provider_change_contract,
    validate_provider_change_inventory,
)
from aiteam.user_config import DEFAULT_ADAPTER_PROFILES
from scripts.audit_provider_change_contract import build_report


def test_inventory_covers_every_builtin_profile_and_surface() -> None:
    inventory = build_provider_change_inventory()
    components = inventory["components"]

    assert {
        row["profile_id"]
        for row in components
        if row["profile_id"] is not None
    } == {row["id"] for row in DEFAULT_ADAPTER_PROFILES}
    assert {row["surface"] for row in components} == {
        "cli_package",
        "mcp_server",
        "sdk_api",
        "internal_adapter",
        "model_catalog",
    }
    assert sum(row["surface"] == "mcp_server" for row in components) == 3


def test_each_profile_separates_transport_adapter_and_catalog() -> None:
    inventory = build_provider_change_inventory()
    for profile in DEFAULT_ADAPTER_PROFILES:
        rows = [
            row
            for row in inventory["components"]
            if row["profile_id"] == profile["id"]
        ]
        surfaces = {row["surface"] for row in rows}
        assert {"internal_adapter", "model_catalog"} <= surfaces
        assert {"cli_package", "sdk_api"} & surfaces


def test_api_and_catalog_dimensions_do_not_collapse_schema_or_metadata() -> None:
    inventory = build_provider_change_inventory()
    api = next(
        row for row in inventory["components"] if row["surface"] == "sdk_api"
    )
    catalog = next(
        row
        for row in inventory["components"]
        if row["surface"] == "model_catalog"
    )

    assert {
        "sdk_package",
        "api_version",
        "endpoint",
        "auth_schema",
        "request_schema",
        "response_schema",
    } == set(api["dimensions"])
    assert {
        "model_id",
        "alias",
        "context",
        "tools",
        "structured_output",
        "price",
        "quota",
        "lifecycle",
    } == set(catalog["dimensions"])


def test_subscription_api_and_local_channels_remain_distinct() -> None:
    inventory = build_provider_change_inventory()
    scopes = {
        row["profile_id"]: row["channel_id"]
        for row in inventory["components"]
        if row["profile_id"] is not None
    }

    assert scopes["codex_subscription"] == "subscription"
    assert scopes["openai_api"] == "api"
    assert scopes["local_qwen_ollama"] == "local"
    assert scopes["gemini_api"] == "api"
    assert scopes["antigravity_subscription"] == "subscription"


def test_unprobed_facts_stay_unknown_without_timestamp_or_value() -> None:
    inventory = build_provider_change_inventory()
    unknown = [
        fact
        for row in inventory["components"]
        for fact in row["facts"].values()
        if fact["state"] == "unknown"
    ]

    assert unknown
    assert all(fact["value"] is None for fact in unknown)
    assert all(fact["source"]["observed_at"] is None for fact in unknown)


def test_discovery_cannot_claim_supported_version() -> None:
    inventory = build_provider_change_inventory()
    tampered = deepcopy(inventory)
    component = tampered["components"][0]
    component["facts"]["supported_version"]["source"].update(
        {
            "kind": "authenticated_discovery",
            "official": True,
        }
    )

    with pytest.raises(
        ValueError, match="cannot establish supported_version"
    ):
        validate_provider_change_inventory(tampered)


def test_unknown_fact_cannot_masquerade_as_observed() -> None:
    inventory = build_provider_change_inventory()
    tampered = deepcopy(inventory)
    tampered["components"][0]["facts"]["latest_known_version"]["source"][
        "observed_at"
    ] = "2026-07-30T00:00:00+00:00"

    with pytest.raises(
        ValueError, match="unknown facts cannot claim"
    ):
        validate_provider_change_inventory(tampered)


def test_contract_rejects_web_or_commercial_name_authority() -> None:
    contract = load_provider_change_contract()
    contract["source_kinds"]["web"] = {
        "authoritative_for": ["latest_known_version"],
        "requires_official": False,
    }

    with pytest.raises(ValueError, match="web/name sources are forbidden"):
        validate_provider_change_contract(contract)


def test_inventory_digest_detects_tampering() -> None:
    inventory = build_provider_change_inventory()
    tampered = deepcopy(inventory)
    tampered["components"][0]["provider_id"] = "other"

    with pytest.raises(ValueError, match="digest drift"):
        validate_provider_change_inventory(tampered)


def test_contract_audit_is_green_and_side_effect_free() -> None:
    report = build_report()

    assert report["summary"] == {
        "passed": 7,
        "total": 7,
        "contract_ready": True,
    }
    assert report["counts"] == {
        "profiles": 12,
        "components": 42,
        "mcp_servers": 3,
        "surfaces": 5,
    }
    assert report["scope"]["secrets_read"] is False
    assert report["scope"]["network_attempted"] is False
    assert report["scope"]["routing_authority_granted"] is False
