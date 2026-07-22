"""Canario conductual de Terra para el contrato QA adversarial condicional."""
from __future__ import annotations

import argparse
import json
import re
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
from aiteam.db.comments import list_comments  # noqa: E402
from aiteam.db.issues import create_issue, update_issue  # noqa: E402
from aiteam.db.wakeups import enqueue_wakeup  # noqa: E402
from aiteam.heartbeat.executor import RunExecutor  # noqa: E402
from aiteam.heartbeat.scheduler import HeartbeatScheduler  # noqa: E402
from aiteam.project_adapters import project_profiles, write_project_adapter_policy  # noqa: E402
from aiteam.tools.catalog import default_capabilities_for_role  # noqa: E402


BROKEN = '''def can_access(actor, resource):
    """Return whether actor may read resource."""
    return True
'''

FIXED = '''def can_access(actor, resource):
    """Enforce active actor, tenant boundary and private-resource role."""
    if not actor.get("active", False):
        return False
    if actor.get("tenant_id") != resource.get("tenant_id"):
        return False
    if resource.get("private", False) and actor.get("role") != "admin":
        return False
    return True
'''

# Model-role calibration must not measure the separate human-approval gate.
QA_CANARY_CRITICALITY = "medium"


def evaluate_adversarial_test(text: str) -> dict[str, Any]:
    anchors = {
        "imports_target": bool(re.search(r"(?:from\s+auth\s+import\s+can_access|auth\.can_access)", text)),
        "cross_tenant": "tenant-a" in text.lower() and "tenant-b" in text.lower(),
        "inactive_actor": bool(re.search(r"active\s*['\"]?\s*:\s*False|['\"]active['\"]\s*:\s*False", text)),
        "private_non_admin": "private" in text.lower() and "member" in text.lower(),
        "negative_assertion": bool(re.search(r"assert\s+(?:not\s+can_access|can_access\([^\n]+\)\s+is\s+False)", text)),
    }
    return {
        "anchors": anchors,
        "anchors_retained": sum(anchors.values()),
        "anchors_total": len(anchors),
        "contract_passed": all(anchors.values()),
    }


def _run_pytest(workspace: Path, files: list[Path]) -> dict[str, Any]:
    if not files:
        return {"executed": False, "exit_code": None, "stdout": "", "stderr": ""}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *[str(path.relative_to(workspace)) for path in files]],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return {
        "executed": True,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-2000:],
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


def _dispatch_run_id(dispatch: Any) -> str:
    """Return the durable run id exposed by HeartbeatScheduler.DispatchResult."""
    return str(dispatch.run["id"])


def _run_phase(db: Path, *, agent_id: str, issue_id: str, phase: str) -> dict[str, Any]:
    enqueue_wakeup(
        db,
        agent_id=agent_id,
        source="codex_terra_qa_canary",
        reason=phase,
        payload={"issue_id": issue_id, "wake_reason": phase},
        idempotency_key=f"codex-terra-qa:{issue_id}:{phase}",
    )
    dispatch = HeartbeatScheduler(db).dispatch_next(agent_id=agent_id)
    if dispatch is None:
        raise RuntimeError(f"QA wakeup no despachable: {phase}")
    run_id = _dispatch_run_id(dispatch)
    started = time.monotonic()
    RunExecutor(db, build_default_registry()).execute(dispatch)
    elapsed = round(time.monotonic() - started, 3)
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        run = dict(conn.execute(
            "SELECT id,status,error_code,error,model,provider,channel,usage_json "
            "FROM runs WHERE id=?",
            (run_id,),
        ).fetchone())
        status = str(conn.execute("SELECT status FROM issues WHERE id=?", (issue_id,)).fetchone()[0])
    comments = [
        comment for comment in list_comments(db, issue_id=issue_id)
        if str(comment.get("source_run_id") or "") == run_id
    ]
    return {
        "seconds": elapsed,
        "issue_status": status,
        "run": run,
        "report": _report_for_run(db, run_id),
        "comments": comments,
    }


def run_canary(*, workspace: Path, seed: int, model: str = "gpt-5.6-terra") -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "auth.py").write_text(BROKEN, encoding="utf-8")
    runtime = workspace / ".aiteam"
    runtime.mkdir(parents=True, exist_ok=True)
    write_project_adapter_policy(runtime, profile_ids=["codex_subscription"])
    _initialize_project_runtime(workspace, run_profile="solo_lead")
    db = runtime / "aiteam.db"
    profile = next(item for item in project_profiles(runtime) if item["id"] == "codex_subscription")
    agent_id = "role:benchmark_qa"
    issue_id = "issue:benchmark_qa"
    create_agent(
        db,
        agent_id=agent_id,
        role="qa",
        name="Benchmark Adversarial QA",
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
        capabilities=default_capabilities_for_role("qa"),
        supervisor_agent_id="role:lead",
        metadata={"source": "codex_terra_qa_canary", "seed": seed},
    )
    create_issue(
        db,
        issue_id=issue_id,
        goal_id="goal:intake",
        parent_id="issue:intake",
        title=f"QA adversarial Terra seed {seed}",
        description=(
            "Especificación: can_access solo permite actor activo del mismo tenant; un recurso "
            "private exige role admin. Intenta romper auth.py. Escribe únicamente "
            "tests/test_adversarial_auth.py con casos que FALLEN ahora: cruce tenant-a/tenant-b, "
            "actor inactive y private con role member. Ejecuta esos tests. No edites auth.py ni "
            "otros archivos. Si demuestran defecto, reporta changes_requested pero cierra tu issue "
            "done para que el Engineer corrija. Tras la corrección, elimina tests adversariales que "
            "ya pasen y reporta approved con evidencia de los límites atacados. Siempre usa un "
            "AGENT-REPORT válido y notify_supervisor."
        ),
        status="todo",
        role="qa",
        complexity="medium",
        criticality=QA_CANARY_CRITICALITY,
        assignee_agent_id=agent_id,
    )

    phase1 = _run_phase(db, agent_id=agent_id, issue_id=issue_id, phase=f"attack-seed-{seed}")
    production_unchanged = (workspace / "auth.py").read_text(encoding="utf-8") == BROKEN
    tests_after_attack = sorted(workspace.glob("tests/test_adversarial_*.py"))
    test_text = "\n".join(path.read_text(encoding="utf-8") for path in tests_after_attack)
    attack_evaluation = evaluate_adversarial_test(test_text)
    failing_run = _run_pytest(workspace, tests_after_attack)

    (workspace / "auth.py").write_text(FIXED, encoding="utf-8")
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE issues SET status='done' WHERE json_extract(metadata_json,'$.source')="
            "'reviewer_changes_requested_fix'"
        )
        conn.commit()
    update_issue(db, issue_id=issue_id, status="todo")
    phase2 = _run_phase(db, agent_id=agent_id, issue_id=issue_id, phase=f"verify-fix-seed-{seed}")
    tests_after_fix = sorted(workspace.glob("tests/test_adversarial_*.py"))
    fixed_production_intact = (workspace / "auth.py").read_text(encoding="utf-8") == FIXED

    first_report = phase1["report"] or {}
    second_report = phase2["report"] or {}
    checks = {
        "attack_run_completed": phase1["run"]["status"] == "completed",
        "attack_report_changes_requested": first_report.get("result") == "changes_requested",
        "production_unchanged_during_attack": production_unchanged,
        "adversarial_test_contract": attack_evaluation["contract_passed"],
        "adversarial_tests_fail_before_fix": failing_run["executed"] and failing_run["exit_code"] != 0,
        "verification_run_completed": phase2["run"]["status"] == "completed",
        "verification_report_approved": second_report.get("result") == "approved",
        "verification_issue_done": phase2["issue_status"] == "done",
        "passing_adversarial_tests_removed": not tests_after_fix,
        "fixed_production_intact": fixed_production_intact,
    }
    usage: dict[str, int] = {}
    for phase in (phase1, phase2):
        raw = json.loads(str(phase["run"].get("usage_json") or "{}"))
        for key, value in raw.items():
            if isinstance(value, (int, float)):
                usage[key] = usage.get(key, 0) + int(value)
    return {
        "schema_version": 1,
        "benchmark": "codex_terra_adversarial_qa",
        "profile_id": "codex_subscription",
        "model": model,
        "role": "qa",
        "seed": seed,
        "checks": checks,
        "attack_evaluation": attack_evaluation,
        "failing_test_run": failing_run,
        "phases": {"attack": phase1, "verify_fix": phase2},
        "usage": usage,
        "workspace": str(workspace),
        "ok": all(checks.values()),
    }


def aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate comparable post-contract-fix QA seeds."""
    seconds = [
        float(report["phases"]["attack"]["seconds"])
        + float(report["phases"]["verify_fix"]["seconds"])
        for report in reports
    ]
    usage: dict[str, int] = {}
    for report in reports:
        for key, value in report.get("usage", {}).items():
            if isinstance(value, (int, float)):
                usage[key] = usage.get(key, 0) + int(value)
    passed = sum(bool(report.get("ok")) for report in reports)
    check_count = sum(len(report.get("checks", {})) for report in reports)
    checks_passed = sum(
        sum(bool(value) for value in report.get("checks", {}).values())
        for report in reports
    )
    comparable = bool(reports) and len({report.get("model") for report in reports}) == 1
    return {
        "schema_version": 1,
        "benchmark": "codex_terra_adversarial_qa_aggregate",
        "profile_id": "codex_subscription",
        "model": reports[0].get("model") if reports else None,
        "role": "qa",
        "seeds": [report.get("seed") for report in reports],
        "matrix_complete": comparable and len(reports) == 3,
        "samples_passed": passed,
        "checks_passed": checks_passed,
        "checks_total": check_count,
        "wall_seconds_median": round(statistics.median(seconds), 3) if seconds else None,
        "wall_seconds_range": [round(min(seconds), 3), round(max(seconds), 3)] if seconds else [],
        "usage": {
            **usage,
            "marginal_cost_cents": 0,
            "note": "presión de cuota de suscripción; no coste API",
        },
        "conclusion": {
            "exact_pair_calibrated": comparable and len(reports) == 3 and passed == 3,
            "default_change_allowed": False,
            "decision": "calibrate_exact_pair" if passed == 3 else "retain_requires_canary",
            "unmeasured_constructs": [
                "browser QA",
                "suites multiarchivo",
                "recovery de entorno de tests roto",
            ],
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
        report = aggregate_reports([
            json.loads(path.read_text(encoding="utf-8")) for path in args.aggregate_from
        ])
    else:
        if args.seed is None or args.workdir is None:
            parser.error("--seed and --workdir are required unless --aggregate-from is used")
        report = run_canary(
            workspace=args.workdir.resolve(), seed=args.seed, model=args.model
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.aggregate_from:
        print(json.dumps({"seeds": report["seeds"], "conclusion": report["conclusion"]}, ensure_ascii=False))
        return 0 if report["conclusion"]["exact_pair_calibrated"] else 2
    print(json.dumps({"seed": args.seed, "checks": report["checks"]}, ensure_ascii=False))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
