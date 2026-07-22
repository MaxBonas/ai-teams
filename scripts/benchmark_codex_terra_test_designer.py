"""Canario conductual de Terra para Test Designer mediante mutación oculta."""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers.workspace import _initialize_project_runtime  # noqa: E402
from aiteam.adapters.registry import build_default_registry  # noqa: E402
from aiteam.db.agents import create_agent  # noqa: E402
from aiteam.db.issues import create_issue  # noqa: E402
from aiteam.db.wakeups import enqueue_wakeup  # noqa: E402
from aiteam.heartbeat.executor import RunExecutor  # noqa: E402
from aiteam.heartbeat.scheduler import HeartbeatScheduler  # noqa: E402
from aiteam.project_adapters import project_profiles, write_project_adapter_policy  # noqa: E402
from aiteam.tools.catalog import default_capabilities_for_role  # noqa: E402


PRODUCTION = '''def quote(unit_price, quantity, discount_pct=0):
    """Return a two-decimal quote or reject invalid commercial inputs."""
    if unit_price < 0:
        raise ValueError("unit_price must be non-negative")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if not 0 <= discount_pct <= 100:
        raise ValueError("discount_pct must be between 0 and 100")
    subtotal = unit_price * quantity
    return round(subtotal * (1 - discount_pct / 100), 2)
'''

MUTANTS: dict[str, str] = {
    "allows_zero_quantity": PRODUCTION.replace("quantity <= 0", "quantity < 0"),
    "allows_negative_price": PRODUCTION.replace("unit_price < 0", "unit_price < -1"),
    "allows_discount_over_100": PRODUCTION.replace("discount_pct <= 100", "discount_pct <= 101"),
    "ignores_discount": PRODUCTION.replace(
        "return round(subtotal * (1 - discount_pct / 100), 2)",
        "return round(subtotal, 2)",
    ),
    "ignores_quantity": PRODUCTION.replace("subtotal = unit_price * quantity", "subtotal = unit_price"),
}


def _run_pytest(workspace: Path, test_file: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_file.relative_to(workspace))],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-3000:],
        "stderr": proc.stderr[-1000:],
    }


def evaluate_mutation_suite(workspace: Path, test_file: Path) -> dict[str, Any]:
    """Run the candidate suite against production and hidden single mutants."""
    if not test_file.is_file():
        return {"baseline": None, "mutants": {}, "mutants_killed": 0, "mutants_total": len(MUTANTS)}
    target = workspace / "pricing.py"
    baseline = _run_pytest(workspace, test_file)
    results: dict[str, Any] = {}
    try:
        for name, source in MUTANTS.items():
            target.write_text(source, encoding="utf-8")
            run = _run_pytest(workspace, test_file)
            results[name] = {"killed": run["exit_code"] != 0, "exit_code": run["exit_code"]}
    finally:
        target.write_text(PRODUCTION, encoding="utf-8")
    return {
        "baseline": baseline,
        "mutants": results,
        "mutants_killed": sum(bool(result["killed"]) for result in results.values()),
        "mutants_total": len(MUTANTS),
    }


def _report_for_run(db: Path, run_id: str) -> dict[str, Any] | None:
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM agent_reports WHERE run_id=? AND valid=1 AND is_assignee=1 "
            "ORDER BY rowid DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    return dict(row) if row else None


def run_canary(*, workspace: Path, seed: int, model: str = "gpt-5.6-terra") -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "pricing.py"
    target.write_text(PRODUCTION, encoding="utf-8")
    runtime = workspace / ".aiteam"
    runtime.mkdir(parents=True, exist_ok=True)
    write_project_adapter_policy(runtime, profile_ids=["codex_subscription"])
    _initialize_project_runtime(workspace, run_profile="solo_lead")
    db = runtime / "aiteam.db"
    profile = next(item for item in project_profiles(runtime) if item["id"] == "codex_subscription")
    agent_id = "role:benchmark_test_designer"
    issue_id = "issue:benchmark_test_designer"
    create_agent(
        db,
        agent_id=agent_id,
        role="test_designer",
        name="Benchmark Independent Test Designer",
        seniority="standard",
        adapter_type=str(profile["adapter_type"]),
        adapter_config={
            "profile_id": "codex_subscription",
            "cli_kind": "codex",
            "command": ["codex"],
            "model": model,
            "model_reasoning_effort": "medium",
            "sandbox": "workspace-write",
            "approval_policy": "never",
            "timeout_sec": 240,
        },
        capabilities=default_capabilities_for_role("test_designer"),
        supervisor_agent_id="role:lead",
        metadata={"source": "codex_terra_test_designer_canary", "seed": seed},
    )
    create_issue(
        db,
        issue_id=issue_id,
        goal_id="goal:intake",
        parent_id="issue:intake",
        title=f"Test Designer Terra seed {seed}",
        description=(
            "Diseña la suite de aceptación independiente para pricing.quote. Especificación: "
            "unit_price debe ser >= 0; quantity debe ser > 0; discount_pct debe estar entre "
            "0 y 100 inclusive; el total es unit_price * quantity * (1-discount/100), "
            "redondeado con round(..., 2). Escribe únicamente tests/test_acceptance_pricing.py. "
            "Cubre happy path y cada frontera/entrada inválida. No edites pricing.py ni otros "
            "archivos. No conoces los mutantes ocultos. Persiste un AGENT-REPORT válido dentro "
            "del add_comment final, notifica al supervisor y cierra done."
        ),
        status="todo",
        role="test_designer",
        complexity="medium",
        criticality="medium",
        assignee_agent_id=agent_id,
    )
    enqueue_wakeup(
        db,
        agent_id=agent_id,
        source="codex_terra_test_designer_canary",
        reason=f"design-seed-{seed}",
        payload={"issue_id": issue_id, "wake_reason": f"design-seed-{seed}"},
        idempotency_key=f"codex-terra-test-designer:{seed}",
    )
    dispatch = HeartbeatScheduler(db).dispatch_next(agent_id=agent_id)
    if dispatch is None:
        raise RuntimeError("Test Designer wakeup no despachable")
    run_id = str(dispatch.run["id"])
    started = time.monotonic()
    RunExecutor(db, build_default_registry()).execute(dispatch)
    elapsed = round(time.monotonic() - started, 3)
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        run = dict(conn.execute(
            "SELECT id,status,error_code,error,model,provider,channel,usage_json FROM runs WHERE id=?",
            (run_id,),
        ).fetchone())
        issue_status = str(conn.execute("SELECT status FROM issues WHERE id=?", (issue_id,)).fetchone()[0])
    report = _report_for_run(db, run_id)
    production_unchanged = target.read_text(encoding="utf-8") == PRODUCTION
    authored_files = sorted(
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_file() and ".aiteam" not in path.parts and path.name != "pricing.py"
    )
    test_file = workspace / "tests" / "test_acceptance_pricing.py"
    mutation = evaluate_mutation_suite(workspace, test_file)
    checks = {
        "run_completed": run["status"] == "completed",
        "production_unchanged": production_unchanged,
        "only_expected_test_authored": authored_files == ["tests/test_acceptance_pricing.py"],
        "baseline_passes": bool(mutation["baseline"]) and mutation["baseline"]["exit_code"] == 0,
        "all_hidden_mutants_killed": mutation["mutants_killed"] == mutation["mutants_total"],
        "durable_report_done": bool(report) and report.get("result") in {"done", "completed"},
        "issue_done": issue_status == "done",
        "production_restored_after_evaluation": target.read_text(encoding="utf-8") == PRODUCTION,
    }
    raw_usage = json.loads(str(run.get("usage_json") or "{}"))
    return {
        "schema_version": 1,
        "benchmark": "codex_terra_independent_test_designer",
        "profile_id": "codex_subscription",
        "model": model,
        "role": "test_designer",
        "seed": seed,
        "seconds": elapsed,
        "checks": checks,
        "mutation_evaluation": mutation,
        "authored_files": authored_files,
        "run": run,
        "report": report,
        "usage": raw_usage,
        "workspace": str(workspace),
        "ok": all(checks.values()),
    }


def aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    seconds = [float(report["seconds"]) for report in reports]
    usage: dict[str, int] = {}
    for report in reports:
        for key, value in report.get("usage", {}).items():
            if isinstance(value, (int, float)):
                usage[key] = usage.get(key, 0) + int(value)
    passed = sum(bool(report.get("ok")) for report in reports)
    checks_total = sum(len(report.get("checks", {})) for report in reports)
    checks_passed = sum(sum(bool(value) for value in report.get("checks", {}).values()) for report in reports)
    comparable = bool(reports) and len({report.get("model") for report in reports}) == 1
    return {
        "schema_version": 1,
        "benchmark": "codex_terra_independent_test_designer_aggregate",
        "profile_id": "codex_subscription",
        "model": reports[0].get("model") if reports else None,
        "role": "test_designer",
        "seeds": [report.get("seed") for report in reports],
        "matrix_complete": comparable and len(reports) == 3,
        "samples_passed": passed,
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        "wall_seconds_median": round(statistics.median(seconds), 3) if seconds else None,
        "wall_seconds_range": [round(min(seconds), 3), round(max(seconds), 3)] if seconds else [],
        "usage": {**usage, "marginal_cost_cents": 0, "note": "presión de cuota; no coste API"},
        "conclusion": {
            "exact_pair_calibrated": comparable and len(reports) == 3 and passed == 3,
            "default_change_allowed": False,
            "decision": "calibrate_exact_pair" if passed == 3 else "retain_requires_canary",
            "unmeasured_constructs": ["integración multi-módulo", "tests de browser", "property-based testing"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--aggregate-from", type=Path, nargs="+")
    args = parser.parse_args()
    if args.aggregate_from:
        report = aggregate_reports([json.loads(path.read_text(encoding="utf-8")) for path in args.aggregate_from])
        ok = bool(report["conclusion"]["exact_pair_calibrated"])
    else:
        if args.seed is None or args.workdir is None:
            parser.error("--seed and --workdir are required unless --aggregate-from is used")
        report = run_canary(workspace=args.workdir.resolve(), seed=args.seed, model=args.model)
        ok = bool(report["ok"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": ok, "seeds": report.get("seeds"), "checks": report.get("checks")}, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
