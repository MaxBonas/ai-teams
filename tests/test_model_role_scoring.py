from copy import deepcopy

import pytest

from aiteam.model_role_scoring import (
    MODEL_ROLE_SCORE_VERSION,
    MODEL_ROLE_SCORE_WEIGHTS,
    rank_model_role_scores,
    score_model_role,
)


def _candidate(candidate_id: str = "candidate:a", channel: str = "api") -> dict:
    return {
        "candidate_id": candidate_id,
        "identity": {
            "profile_id": candidate_id,
            "provider_org": "provider",
            "model_vendor": "vendor",
            "perspective_key": "vendor",
            "channel": channel,
            "capacity_pool": candidate_id,
            "model_id": "model-a",
        },
    }


def _components(channel: str = "api") -> dict:
    basis = {
        "api": "api_cost_per_accepted_task",
        "subscription": "subscription_quota_pressure",
        "local": "local_resource_throughput",
        "free_gateway": "gateway_capacity_pressure",
    }[channel]
    return {
        "quality": {"value": 90, "source": "hidden_suite"},
        "capability": {"value": 80, "source": "contract_fixture"},
        "reliability": {"value": 70, "source": "durable_runs"},
        "economy": {
            "value": 60,
            "source": "channel_normalizer",
            "basis": basis,
            "comparison_group": f"{channel}:same-task",
            "burden": 12,
        },
        "speed": {
            "value": 50,
            "source": "e2e_runs",
            "comparison_group": "same-task-and-host",
            "latency_ms": 2000,
        },
    }


def _evidence(**overrides) -> dict:
    value = {
        "status": "calibrated",
        "classes": ["behavioral_deterministic", "static_analysis"],
        "seeds": 3,
        "cases": 2,
        "required_tools": ["repo_write"],
        "covered_tools": ["repo_write"],
        "fresh": True,
        "provider_version": "1.0",
        "evaluated_at": "2026-07-22",
        "receipts": ["receipt.json"],
        "unmeasured_constructs": ["novel_architecture_quality"],
        "goodhart_risk": "low",
    }
    value.update(overrides)
    return value


def _gates(**overrides) -> dict:
    value = {
        "configured": True,
        "adapter_green": True,
        "model_verified": True,
        "selectable": True,
        "compatible": True,
        "automatic_policy": True,
        "calibrated": True,
        "fresh": True,
        "privacy": True,
        "tools": True,
        "workspace": True,
        "structured_output": True,
        "capacity_available": True,
    }
    value.update(overrides)
    return value


def _score(
    candidate_id: str = "candidate:a",
    channel: str = "api",
    *,
    components: dict | None = None,
    evidence: dict | None = None,
    gates: dict | None = None,
) -> dict:
    return score_model_role(
        candidate=_candidate(candidate_id, channel),
        role="software_engineer",
        components=components or _components(channel),
        evidence=evidence or _evidence(),
        hard_gates=gates or _gates(),
    )


def test_weighted_score_uses_preregistered_breakdown() -> None:
    result = _score()

    assert MODEL_ROLE_SCORE_WEIGHTS == {
        "quality": 40,
        "capability": 15,
        "reliability": 15,
        "economy": 20,
        "speed": 10,
    }
    assert result["score_version"] == MODEL_ROLE_SCORE_VERSION
    assert result["canonical_role"] == "engineer"
    assert result["score"] == 75.5
    assert result["known_weight_percent"] == 100
    assert result["auto_eligible"] is True
    assert result["rollout"] == "shadow_only"


def test_confidence_is_separate_and_never_multiplies_score() -> None:
    strong = _score()
    weak = _score(
        evidence=_evidence(
            status="partial", classes=["lexical_rubric"], goodhart_risk="material"
        )
    )

    assert strong["score"] == weak["score"] == 75.5
    assert strong["confidence"]["value"] > weak["confidence"]["value"]
    assert weak["auto_eligible"] is False


def test_unknown_component_is_not_zero_or_an_advantage() -> None:
    components = _components()
    components["economy"] = {
        "value": None,
        "source": "missing",
        "reason": "quota_unknown",
    }
    result = _score(components=components)

    assert result["score"] is None
    assert result["known_weight_percent"] == 80
    assert result["score_range"] == {"minimum": 63.5, "maximum": 83.5}
    assert result["breakdown"]["economy"]["status"] == "unknown"
    assert "economy" in result["unknown_components"]
    assert result["confidence"]["evidence_value"] == 100
    assert result["confidence"]["value"] == 80
    assert result["confidence"]["caps"] == ["unknown_score_components"]
    assert result["auto_eligible"] is False


@pytest.mark.parametrize(
    ("channel", "basis"),
    [
        ("api", "api_cost_per_accepted_task"),
        ("subscription", "subscription_quota_pressure"),
        ("local", "local_resource_throughput"),
        ("free_gateway", "gateway_capacity_pressure"),
    ],
)
def test_economy_requires_channel_specific_basis(channel: str, basis: str) -> None:
    result = _score(channel=channel)

    assert result["breakdown"]["economy"]["basis"] == basis
    assert result["breakdown"]["economy"]["status"] == "known"


def test_wrong_economy_unit_fails_closed_as_unknown() -> None:
    components = _components("subscription")
    components["economy"]["basis"] = "api_cost_per_accepted_task"
    result = _score(channel="subscription", components=components)

    assert result["score"] is None
    assert result["breakdown"]["economy"]["status"] == "unknown"
    assert result["breakdown"]["economy"]["reason"].startswith("economy_basis_mismatch")


def test_stale_evidence_overrides_claimed_pass_gate() -> None:
    result = _score(evidence=_evidence(fresh=False), gates=_gates(fresh=True))

    assert result["hard_gates"]["fresh"] == {
        "passed": False,
        "reason": "evidence_stale_or_unproven",
        "source": "evidence",
    }
    assert result["auto_eligible"] is False
    assert result["score"] == 75.5


def test_high_score_cannot_bypass_privacy_or_tools_gate() -> None:
    components = {
        name: {**value, "value": 100} for name, value in _components().items()
    }
    result = _score(components=components, gates=_gates(privacy=False, tools=None))

    assert result["score"] == 100
    assert result["auto_eligible"] is False
    assert any(
        reason.startswith("gate:privacy")
        for reason in result["auto_ineligible_reasons"]
    )
    assert any(
        reason.startswith("gate:tools") for reason in result["auto_ineligible_reasons"]
    )


def test_out_of_range_component_is_rejected() -> None:
    components = _components()
    components["quality"]["value"] = 101

    with pytest.raises(ValueError, match="quality component"):
        _score(components=components)


def test_tie_breaks_by_evidence_then_economy_latency_and_identity() -> None:
    lexical = _score(
        "candidate:lexical", evidence=_evidence(classes=["lexical_rubric"])
    )
    behavioral = _score("candidate:behavioral")
    assert [
        row["candidate_id"] for row in rank_model_role_scores([lexical, behavioral])
    ] == [
        "candidate:behavioral",
        "candidate:lexical",
    ]

    cheap_components = _components()
    cheap_components["economy"]["burden"] = 5
    cheap_components["speed"]["latency_ms"] = 3000
    fast_components = deepcopy(_components())
    fast_components["economy"]["burden"] = 10
    fast_components["speed"]["latency_ms"] = 1000
    cheap = _score("candidate:cheap", components=cheap_components)
    fast = _score("candidate:fast", components=fast_components)
    assert [row["candidate_id"] for row in rank_model_role_scores([fast, cheap])] == [
        "candidate:cheap",
        "candidate:fast",
    ]

    same_a = _score("candidate:a")
    same_b = _score("candidate:b")
    assert [
        row["candidate_id"] for row in rank_model_role_scores([same_b, same_a])
    ] == [
        "candidate:a",
        "candidate:b",
    ]


def test_non_comparable_economy_is_not_used_as_cross_channel_tie_break() -> None:
    api_components = _components("api")
    api_components["economy"]["burden"] = 1
    api_components["speed"]["latency_ms"] = 3000
    subscription_components = _components("subscription")
    subscription_components["economy"]["burden"] = 99
    subscription_components["speed"]["latency_ms"] = 1000
    api = _score("candidate:api", "api", components=api_components)
    subscription = _score(
        "candidate:subscription", "subscription", components=subscription_components
    )

    ranked = rank_model_role_scores([api, subscription])

    assert [row["candidate_id"] for row in ranked] == [
        "candidate:subscription",
        "candidate:api",
    ]
