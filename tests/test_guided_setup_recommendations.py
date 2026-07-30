from __future__ import annotations

from typing import Any

from aiteam.guided_setup_recommendations import (
    build_progressive_recommendations,
)


def _coverage(
    *,
    lead_ready: bool = False,
    quorum_ready: bool = False,
    full_team_ready: bool = False,
    worker: dict[str, Any] | None = None,
    recommended_profile: str = "solo_lead",
) -> dict[str, Any]:
    lead_candidate = {
        "candidate_id": "codex:lead",
        "profile_id": "codex_subscription",
        "economics": {"class": "zero_marginal"},
    }
    return {
        "schema_version": "guided_setup_coverage_v1",
        "recommended_profile": recommended_profile,
        "recommended_profile_ready": {
            "solo_lead": lead_ready,
            "lead_quorum": quorum_ready,
            "full_team": full_team_ready,
        }[recommended_profile],
        "profiles": {
            "solo_lead": {
                "ready": lead_ready,
                "requirements": [],
            },
            "lead_quorum": {
                "ready": quorum_ready,
                "requirements": []
                if quorum_ready
                else [
                    {
                        "role": "quorum_auditor",
                        "status": "diversity_gap",
                        "missing_count": 0,
                        "perspective_count": 1,
                        "capacity_pool_count": 1,
                    }
                ],
            },
            "full_team": {
                "ready": full_team_ready,
                "requirements": []
                if full_team_ready
                else [
                    {
                        "role": "engineer",
                        "status": "missing",
                        "missing_count": 1,
                        "perspective_count": 0,
                        "capacity_pool_count": 0,
                    }
                ],
            },
        },
        "roles": {
            "team_lead": {
                "candidates": [lead_candidate] if lead_ready else [],
            },
            "worker": {"candidates": [worker] if worker else []},
        },
    }


def _preparation(*adapters: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "guided_setup_preparation_v1",
        "adapters": list(adapters),
    }


def _adapter(
    profile_id: str,
    *,
    state: str,
    primary: bool = True,
) -> dict[str, Any]:
    return {
        "id": profile_id,
        "state": state,
        "primary_candidate": primary,
        "stages": {
            "installation": "passed",
            "version": "passed",
            "authentication": "failed" if state == "blocked" else "passed",
            "catalog": "not_checked" if state != "ready" else "passed",
            "health": "not_checked" if state != "ready" else "passed",
            "contract": "not_checked" if state != "ready" else "passed",
        },
    }


def test_recommends_one_minimum_lead_path_and_keeps_alternatives() -> None:
    result = build_progressive_recommendations(
        _coverage(),
        _preparation(
            _adapter("codex_subscription", state="blocked"),
            _adapter("antigravity_subscription", state="unverified"),
        ),
    )

    action = result["next_action"]
    assert action["code"] == "complete_lead_adapter"
    assert action["profile_id"] == "codex_subscription"
    assert action["alternative_profile_ids"] == [
        "antigravity_subscription"
    ]
    assert "installation" not in action["pending_stages"]
    assert action["pending_stages"] == [
        "authentication",
        "catalog",
        "health",
        "contract",
    ]


def test_ready_adapter_without_eligible_lead_never_requests_reinstall() -> None:
    result = build_progressive_recommendations(
        _coverage(),
        _preparation(_adapter("codex_subscription", state="ready")),
    )

    assert result["next_action"]["code"] == (
        "restore_lead_model_eligibility"
    )
    assert result["policy"]["ready_adapter_reinstall_allowed"] is False


def test_ready_minimum_route_moves_to_required_quorum_gap() -> None:
    result = build_progressive_recommendations(
        _coverage(
            lead_ready=True,
            recommended_profile="lead_quorum",
        ),
        _preparation(_adapter("codex_subscription", state="ready")),
    )

    assert result["phases"][0]["status"] == "ready"
    assert result["next_action"]["code"] == "expand_quorum_diversity"
    assert result["next_action"]["required"] is True
    assert result["next_action"]["gaps"][0]["status"] == "diversity_gap"


def test_zero_marginal_worker_is_optional_and_last() -> None:
    worker = {
        "candidate_id": "local:worker",
        "profile_id": "ollama",
        "economics": {"class": "zero_marginal"},
    }
    result = build_progressive_recommendations(
        _coverage(
            lead_ready=True,
            quorum_ready=True,
            full_team_ready=True,
            worker=worker,
        ),
        _preparation(_adapter("codex_subscription", state="ready")),
    )

    assert result["next_action"]["code"] == "consider_economic_worker"
    assert result["next_action"]["required"] is False
    assert result["next_action"]["priority"] == 40


def test_no_declared_lead_channel_fails_closed_without_install_choice() -> None:
    result = build_progressive_recommendations(
        _coverage(),
        _preparation(),
    )

    assert result["next_action"]["code"] == "choose_lead_channel"
    assert result["next_action"]["profile_id"] is None
    assert result["policy"]["automatic_install"] is False
