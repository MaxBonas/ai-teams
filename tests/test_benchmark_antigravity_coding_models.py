from scripts.benchmark_antigravity_coding_models import (
    BASELINE_MODEL,
    CHALLENGER_MODEL,
    aggregate_diverse_family_reports,
    aggregate_reports,
    aggregate_single_model_reports,
    bootstrap_profile_ids,
    model_workspace_name,
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


def test_single_model_aggregate_calibrates_exact_pair_without_fake_baseline() -> None:
    reports = [
        {
            "seed": seed,
            "case": "cli_conversor",
            "provider_version": "0.145.0",
            "evaluation_contract": code_evaluation_contract(),
            "_source_receipt": f"local-engineer-seed-{seed}.json",
            "arms": {
                "gpt-5.6-terra": {
                    **_arm(passed=9, seconds=50 + seed),
                    "input_tokens": 100 * seed,
                    "output_tokens": 10 * seed,
                    "usage_available": True,
                }
            },
        }
        for seed in (1, 2, 3)
    ]

    aggregate = aggregate_single_model_reports(
        reports, model="gpt-5.6-terra", profile_id="codex_subscription"
    )

    assert aggregate["matrix_complete"] is True
    assert aggregate["samples_passed"] == 3
    assert aggregate["usage"]["input_tokens"] == 600
    assert aggregate["integrity"]["sources_bound"] is True
    assert len(aggregate["sample_manifest"][0]["evidence_sha256"]) == 64
    assert aggregate["conclusion"]["exact_pair_calibrated"] is True
    assert aggregate["conclusion"]["default_change_allowed"] is False


def test_local_coding_bootstrap_keeps_target_profile_and_no_external_quota() -> None:
    assert bootstrap_profile_ids("local_gemma4_ollama") == [
        "local_gemma4_ollama",
        "codex_subscription",
    ]
    assert model_workspace_name("gemma4:26b") == "gemma4_26b"


def test_diversity_aggregate_requires_two_exact_distinct_families() -> None:
    aggregates = [
        {
            "profile_id": "codex_subscription",
            "model": "gpt-5.6-terra",
            "case": case,
            "provider_version": "0.145.0",
            "matrix_complete": True,
            "samples_passed": 3,
            "conclusion": {"exact_pair_calibrated": True},
            "_source_receipt": f"{case}.json",
        }
        for case in ("cli_conversor", "config_redactor")
    ]

    aggregate = aggregate_diverse_family_reports(
        aggregates,
        model="gpt-5.6-terra",
        profile_id="codex_subscription",
    )

    assert aggregate["case_families"] == ["cli_conversor", "config_redactor"]
    assert aggregate["samples_total"] == 6
    assert aggregate["integrity"]["same_exact_pair"] is True
    assert aggregate["conclusion"]["case_diversity_passed"] is True


def test_diversity_aggregate_rejects_duplicate_family_or_identity() -> None:
    aggregates = [
        {
            "profile_id": "codex_subscription",
            "model": model,
            "case": "same_case",
            "provider_version": "0.145.0",
            "matrix_complete": True,
            "samples_passed": 3,
            "conclusion": {"exact_pair_calibrated": True},
            "_source_receipt": f"{model}.json",
        }
        for model in ("gpt-5.6-terra", "gpt-5.6-luna")
    ]

    aggregate = aggregate_diverse_family_reports(
        aggregates,
        model="gpt-5.6-terra",
        profile_id="codex_subscription",
    )

    assert aggregate["conclusion"]["exact_pair_calibrated"] is False
    assert aggregate["integrity"]["same_exact_pair"] is False
    assert aggregate["integrity"]["two_distinct_families"] is False


def test_single_model_aggregate_rejects_mixed_provider_versions() -> None:
    reports = [
        {
            "seed": seed,
            "case": "cli_conversor",
            "provider_version": "1.1.6" if seed < 3 else "1.1.5",
            "evaluation_contract": code_evaluation_contract(),
            "_source_receipt": f"sonnet-seed-{seed}.json",
            "arms": {"claude-sonnet-4-6": _arm(passed=9, seconds=20)},
        }
        for seed in (1, 2, 3)
    ]

    aggregate = aggregate_single_model_reports(
        reports,
        model="claude-sonnet-4-6",
        profile_id="antigravity_subscription",
    )

    assert aggregate["same_provider_version"] is False
    assert aggregate["conclusion"]["exact_pair_calibrated"] is False
