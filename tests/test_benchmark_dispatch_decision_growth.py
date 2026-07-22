from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.benchmark_dispatch_decision_growth import (
    expected_decision_rows,
    run_benchmark,
)


@pytest.mark.parametrize(
    ("queue_size", "expected"),
    ((0, 0), (1, 1), (25, 325), (30, 450), (100, 2200), (1000, 24700)),
)
def test_expected_decision_rows_respects_snapshot_cap(
    queue_size: int, expected: int
) -> None:
    assert expected_decision_rows(queue_size) == expected


def test_dispatch_growth_benchmark_preserves_exact_provenance(tmp_path: Path) -> None:
    report = run_benchmark(
        workdir=tmp_path,
        queue_sizes=(1, 5, 30),
        repeats=1,
    )

    assert report["contract"]["models_or_network_used"] is False
    assert report["contract"]["project_databases_read"] is False
    assert report["conclusion"]["structural_amplification_bounded"] is True
    assert all(case["checks"]["row_formula_exact"] for case in report["cases"])
    assert report["cases"][-1]["decision_rows"] == 450
    assert report["cases"][-1]["max_observations_per_wakeup"] == 25


def test_dispatch_growth_benchmark_validates_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive integers"):
        run_benchmark(workdir=tmp_path, queue_sizes=(0,), repeats=1)
    with pytest.raises(ValueError, match="repeats"):
        run_benchmark(workdir=tmp_path, queue_sizes=(1,), repeats=0)


def test_dispatch_growth_default_temp_runtime_cleans_up() -> None:
    report = run_benchmark(queue_sizes=(1, 5), repeats=1)

    assert report["conclusion"]["structural_amplification_bounded"] is True


def test_versioned_dispatch_growth_receipt_matches_contract() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "results"
        / "dispatch_decision_growth"
        / "dispatch-decision-growth-v1.json"
    )
    report = json.loads(path.read_text(encoding="utf-8"))

    assert report["case_version"] == "dispatch-decision-growth-v1"
    assert report["contract"]["queue_sizes"] == [1, 25, 100, 1000]
    assert report["contract"]["repeats"] == 3
    assert all(report["checks"].values())
    assert report["conclusion"] == {
        "structural_amplification_bounded": True,
        "operational_pressure_observed": False,
        "retention_implementation_allowed": False,
        "decision": "retain_additive_log_and_monitor",
        "reason": "la amplificación exacta permanece dentro de los thresholds preregistrados",
    }
