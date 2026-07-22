from copy import deepcopy

from aiteam.model_selection import build_contextual_model_selection, same_profile_fallback


def _score(candidate_id: str, value: float, *, eligible: bool = True) -> dict:
    return {
        "candidate_id": candidate_id,
        "score": value,
        "score_range": {"minimum": value, "maximum": value},
        "breakdown": {"quality": {"value": value}},
        "confidence": {"value": 90.0, "evidence_rank": 90.0},
        "hard_gates": {
            name: {"passed": True, "reason": "fixture", "source": "fixture"}
            for name in (
                "configured", "adapter_green", "model_verified", "selectable",
                "compatible", "automatic_policy", "calibrated", "fresh", "privacy",
                "tools", "workspace", "structured_output", "capacity_available",
            )
        },
        "auto_eligible": eligible,
        "auto_ineligible_reasons": [] if eligible else ["fixture_ineligible"],
        "tie_break": {"evidence_rank": 90.0, "quality": value},
    }


def _candidate(candidate_id: str, profile_id: str, model: str, value: float) -> dict:
    return {
        "candidate_id": candidate_id,
        "identity": {
            "profile_id": profile_id,
            "provider_org": profile_id,
            "model_vendor": profile_id,
            "channel": "subscription",
            "capacity_pool": profile_id,
            "model_id": model,
        },
        "states": {
            "configured": {"value": True},
            "adapter_green": {"value": True},
            "selectable": {"value": True},
        },
        "provider_metadata": {},
        "model_metadata": {},
        "roles": [{
            "canonical_role": "reviewer",
            "score": _score(candidate_id, value),
        }],
    }


def test_same_profile_fallback_uses_canonical_rank_with_recovery_continuity() -> None:
    def row(model: str, rank: int, *, family_tier: str, automatic: bool = True) -> dict:
        return {
            "candidate_id": f"profile::{model}",
            "identity": {"profile_id": "profile", "model_id": model},
            "model_metadata": {"tier": family_tier},
            "owner_selectable": True,
            "rank": rank,
            "selection_reason": f"rank {rank}",
            "selection_score": {
                "hard_gates": {"automatic_policy": {"passed": automatic}}
            },
        }

    projection = {"candidates": [
        row("gpt-failed", 4, family_tier="tier_2"),
        row("claude-best", 1, family_tier="tier_2"),
        row("gpt-same-tier", 3, family_tier="tier_2"),
        row("gpt-manual", 2, family_tier="tier_2", automatic=False),
    ]}

    selected = same_profile_fallback(
        projection, profile_id="profile", failed_model="gpt-failed"
    )

    assert selected is not None
    assert selected["value"] == "gpt-same-tier"
    assert selected["candidate_id"] == "profile::gpt-same-tier"
    assert selected["changes_family"] is False
    assert selected["changes_tier"] is False


def test_same_profile_fallback_never_crosses_profile_or_automatic_policy() -> None:
    projection = {"candidates": [
        {
            "candidate_id": "other::gpt-ok",
            "identity": {"profile_id": "other", "model_id": "gpt-ok"},
            "model_metadata": {"tier": "tier_3"},
            "owner_selectable": True,
            "rank": 1,
            "selection_score": {
                "hard_gates": {"automatic_policy": {"passed": True}}
            },
        },
        {
            "candidate_id": "profile::gpt-manual",
            "identity": {"profile_id": "profile", "model_id": "gpt-manual"},
            "model_metadata": {"tier": "tier_3"},
            "owner_selectable": True,
            "rank": 2,
            "selection_score": {
                "hard_gates": {"automatic_policy": {"passed": False}}
            },
        },
    ]}

    assert same_profile_fallback(
        projection, profile_id="profile", failed_model="gpt-failed"
    ) is None


def _fixture() -> tuple[dict, list[dict], dict[str, list[dict]]]:
    read_model = {
        "schema_version": "model_catalog_read_model_v1",
        "score_version": "model_role_score_v1",
        "content_hash": "fixture",
        "candidates": [
            _candidate("high", "restricted", "high-model", 95),
            _candidate("lower", "owner", "lower-model", 80),
            {
                **_candidate("unscored", "owner", "unscored-model", 10),
                "roles": [],
            },
        ],
    }
    profiles = [
        {
            "id": "restricted", "status": "active", "data_policy": "non_confidential_only",
            "workspace_mode": "read", "structured_output": "json_schema",
        },
        {
            "id": "owner", "status": "active", "data_policy": "owner_account",
            "workspace_mode": "read", "structured_output": "json_schema",
        },
    ]
    options = {
        "restricted": [{
            "value": "high-model", "selectable": True, "tier": "standard",
            "allowed_roles": ["reviewer"], "caps": ["reasoning", "synthesis", "repo_read"],
            "structured_output": "json_schema",
        }],
        "owner": [
            {
                "value": model, "selectable": True, "tier": "standard",
                "allowed_roles": ["reviewer"], "caps": ["reasoning", "synthesis", "repo_read"],
                "structured_output": "json_schema",
            }
            for model in ("lower-model", "unscored-model")
        ],
    }
    return read_model, profiles, options


def test_context_gate_precedes_base_score_and_preserves_base_measurement() -> None:
    read_model, profiles, options = _fixture()
    original = deepcopy(read_model)

    result = build_contextual_model_selection(
        read_model,
        role="code_reviewer",
        profiles=profiles,
        options_by_profile=options,
        data_class="confidential",
    )

    assert [item["candidate_id"] for item in result["candidates"]] == [
        "lower", "high", "unscored",
    ]
    assert result["default"]["candidate_id"] == "lower"
    blocked = result["candidates"][1]
    assert blocked["base_score"]["score"] == 95
    assert blocked["selection_score"]["score"] == 95
    assert blocked["contextual_compatibility"]["code"] == "confidential_data_forbidden"
    assert blocked["selection_score"]["hard_gates"]["privacy"]["passed"] is False
    assert read_model == original


def test_unscored_pairs_remain_visible_but_never_become_default() -> None:
    read_model, profiles, options = _fixture()
    for candidate in read_model["candidates"]:
        candidate["roles"] = []

    result = build_contextual_model_selection(
        read_model,
        role="reviewer",
        profiles=profiles,
        options_by_profile=options,
    )

    assert result["counts"] == {"candidates": 3, "auto_eligible": 0, "owner_selectable": 3}
    assert result["default"] == {
        "candidate_id": None,
        "action": "preserve_explicit_or_require_owner",
        "reason": "no_auto_eligible_candidate",
        "runner_up_candidate_id": None,
        "advantage": None,
    }
    assert all(item["selection_score"]["score"] is None for item in result["candidates"])


def test_owner_can_select_compatible_candidate_that_is_not_auto_eligible() -> None:
    read_model, profiles, options = _fixture()
    read_model["candidates"][1]["roles"][0]["score"] = _score(
        "lower", 80, eligible=False
    )

    result = build_contextual_model_selection(
        read_model,
        role="reviewer",
        profiles=profiles,
        options_by_profile=options,
    )

    lower = next(item for item in result["candidates"] if item["candidate_id"] == "lower")
    assert lower["owner_selectable"] is True
    assert lower["selection_score"]["auto_eligible"] is False


def test_observed_exhaustion_is_a_hard_gate_before_ranking() -> None:
    read_model, profiles, options = _fixture()

    result = build_contextual_model_selection(
        read_model,
        role="reviewer",
        profiles=profiles,
        options_by_profile=options,
        capacity_by_profile={
            "restricted": {
                "profile_id": "restricted",
                "state": "exhausted_observed",
                "source": "subscription_quota_snapshot",
            }
        },
    )

    assert result["candidates"][0]["candidate_id"] == "lower"
    high = next(item for item in result["candidates"] if item["candidate_id"] == "high")
    assert high["owner_selectable"] is False
    assert "cuota" in high["disabled_reason"].lower()
    assert high["selection_score"]["hard_gates"]["capacity_available"] == {
        "passed": False,
        "reason": "capacity:exhausted_observed",
        "source": "subscription_quota_snapshot",
    }


def test_owner_quota_policy_can_replace_only_contextual_economy_component() -> None:
    read_model, profiles, options = _fixture()
    base = read_model["candidates"][1]["roles"][0]["score"]
    base["breakdown"] = {
        name: {
            "status": "known",
            "value": 80.0 if name != "economy" else 50.0,
            "weight_percent": weight,
            "weighted_points": (80.0 if name != "economy" else 50.0) * weight / 100,
        }
        for name, weight in {
            "quality": 40, "capability": 15, "reliability": 15, "economy": 20, "speed": 10,
        }.items()
    }
    base["score"] = 74.0
    base["score_range"] = {"minimum": 74.0, "maximum": 74.0}
    base["known_weight_percent"] = 100
    base["unknown_components"] = []
    base["confidence"]["evidence_value"] = 90.0

    result = build_contextual_model_selection(
        read_model,
        role="reviewer",
        profiles=profiles,
        options_by_profile=options,
        capacity_by_profile={
            "owner": {
                "profile_id": "owner",
                "state": "metered",
                "source": "subscription_quota_snapshot",
                "forecast": {"source": "owner_config", "utilization": 0.9},
            }
        },
    )

    lower = next(item for item in result["candidates"] if item["candidate_id"] == "lower")
    assert lower["base_score"]["score"] == 74.0
    assert lower["selection_score"]["score"] == 66.0
    assert lower["selection_score"]["breakdown"]["economy"]["value"] == 10.0
    assert lower["selection_score"]["context_adjustments"]["numeric_components_changed"] == ["economy"]
