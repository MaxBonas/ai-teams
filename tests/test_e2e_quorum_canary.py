from __future__ import annotations

from pathlib import Path

from scripts.e2e_quorum_canary import run_canary
from scripts.orchestrator_evals import evaluate_db


def test_quorum_canary_proves_gate_synthesis_planning_completion_and_liveness(tmp_path) -> None:
    report = run_canary(tmp_path)
    assert report["ok"] is True
    assert all(report["checks"].values())

    evals = evaluate_db(Path(report["db"]))
    assert evals["quorum"]["sessions_by_status"] == {"accepted": 1}
    assert evals["quorum"]["valid_contributions"] == 2
    assert evals["quorum"]["invalid_contributions"] == 0
    assert evals["quorum"]["accepted_without_provider_diversity"] == 0
    assert evals["quorum"]["accepted_with_unresolved_findings"] == 0
    assert evals["quorum"]["healthy"] is True
