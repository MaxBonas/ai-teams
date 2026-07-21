r"""A/B conductual de modelos coding servidos por Antigravity.

Cada brazo usa un Engineer real de AI Teams, el adapter subscription CLI y el
camino durable scheduler -> executor. Ambos reciben el mismo goal y workspace
inicial; una suite oculta y Ruff evalúan después los archivos materializados.

Uso (consume cuota real de Antigravity):
    .\scripts\python_local.bat scripts\benchmark_antigravity_coding_models.py \
      --case benchmarks/cli_conversor --seed 1 --workdir runtime/bench/agy-code-s1 \
      --output benchmarks/results/model_calibration/antigravity-coding-seed-1.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers.workspace import _initialize_project_runtime  # noqa: E402
from aiteam.adapters.registry import build_default_registry  # noqa: E402
from aiteam.db.agents import create_agent  # noqa: E402
from aiteam.db.issues import create_issue  # noqa: E402
from aiteam.db.wakeups import enqueue_wakeup  # noqa: E402
from aiteam.heartbeat.executor import RunExecutor  # noqa: E402
from aiteam.heartbeat.scheduler import HeartbeatScheduler  # noqa: E402
from aiteam.project_adapters import (  # noqa: E402
    choose_adapter_for_role,
    project_profiles,
    write_project_adapter_policy,
)
from aiteam.tools.catalog import default_capabilities_for_role  # noqa: E402
from scripts.benchmark_integrity import audit_ab_series, code_evaluation_contract  # noqa: E402
from scripts.benchmark_vs_codex import score_workspace  # noqa: E402


PROFILE_ID = "antigravity_subscription"
BASELINE_MODEL = "gemini-3.5-flash-high"
CHALLENGER_MODEL = "claude-sonnet-4-6"


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(resolved)


def run_arm(
    *, workspace: Path, goal: str, hidden_dir: Path, model: str, max_attempts: int = 2,
) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = workspace / ".aiteam"
    runtime.mkdir(parents=True, exist_ok=True)
    write_project_adapter_policy(runtime, profile_ids=[PROFILE_ID])
    _initialize_project_runtime(workspace, run_profile="solo_lead")
    db = runtime / "aiteam.db"
    profiles = project_profiles(runtime)
    selection = choose_adapter_for_role("engineer", "standard", profiles)
    if not selection or selection.get("adapter_profile_id") != PROFILE_ID:
        raise RuntimeError("Antigravity profile is not selectable for engineer")
    adapter_config = dict(selection.get("adapter_config") or {})
    adapter_config["model"] = model
    create_agent(
        db,
        agent_id="role:benchmark_engineer",
        role="engineer",
        name="Benchmark Engineer",
        seniority="standard",
        adapter_type=str(selection["adapter_type"]),
        adapter_config=adapter_config,
        capabilities=default_capabilities_for_role("engineer"),
        supervisor_agent_id="role:lead",
        metadata={"adapter_profile_id": PROFILE_ID, "source": "antigravity_coding_benchmark"},
    )
    create_issue(
        db,
        issue_id="issue:benchmark_code",
        goal_id="goal:intake",
        parent_id="issue:intake",
        title="Implementar benchmark de código",
        description=(
            goal.strip()
            + "\n\nContrato del benchmark: trabaja solo mediante ops estructurados. "
            "Materializa todos los entregables con write_file. No delegues y no preguntes al usuario. "
            "No conoces la suite oculta; un evaluador determinista la ejecutará al terminar."
        ),
        status="todo",
        role="engineer",
        complexity="medium",
        assignee_agent_id="role:benchmark_engineer",
    )
    enqueue_wakeup(
        db,
        agent_id="role:benchmark_engineer",
        source="model_calibration",
        reason="delegated_work",
        payload={"issue_id": "issue:benchmark_code", "wake_reason": "delegated_work"},
        idempotency_key=f"antigravity-coding:{model}:initial",
    )

    scheduler = HeartbeatScheduler(db)
    executor = RunExecutor(db, build_default_registry())
    started = time.monotonic()
    attempts = 0
    while attempts < max_attempts:
        dispatch = scheduler.dispatch_next(agent_id="role:benchmark_engineer")
        if dispatch is None:
            break
        attempts += 1
        executor.execute(dispatch)
        with sqlite3.connect(db) as conn:
            status = str(conn.execute(
                "SELECT status FROM issues WHERE id='issue:benchmark_code'"
            ).fetchone()[0])
        if status in {"done", "cancelled"}:
            break
    wall_seconds = round(time.monotonic() - started, 3)

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        issue_status = str(conn.execute(
            "SELECT status FROM issues WHERE id='issue:benchmark_code'"
        ).fetchone()[0])
        runs = [dict(row) for row in conn.execute(
            "SELECT id,status,error_code,liveness_state FROM runs "
            "WHERE issue_id='issue:benchmark_code' ORDER BY created_at,rowid"
        ).fetchall()]
        usage = conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),"
            "COALESCE(SUM(cost_cents),0) FROM cost_events c JOIN runs r ON r.id=c.run_id "
            "WHERE r.issue_id='issue:benchmark_code'"
        ).fetchone()
    score = score_workspace(workspace, hidden_dir)
    return {
        "model": model,
        "profile_id": PROFILE_ID,
        "provider": "google-antigravity",
        "channel": "subscription",
        "issue_status": issue_status,
        "attempts": attempts,
        "runs": runs,
        "wall_seconds": wall_seconds,
        "input_tokens": int(usage[0]),
        "output_tokens": int(usage[1]),
        "cost_cents": int(usage[2]),
        "usage_available": bool(int(usage[0]) or int(usage[1])),
        "score": score,
        "workspace": _portable_path(workspace),
    }


def aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    seeds = [int(report["seed"]) for report in reports]
    models = (BASELINE_MODEL, CHALLENGER_MODEL)
    integrity = audit_ab_series(reports, required_arms=models, min_seeds=3)
    summaries: dict[str, dict[str, Any]] = {}
    for model in models:
        arms = [report.get("arms", {}).get(model) for report in reports]
        if any(not isinstance(arm, dict) for arm in arms):
            continue
        completed = [arm for arm in arms if isinstance(arm, dict)]
        hidden_totals = {int(arm["score"].get("hidden_total") or 0) for arm in completed}
        summaries[model] = {
            "samples": len(completed),
            "done": sum(arm["issue_status"] == "done" for arm in completed),
            "hidden_passed": [int(arm["score"].get("hidden_passed") or 0) for arm in completed],
            "hidden_total": next(iter(hidden_totals), 0),
            "ruff_issues": [arm["score"].get("ruff_issues") for arm in completed],
            "attempts": [int(arm["attempts"]) for arm in completed],
            "wall_seconds_median": round(statistics.median(float(arm["wall_seconds"]) for arm in completed), 3),
            "usage_available": all(bool(arm["usage_available"]) for arm in completed),
        }
    baseline = summaries.get(BASELINE_MODEL)
    challenger = summaries.get(CHALLENGER_MODEL)
    conclusion_allowed = bool(integrity["conclusion_allowed"] and baseline and challenger)
    disposition = "insufficient_evidence"
    if conclusion_allowed:
        deltas = [
            challenger["hidden_passed"][idx] - baseline["hidden_passed"][idx]
            for idx in range(3)
        ]
        challenger_ruff = sum(int(value or 0) for value in challenger["ruff_issues"])
        baseline_ruff = sum(int(value or 0) for value in baseline["ruff_issues"])
        no_behavioral_regression = all(delta >= 0 for delta in deltas)
        no_convergence_regression = challenger["done"] >= baseline["done"]
        no_static_regression = challenger_ruff <= baseline_ruff
        strict_improvement = (
            any(delta > 0 for delta in deltas)
            or challenger["done"] > baseline["done"]
            or challenger_ruff < baseline_ruff
            or challenger["wall_seconds_median"] < baseline["wall_seconds_median"]
        )
        if (
            no_behavioral_regression
            and no_convergence_regression
            and no_static_regression
            and strict_improvement
            and challenger["done"] == 3
        ):
            disposition = "promote_challenger"
        else:
            disposition = "retain_baseline"
    return {
        "schema_version": 1,
        "benchmark": "antigravity_coding_behavioral_calibration",
        "case": reports[0].get("case") if reports else None,
        "seeds": sorted(seeds),
        "models": summaries,
        "integrity": integrity,
        "conclusion": {
            "allowed": conclusion_allowed,
            "disposition": disposition,
            "default_change_allowed": conclusion_allowed and disposition == "promote_challenger",
            "economic_comparison_available": bool(
                baseline and challenger and baseline["usage_available"] and challenger["usage_available"]
            ),
            "quota_note": "sin usage headless comparable; runs y segundos son presión de cuota, no coste API",
            "goodhart_risk": "residual_hidden_suite_overfit",
        },
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=REPO_ROOT / "benchmarks" / "cli_conversor")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input", action="append", type=Path, dest="inputs")
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args()

    output = args.output.resolve()
    if args.inputs:
        aggregate = aggregate_reports([json.loads(path.read_text(encoding="utf-8")) for path in args.inputs])
        _write(output, aggregate)
        print(json.dumps(aggregate["conclusion"], indent=2, ensure_ascii=False))
        return 0 if aggregate["integrity"]["conclusion_allowed"] else 2
    if args.seed is None or args.workdir is None:
        parser.error("--seed and --workdir are required unless --input is used")

    case = args.case.resolve()
    goal = (case / "goal.md").read_text(encoding="utf-8")
    hidden = case / "hidden_tests"
    workdir = args.workdir.resolve()
    report: dict[str, Any] = {
        "schema_version": 1,
        "benchmark": "antigravity_coding_behavioral_calibration",
        "case": case.name,
        "seed": args.seed,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "evaluation_contract": code_evaluation_contract(),
        "config": {"max_attempts": max(1, args.max_attempts), "stateless": True},
        "arms": {},
    }
    for model in (BASELINE_MODEL, CHALLENGER_MODEL):
        arm_workspace = workdir / model
        if arm_workspace.exists():
            raise RuntimeError(f"workspace already exists: {arm_workspace}")
        report["arms"][model] = run_arm(
            workspace=arm_workspace,
            goal=goal,
            hidden_dir=hidden,
            model=model,
            max_attempts=max(1, args.max_attempts),
        )
        _write(output, report)
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _write(output, report)
    print(json.dumps({model: report["arms"][model]["score"] for model in report["arms"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
