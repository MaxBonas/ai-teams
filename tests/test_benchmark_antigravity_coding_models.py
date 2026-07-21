from scripts.benchmark_antigravity_coding_models import (
    BASELINE_MODEL,
    CHALLENGER_MODEL,
    aggregate_reports,
)
from scripts.benchmark_integrity import code_evaluation_contract


def _arm(*, passed: int, seconds: float, ruff: int = 0, status: str = "done") -> dict:
    return {
        "issue_status": status,
        "attempts": 1,
        "wall_seconds": seconds,
        "usage_available": False,
        "score": {
            "hidden_exit": 0,
            "hidden_passed": passed,
            "hidden_failed": 9 - passed,
            "hidden_errors": 0,
            "hidden_total": 9,
            "ruff_issues": ruff,
        },
    }


def test_aggregate_requires_balanced_three_seed_matrix() -> None:
    reports = [
        {"seed": seed, "case": "x", "evaluation_contract": code_evaluation_contract(), "arms": {BASELINE_MODEL: _arm(passed=8, seconds=8), CHALLENGER_MODEL: _arm(passed=9, seconds=20)}}
        for seed in (1, 2)
    ]
    aggregate = aggregate_reports(reports)
    assert aggregate["integrity"]["conclusion_allowed"] is False
    assert aggregate["conclusion"]["default_change_allowed"] is False


def test_challenger_must_improve_behavior_without_regression() -> None:
    reports = [
        {"seed": seed, "case": "x", "evaluation_contract": code_evaluation_contract(), "arms": {BASELINE_MODEL: _arm(passed=8, seconds=8), CHALLENGER_MODEL: _arm(passed=9, seconds=20)}}
        for seed in (1, 2, 3)
    ]
    aggregate = aggregate_reports(reports)
    assert aggregate["integrity"]["conclusion_allowed"] is True
    assert aggregate["conclusion"]["disposition"] == "promote_challenger"


def test_equal_quality_retains_faster_baseline_without_token_claims() -> None:
    reports = [
        {"seed": seed, "case": "x", "evaluation_contract": code_evaluation_contract(), "arms": {BASELINE_MODEL: _arm(passed=9, seconds=8), CHALLENGER_MODEL: _arm(passed=9, seconds=20)}}
        for seed in (1, 2, 3)
    ]
    aggregate = aggregate_reports(reports)
    assert aggregate["conclusion"]["disposition"] == "retain_baseline"
    assert aggregate["conclusion"]["economic_comparison_available"] is False


def test_equal_hidden_quality_can_promote_on_cleaner_durable_convergence() -> None:
    reports = [
        {
            "seed": seed,
            "case": "x",
            "evaluation_contract": code_evaluation_contract(),
            "arms": {
                BASELINE_MODEL: _arm(
                    passed=9, seconds=100, ruff=1 if seed > 1 else 0,
                    status="in_progress" if seed == 2 else "done",
                ),
                CHALLENGER_MODEL: _arm(passed=9, seconds=50, ruff=0),
            },
        }
        for seed in (1, 2, 3)
    ]
    aggregate = aggregate_reports(reports)
    assert aggregate["conclusion"]["disposition"] == "promote_challenger"
    assert aggregate["conclusion"]["default_change_allowed"] is True


def test_legacy_behavioral_report_cannot_promote_without_explicit_limits() -> None:
    reports = [
        {
            "seed": seed,
            "case": "x",
            "arms": {
                BASELINE_MODEL: _arm(passed=8, seconds=8),
                CHALLENGER_MODEL: _arm(passed=9, seconds=20),
            },
        }
        for seed in (1, 2, 3)
    ]

    aggregate = aggregate_reports(reports)

    assert aggregate["integrity"]["conclusion_allowed"] is True
    assert aggregate["integrity"]["promotion_allowed"] is False
    assert aggregate["conclusion"]["disposition"] == "insufficient_promotion_contract"
    assert aggregate["conclusion"]["default_change_allowed"] is False
