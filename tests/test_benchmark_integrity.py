from __future__ import annotations

from scripts.benchmark_integrity import (
    audit_ab_series,
    audit_quorum_series,
    audit_evaluation_contract,
    code_evaluation_contract,
    quorum_evaluation_contract,
)


def _ab_report(seed: int, arm: str) -> dict:
    return {
        "case": "case-a",
        "seed": seed,
        "config": {"team_run_profile": arm},
        "evaluation_contract": code_evaluation_contract(),
        "arms": {
            "team": {
                "score": {
                    "hidden_exit": 0,
                    "hidden_passed": 10,
                    "hidden_failed": 0,
                    "hidden_errors": 0,
                    "hidden_total": 10,
                    "ruff_issues": 0,
                }
            }
        },
    }


def test_ab_series_requires_balanced_arm_seed_matrix() -> None:
    reports = [
        _ab_report(1, "solo_lead"),
        _ab_report(1, "full_team"),
        _ab_report(2, "solo_lead"),
        _ab_report(2, "full_team"),
    ]

    audit = audit_ab_series(
        reports, required_arms=("solo_lead", "full_team"), min_seeds=2
    )

    assert audit["conclusion_allowed"] is True
    assert audit["matched_seed_count"] == 2
    assert audit["missing_cells"] == []
    assert audit["goodhart_risk"] == "residual"
    assert audit["promotion_allowed"] is True


def test_evaluation_contract_rejects_lexical_only_claim_and_missing_limits() -> None:
    audit = audit_evaluation_contract({
        "evaluators": [{"id": "keywords", "class": "lexical_coverage"}],
        "independent_semantic_or_structural": True,
        "goodhart_risk": "material",
    })

    assert audit["promotion_ready"] is False
    assert "independent_evidence_flag_inconsistent" in audit["issues"]
    assert "constructs_not_measured_missing" in audit["issues"]


def test_legacy_hidden_suite_keeps_directional_conclusion_but_not_promotion() -> None:
    reports = [
        {key: value for key, value in _ab_report(seed, arm).items() if key != "evaluation_contract"}
        for seed in (1, 2)
        for arm in ("solo_lead", "full_team")
    ]

    audit = audit_ab_series(
        reports, required_arms=("solo_lead", "full_team"), min_seeds=2
    )

    assert audit["conclusion_allowed"] is True
    assert audit["promotion_allowed"] is False
    assert audit["goodhart_risk"] == "material"


def test_ab_series_refuses_missing_arm_and_unseeded_evidence() -> None:
    reports = [
        _ab_report(1, "solo_lead"),
        {**_ab_report(2, "solo_lead"), "seed": None},
    ]

    audit = audit_ab_series(
        reports, required_arms=("solo_lead", "full_team"), min_seeds=2
    )

    assert audit["conclusion_allowed"] is False
    assert "arm_seed_matrix_incomplete" in audit["issues"]
    assert "unseeded_reports_excluded" in audit["issues"]


def _quorum_report(delta: float, provider: str, *, structural: bool = True) -> dict:
    contract = quorum_evaluation_contract(
        base_structural={"valid": True},
        final_structural={"valid": structural},
    )
    return {
        "benchmark": "lead_quorum_plan_quality",
        "delta_score_pct": delta,
        "base": {"passes_hard_gate": True},
        "final": {"passes_hard_gate": True},
        "evaluation_contract": contract,
        "provenance": {
            "session": {"status": "accepted"},
            "contributions": [{
                "valid": 1,
                "run_id": f"run-{provider}-{delta}",
                "provider": provider,
                "model": f"model-{provider}",
                "channel": "api",
            }],
        },
    }


def test_quorum_series_enforces_sample_provenance_range_and_structure() -> None:
    reports = [
        _quorum_report(2.0, "openai"),
        _quorum_report(4.0, "anthropic"),
        _quorum_report(6.0, "openai"),
        {"completed": False, "failure": "quota"},
    ]

    audit = audit_quorum_series(reports, min_sessions=3, min_providers=2)

    assert audit["conclusion_allowed"] is True
    assert audit["accepted_sessions"] == 3
    assert audit["excluded_incomplete_sessions"] == 1
    assert audit["delta_median"] == 4.0
    assert audit["delta_range"] == [2.0, 6.0]
    assert audit["promotion_allowed"] is True


def test_quorum_series_refuses_unstable_or_lexical_only_sample() -> None:
    lexical_only = _quorum_report(3.0, "openai")
    lexical_only["evaluation_contract"]["evaluators"] = [
        {"id": "keywords", "class": "lexical_coverage", "blind": True}
    ]
    reports = [
        _quorum_report(-2.0, "openai"),
        _quorum_report(1.0, "anthropic"),
        lexical_only,
    ]

    audit = audit_quorum_series(reports, min_sessions=3, min_providers=2)

    assert audit["conclusion_allowed"] is False
    assert "delta_sign_unstable" in audit["issues"]
    assert "independent_structural_evidence_missing" in audit["issues"]
    assert audit["goodhart_risk"] == "high"
