from datetime import datetime, timezone

from aiteam.model_catalog_projection import (
    MODEL_CATALOG_IDENTITY_SCHEMA_VERSION,
    MODEL_CATALOG_STATE_NAMES,
    build_model_catalog_identity_projection,
)
from aiteam.model_catalog_api import CATALOG_STATE_NAMES


OBSERVED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def test_api_uses_the_canonical_identity_state_contract() -> None:
    assert CATALOG_STATE_NAMES is MODEL_CATALOG_STATE_NAMES


def _profiles() -> list[dict]:
    return [
        {
            "id": "openai_api",
            "provider": "openai",
            "channel": "api",
            "health": {"status": "ok", "version": "responses-v1"},
            "configured": True,
            "model_options": [
                {
                    "value": "gpt-5.6-sol",
                    "label": "Sol",
                    "best_for": ["lead", "team_lead"],
                    "selectable": True,
                    "verification_status": "verified",
                }
            ],
        },
        {
            "id": "codex_subscription",
            "provider": "openai-codex",
            "channel": "subscription",
            "health": {"status": "installed", "version": "0.145.0"},
            "model_options": [
                {"value": "gpt-5.6-sol", "best_for": ["lead"], "selectable": False}
            ],
        },
        {
            "id": "ollama_local",
            "provider": "ollama",
            "channel": "local",
            "health": {"status": "untested"},
            "model_options": [{"value": "gemma4:26b", "best_for": ["engineer"]}],
        },
        {
            "id": "opencode_zen_free",
            "provider": "opencode-zen",
            "channel": "free_gateway",
            "health": {"status": "ok", "version": "1.18.4"},
            "model_options": [
                {
                    "value": "opencode/deepseek-v4-flash-free",
                    "automatic": False,
                    "requires_probe": True,
                    "availability": "catalogued",
                }
            ],
        },
    ]


def test_projection_keeps_same_model_separate_by_operational_channel() -> None:
    projection = build_model_catalog_identity_projection(
        profiles=_profiles(), observed_at=OBSERVED_AT
    )

    assert projection["schema_version"] == MODEL_CATALOG_IDENTITY_SCHEMA_VERSION
    rows = [
        row
        for row in projection["candidates"]
        if row["identity"]["model_id"] == "gpt-5.6-sol"
    ]
    assert len(rows) == 2
    assert {row["identity"]["channel"] for row in rows} == {"api", "subscription"}
    assert len({row["candidate_id"] for row in rows}) == 2
    assert {row["identity"]["model_vendor"] for row in rows} == {"openai"}
    assert {row["identity"]["capacity_pool"] for row in rows} == {
        "openai_api",
        "codex_subscription",
    }


def test_discovery_catalogues_but_does_not_verify_execution() -> None:
    projection = build_model_catalog_identity_projection(
        profiles=_profiles(),
        discovered_models=[
            {
                "profile_id": "ollama_local",
                "model": "qwen-local:latest",
                "source": "ollama list",
                "provider_version": "0.9.0",
            }
        ],
        observed_at=OBSERVED_AT,
    )
    row = next(
        row
        for row in projection["candidates"]
        if row["identity"]["model_id"] == "qwen-local:latest"
    )

    assert row["states"]["catalogued"]["value"] is True
    assert row["states"]["model_verified"]["value"] is False
    assert row["states"]["model_verified"]["reason"] == "discovery_is_not_execution"
    assert row["states"]["selectable"]["value"] is False


def test_historical_unknown_profile_remains_visible_with_exact_identity() -> None:
    projection = build_model_catalog_identity_projection(
        profiles=_profiles(),
        historical_models=[
            {
                "profile_id": "retired_anthropic_api",
                "provider": "anthropic",
                "channel": "api",
                "model": "claude-historical",
                "source": "runs.adapter_config_json",
                "observed_at": "2026-05-01T08:00:00+00:00",
            }
        ],
        observed_at=OBSERVED_AT,
    )
    row = next(
        row
        for row in projection["candidates"]
        if row["identity"]["profile_id"] == "retired_anthropic_api"
    )

    assert row["identity"]["provider_org"] == "anthropic"
    assert row["identity"]["channel"] == "api"
    assert row["states"]["catalogued"]["value"] is True
    assert row["states"]["configured"]["value"] is False
    assert row["sources"][0]["kind"] == "historical_run"


def test_every_channel_fixture_exposes_orthogonal_state_provenance() -> None:
    projection = build_model_catalog_identity_projection(
        profiles=_profiles(), observed_at=OBSERVED_AT
    )

    assert {row["identity"]["channel"] for row in projection["candidates"]} == {
        "api",
        "subscription",
        "local",
        "free_gateway",
    }
    for row in projection["candidates"]:
        assert tuple(row["states"]) == MODEL_CATALOG_STATE_NAMES
        assert "available" not in row
        for state in row["states"].values():
            assert set(state) == {"value", "reason", "source", "version", "observed_at"}
            assert state["reason"]
            assert state["source"]
            assert state["observed_at"]

    manual = next(
        row
        for row in projection["candidates"]
        if row["identity"]["profile_id"] == "opencode_zen_free"
    )
    assert manual["states"]["manual_only"]["value"] is True
    assert manual["states"]["adapter_green"]["value"] is True
    assert manual["states"]["compatible"]["value"] is None
    assert manual["states"]["calibrated"]["value"] is None


def test_projection_is_deterministic_for_same_inputs_and_timestamp() -> None:
    first = build_model_catalog_identity_projection(
        profiles=_profiles(), observed_at=OBSERVED_AT
    )
    second = build_model_catalog_identity_projection(
        profiles=reversed(_profiles()), observed_at=OBSERVED_AT
    )

    assert first == second


def test_old_history_cannot_override_current_catalog_policy() -> None:
    kwargs = {
        "profiles": [{
            "id": "openai_api",
            "provider": "openai",
            "channel": "api",
            "configured": True,
        }],
        "declared_options_by_profile": {
            "openai_api": [{
                "value": "gpt-5.6-sol",
                "label": "Current Sol",
                "selectable": True,
                "automatic": True,
                "availability": "verified",
                "observed_at": "2026-07-22T10:00:00+00:00",
            }]
        },
        "historical_models": [{
            "profile_id": "openai_api",
            "model": "gpt-5.6-sol",
            "label": "Old Sol",
            "selectable": False,
            "automatic": False,
            "availability": "unavailable",
            "observed_at": "2026-01-01T00:00:00+00:00",
        }],
        "observed_at": OBSERVED_AT,
    }

    row = build_model_catalog_identity_projection(**kwargs)["candidates"][0]

    assert row["label"] == "Current Sol"
    assert row["states"]["selectable"]["value"] is True
    assert row["states"]["manual_only"]["value"] is False
    assert row["states"]["blocked"]["value"] is False
    assert row["states"]["selectable"]["source"] == "declared_catalog"
    assert row["states"]["selectable"]["observed_at"] == "2026-07-22T10:00:00+00:00"


def test_each_state_keeps_the_provenance_of_its_winning_field() -> None:
    projection = build_model_catalog_identity_projection(
        profiles=[{"id": "p", "provider": "openai", "channel": "api"}],
        declared_options_by_profile={
            "p": [{
                "value": "gpt-5.6-sol",
                "selectable": False,
                "automatic": True,
                "availability": "catalogued",
                "source": "static catalog",
                "observed_at": "2026-07-20T00:00:00+00:00",
            }]
        },
        discovered_models=[{
            "profile_id": "p",
            "model": "gpt-5.6-sol",
            "selectable": True,
            "availability": "verified",
            "verified": True,
            "source": "provider discovery",
            "provider_version": "v2",
            "observed_at": "2026-07-21T00:00:00+00:00",
        }],
        historical_models=[{
            "profile_id": "p",
            "model": "gpt-5.6-sol",
            "automatic": False,
            "source": "newer historical run",
            "observed_at": "2026-07-22T00:00:00+00:00",
        }],
        observed_at=OBSERVED_AT,
    )
    states = projection["candidates"][0]["states"]

    assert states["selectable"]["value"] is True
    assert states["selectable"]["source"] == "provider discovery"
    assert states["selectable"]["version"] == "v2"
    assert states["manual_only"]["value"] is False
    assert states["manual_only"]["source"] == "static catalog"


def test_conflicting_duplicate_profile_identity_fails_closed() -> None:
    profiles = [
        {"id": "shared", "provider": "openai", "channel": "api"},
        {"id": "shared", "provider": "anthropic", "channel": "subscription"},
    ]

    try:
        build_model_catalog_identity_projection(
            profiles=profiles,
            observed_at=OBSERVED_AT,
        )
    except ValueError as exc:
        assert "conflicting profile identity" in str(exc)
    else:
        raise AssertionError("conflicting profile identity must fail closed")


def test_exact_model_blocked_state_is_not_selectable() -> None:
    projection = build_model_catalog_identity_projection(
        profiles=[{"id": "p", "provider": "openai", "channel": "api"}],
        declared_options_by_profile={
            "p": [{
                "value": "gpt-5.6-sol",
                "availability": "blocked",
                "availability_reason": "policy_denied",
                "selectable": True,
            }]
        },
        observed_at=OBSERVED_AT,
    )
    states = projection["candidates"][0]["states"]

    assert states["blocked"]["value"] is True
    assert states["blocked"]["reason"] == "policy_denied"
    assert states["selectable"]["value"] is False


def test_conflicting_historical_operational_identity_fails_closed() -> None:
    observations = [
        {
            "profile_id": "old-profile",
            "provider": "openai",
            "channel": "api",
            "model": "shared-model",
        },
        {
            "profile_id": "old-profile",
            "provider": "anthropic",
            "channel": "subscription",
            "model": "shared-model",
        },
    ]

    try:
        build_model_catalog_identity_projection(
            profiles=[], historical_models=observations, observed_at=OBSERVED_AT
        )
    except ValueError as exc:
        assert "conflicting operational identity" in str(exc)
    else:
        raise AssertionError("conflicting historical identity must fail closed")
