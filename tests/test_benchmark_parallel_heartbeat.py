from __future__ import annotations

from pathlib import Path

from scripts.benchmark_parallel_heartbeat import run_benchmark


def test_parallel_heartbeat_hermetic_ab_closes_correction_contract(tmp_path: Path) -> None:
    report = run_benchmark(workdir=tmp_path, delay_seconds=0.02)

    assert report["conclusion"] == {
        "correction_validated": True,
        "performance_claim_allowed": False,
        "live_contention_trigger_satisfied": False,
        "default_change_allowed": False,
        "decision": "retain_sequential_default",
        "reason": "A/B hermético de corrección; no representa latencia, cuota ni calidad de proveedores vivos",
    }
    assert all(report["checks"].values())
    assert report["arms"]["sequential"]["overlap_pairs"] == []
    assert len(report["arms"]["parallel"]["overlap_pairs"]) == 3
    assert report["arms"]["parallel"]["audit"]["evidence_quality"] == "exact"
    assert report["arms"]["parallel"]["run_status_by_issue"]["root-fail-d"] == "failed"


def test_parallel_heartbeat_default_temp_runtime_cleans_up() -> None:
    report = run_benchmark(delay_seconds=0.005)

    assert report["conclusion"]["correction_validated"] is True
