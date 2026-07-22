from pathlib import Path

from scripts.benchmark_codex_terra_test_designer import (
    MUTANTS,
    PRODUCTION,
    aggregate_reports,
    evaluate_mutation_suite,
)


def test_mutation_evaluator_kills_every_frozen_mutant(tmp_path: Path) -> None:
    (tmp_path / "pricing.py").write_text(PRODUCTION, encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    test_file = tests / "test_acceptance_pricing.py"
    test_file.write_text(
        '''import pytest
from pricing import quote

def test_happy_path_and_discount():
    assert quote(10, 3, 20) == 24

@pytest.mark.parametrize("args", [(-0.01, 1, 0), (1, 0, 0), (1, 1, 101)])
def test_invalid_inputs(args):
    with pytest.raises(ValueError):
        quote(*args)
''',
        encoding="utf-8",
    )

    result = evaluate_mutation_suite(tmp_path, test_file)

    assert result["baseline"]["exit_code"] == 0
    assert result["mutants_killed"] == result["mutants_total"] == len(MUTANTS)
    assert (tmp_path / "pricing.py").read_text(encoding="utf-8") == PRODUCTION


def test_aggregate_requires_three_passing_comparable_samples() -> None:
    reports = [
        {
            "model": "gpt-5.6-terra",
            "seed": seed,
            "ok": True,
            "seconds": 10 + seed,
            "checks": {"suite": True},
            "usage": {"input_tokens": 100},
        }
        for seed in (1, 2, 3)
    ]

    result = aggregate_reports(reports)

    assert result["samples_passed"] == 3
    assert result["checks_passed"] == result["checks_total"] == 3
    assert result["usage"]["input_tokens"] == 300
    assert result["conclusion"]["exact_pair_calibrated"] is True
