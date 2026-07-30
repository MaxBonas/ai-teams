from __future__ import annotations

from typing import Any

from aiteam.guided_setup_coverage import build_guided_setup_coverage


def _candidate(
    role: str,
    name: str,
    *,
    profile: str,
    perspective: str,
    pool: str,
    eligible: bool = True,
    channel: str = "subscription",
) -> dict[str, Any]:
    return {
        "candidate_id": name,
        "identity": {
            "profile_id": profile,
            "model_id": f"model-{name}",
            "provider_org": perspective,
            "model_vendor": perspective,
            "channel": channel,
            "perspective_key": perspective,
            "capacity_pool": pool,
        },
        "model_metadata": {
            "tier": "premium" if role in {"team_lead", "quorum_auditor"} else "standard",
            "caps": ["reasoning", "coding"],
            "price_note": "fixture",
        },
        "owner_selectable": True,
        "rank": 1,
        "selection_reason": "fixture",
        "contextual_compatibility": {"allowed": True, "code": "compatible"},
        "selection_score": {
            "score": 90,
            "auto_eligible": eligible,
            "hard_gates": {
                "configured": {"passed": True},
                "adapter_green": {"passed": True},
                "calibrated": {"passed": eligible},
            },
        },
    }


def _selection(role: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "selection_version": "model_selection_v1",
        "canonical_role": role,
        "candidates": candidates,
    }


def _full_fixture() -> dict[str, dict[str, Any]]:
    return {
        "team_lead": _selection(
            "team_lead",
            [
                _candidate(
                    "team_lead",
                    "lead",
                    profile="codex",
                    perspective="openai",
                    pool="chatgpt",
                )
            ],
        ),
        "quorum_auditor": _selection(
            "quorum_auditor",
            [
                _candidate(
                    "quorum_auditor",
                    "audit-a",
                    profile="codex",
                    perspective="openai",
                    pool="chatgpt",
                ),
                _candidate(
                    "quorum_auditor",
                    "audit-b",
                    profile="antigravity",
                    perspective="google",
                    pool="google-subscription",
                ),
            ],
        ),
        "engineer": _selection(
            "engineer",
            [
                _candidate(
                    "engineer",
                    "engineer",
                    profile="codex",
                    perspective="openai",
                    pool="chatgpt",
                )
            ],
        ),
        "reviewer": _selection(
            "reviewer",
            [
                _candidate(
                    "reviewer",
                    "reviewer",
                    profile="antigravity",
                    perspective="google",
                    pool="google-subscription",
                )
            ],
        ),
    }


def test_all_profiles_are_covered_only_by_auto_eligible_candidates() -> None:
    coverage = build_guided_setup_coverage(_full_fixture())

    assert coverage["profiles"]["solo_lead"]["ready"] is True
    assert coverage["profiles"]["lead_quorum"]["ready"] is True
    assert coverage["profiles"]["full_team"]["ready"] is True
    assert coverage["policy"]["manual_selection_grants_coverage"] is False


def test_owner_selectable_but_uncalibrated_candidate_does_not_cover_role() -> None:
    fixture = _full_fixture()
    fixture["team_lead"]["candidates"][0]["selection_score"][
        "auto_eligible"
    ] = False
    coverage = build_guided_setup_coverage(fixture)

    assert coverage["roles"]["team_lead"]["candidate_count"] == 1
    assert coverage["roles"]["team_lead"]["eligible_count"] == 0
    assert coverage["roles"]["team_lead"]["excluded_count"] == 1
    assert coverage["roles"]["team_lead"]["excluded_candidates"][0][
        "coverage_eligible"
    ] is False
    assert coverage["roles"]["team_lead"]["excluded_candidates"][0][
        "exclusion_reasons"
    ] == ["automation_gate_failed"]
    assert coverage["profiles"]["solo_lead"]["ready"] is False


def test_quorum_requires_two_distinct_perspectives_and_capacity_pools() -> None:
    fixture = _full_fixture()
    fixture["quorum_auditor"]["candidates"][1]["identity"].update(
        {"perspective_key": "openai", "capacity_pool": "chatgpt"}
    )
    coverage = build_guided_setup_coverage(fixture)
    quorum = coverage["profiles"]["lead_quorum"]["requirements"][1]

    assert quorum["eligible_count"] == 2
    assert quorum["status"] == "diversity_gap"
    assert coverage["profiles"]["lead_quorum"]["ready"] is False


def test_ready_profile_filter_prevents_unprepared_adapter_coverage() -> None:
    coverage = build_guided_setup_coverage(
        _full_fixture(),
        ready_profile_ids={"codex"},
    )

    assert coverage["profiles"]["solo_lead"]["ready"] is True
    assert coverage["profiles"]["lead_quorum"]["ready"] is False
    assert coverage["profiles"]["full_team"]["ready"] is False
    excluded = coverage["roles"]["reviewer"]["excluded_candidates"][0]
    assert excluded["exclusion_reasons"] == [
        "adapter_not_prepared_in_setup"
    ]


def test_local_models_have_zero_marginal_cost_without_extra_coverage() -> None:
    fixture = _full_fixture()
    fixture["engineer"] = _selection(
        "engineer",
        [
            _candidate(
                "engineer",
                "local-worker",
                profile="ollama",
                perspective="local",
                pool="local-machine",
                channel="local",
            )
        ],
    )
    coverage = build_guided_setup_coverage(fixture)
    candidate = coverage["roles"]["engineer"]["candidates"][0]

    assert candidate["economics"]["class"] == "zero_marginal"
    assert candidate["economics"]["marginal_cost"] == "zero"
    assert coverage["profiles"]["full_team"]["ready"] is True
