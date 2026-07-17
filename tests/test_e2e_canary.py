"""El canario e2e corre con la suite: si el flujo completo de orquestación
deja de converger (delegación → file_ops → dependencias → test_runner builtin
→ gate → cierre), esto falla en CI antes de tocar un proyecto real."""
from __future__ import annotations

from pathlib import Path

from scripts.e2e_canary import run_canary
from scripts.orchestrator_evals import evaluate_db


def test_e2e_canary_converges(tmp_path: Path) -> None:
    report = run_canary(tmp_path)

    assert report["ok"], f"canario roto: {report['checks']}"
    assert report["checks"]["gate_denied_before_runner_then_recovered"]
    assert report["checks"]["gate_left_corrective_comment"]
    assert report["info"]["quality_gate_denials"] >= 1
    assert report["info"]["run_order"][0] == "role:lead"
    assert report["info"]["run_order"].index("role:test_runner") > 0
    assert report["info"]["run_order"][-1] == "role:lead"
    assert report["ticks"] <= 3, "convergencia degradada: antes bastaba 1 tick"

    evals = evaluate_db(Path(report["db"]))
    assert evals["outcome"]["accepted_root_issues"] == 1
    assert evals["coordination"]["test_failure_reports"] == 0
    assert evals["coordination"]["approvals_contradicted_by_later_test_failure"] == 0
    assert evals["liveness"]["healthy"] is True
