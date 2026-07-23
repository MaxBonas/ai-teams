"""Canario conductual de Terra para Test Designer mediante mutación oculta."""
from __future__ import annotations

import argparse
import hashlib
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
from aiteam.user_config import DEFAULT_ADAPTER_PROFILES  # noqa: E402


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

STATE_MACHINE_PRODUCTION = '''def transition(job, event):
    """Return a new job after one valid lifecycle transition."""
    status = job.get("status")
    if status not in {"pending", "running", "succeeded", "failed"}:
        raise ValueError("unknown status")
    transitions = {
        ("pending", "start"): "running",
        ("running", "succeed"): "succeeded",
        ("running", "fail"): "failed",
    }
    key = (status, event)
    if key not in transitions:
        raise ValueError("invalid transition")
    updated = dict(job)
    updated["status"] = transitions[key]
    return updated
'''

STATE_MACHINE_MUTANTS: dict[str, str] = {
    "allows_pending_succeed": STATE_MACHINE_PRODUCTION.replace(
        '("pending", "start"): "running",',
        '("pending", "start"): "running",\n        ("pending", "succeed"): "succeeded",',
    ),
    "maps_failure_to_success": STATE_MACHINE_PRODUCTION.replace(
        '("running", "fail"): "failed",',
        '("running", "fail"): "succeeded",',
    ),
    "allows_terminal_restart": STATE_MACHINE_PRODUCTION.replace(
        '("running", "fail"): "failed",',
        '("running", "fail"): "failed",\n        ("failed", "start"): "running",',
    ),
    "ignores_unknown_event": STATE_MACHINE_PRODUCTION.replace(
        'if key not in transitions:\n        raise ValueError("invalid transition")',
        'if key not in transitions:\n        return dict(job)',
    ),
    "mutates_input": STATE_MACHINE_PRODUCTION.replace(
        "updated = dict(job)",
        "updated = job",
    ),
}
SUPPORTED_PROFILES = (
    "codex_subscription",
    "antigravity_subscription",
    "local_gemma4_ollama",
)
CONTRACT_VERSION = "independent_test_designer_mutation_v2"
TEST_DESIGNER_DIVERSITY_CONTRACT = "independent_test_designer_two_family_v3"
PRICING_FAMILY = "pricing_boundary_mutation"
STATE_MACHINE_FAMILY = "job_state_machine_mutation"
SUPPORTED_CASE_FAMILIES = (PRICING_FAMILY, STATE_MACHINE_FAMILY)


def adapter_config(profile_id: str, model: str) -> dict[str, Any]:
    if profile_id == "codex_subscription":
        return {
            "profile_id": profile_id,
            "cli_kind": "codex",
            "command": ["codex"],
            "model": model,
            "model_reasoning_effort": "medium",
            "sandbox": "workspace-write",
            "approval_policy": "never",
            "timeout_sec": 240,
        }
    if profile_id == "antigravity_subscription":
        return {
            "profile_id": profile_id,
            "cli_kind": "antigravity",
            "command": ["agy"],
            "model": model,
            "sandbox": "workspace-write",
            "timeout_sec": 240,
        }
    if profile_id == "local_gemma4_ollama":
        profile = next(
            item for item in DEFAULT_ADAPTER_PROFILES if item["id"] == profile_id
        )
        config = dict(profile["config"])
        config.update(
            {
                "profile_id": profile_id,
                "model": model,
                "model_reasoning_effort": "none",
                "sandbox": "workspace-write",
                "timeout_sec": 240,
            }
        )
        return config
    raise ValueError(f"unsupported benchmark profile: {profile_id}")


def bootstrap_profile_ids(profile_id: str) -> list[str]:
    if profile_id.startswith("local_"):
        return [profile_id, "codex_subscription"]
    return [profile_id]


def durable_authored_files(
    workspace: Path, production_filename: str = "pricing.py"
) -> list[str]:
    """Source artifacts only; interpreter caches are execution by-products."""
    return sorted(
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_file()
        and ".aiteam" not in path.parts
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
        and path.name != production_filename
    )


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


def evaluate_mutation_suite(
    workspace: Path,
    test_file: Path,
    *,
    production_filename: str = "pricing.py",
    production_source: str = PRODUCTION,
    mutants: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run the candidate suite against production and hidden single mutants."""
    mutation_set = MUTANTS if mutants is None else mutants
    if not test_file.is_file():
        return {
            "baseline": None,
            "mutants": {},
            "mutants_killed": 0,
            "mutants_total": len(mutation_set),
        }
    target = workspace / production_filename
    baseline = _run_pytest(workspace, test_file)
    results: dict[str, Any] = {}
    try:
        for name, source in mutation_set.items():
            target.write_text(source, encoding="utf-8")
            run = _run_pytest(workspace, test_file)
            results[name] = {"killed": run["exit_code"] != 0, "exit_code": run["exit_code"]}
    finally:
        target.write_text(production_source, encoding="utf-8")
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


def run_canary(
    *, workspace: Path, seed: int, model: str = "gpt-5.6-terra",
    profile_id: str = "codex_subscription",
    case_family: str = PRICING_FAMILY,
) -> dict[str, Any]:
    if case_family not in SUPPORTED_CASE_FAMILIES:
        raise ValueError(f"unsupported Test Designer family: {case_family}")
    is_state_machine = case_family == STATE_MACHINE_FAMILY
    production_filename = "job_state.py" if is_state_machine else "pricing.py"
    production_source = STATE_MACHINE_PRODUCTION if is_state_machine else PRODUCTION
    mutants = STATE_MACHINE_MUTANTS if is_state_machine else MUTANTS
    test_filename = (
        "tests/test_acceptance_job_state.py"
        if is_state_machine
        else "tests/test_acceptance_pricing.py"
    )
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / production_filename
    target.write_text(production_source, encoding="utf-8")
    runtime = workspace / ".aiteam"
    runtime.mkdir(parents=True, exist_ok=True)
    write_project_adapter_policy(
        runtime, profile_ids=bootstrap_profile_ids(profile_id)
    )
    _initialize_project_runtime(workspace, run_profile="solo_lead")
    db = runtime / "aiteam.db"
    profile = next(item for item in project_profiles(runtime) if item["id"] == profile_id)
    agent_id = "role:benchmark_test_designer"
    issue_id = "issue:benchmark_test_designer"
    create_agent(
        db,
        agent_id=agent_id,
        role="test_designer",
        name="Benchmark Independent Test Designer",
        seniority="standard",
        adapter_type=str(profile["adapter_type"]),
        adapter_config=adapter_config(profile_id, model),
        capabilities=default_capabilities_for_role("test_designer"),
        supervisor_agent_id="role:lead",
        metadata={"source": "codex_terra_test_designer_canary", "seed": seed},
    )
    create_issue(
        db,
        issue_id=issue_id,
        goal_id="goal:intake",
        parent_id="issue:intake",
        title=f"Test Designer {profile_id}/{model} seed {seed}",
        description=(
            (
                "Diseña la suite de aceptación independiente para job_state.transition. "
                "Estados: pending, running, succeeded y failed. Las únicas transiciones válidas "
                "son pending+start→running, running+succeed→succeeded y running+fail→failed. "
                "Estados terminales no aceptan eventos; estados/eventos/transiciones desconocidos "
                "lanzan ValueError. La función devuelve una copia y nunca muta el job recibido. "
                "Escribe únicamente tests/test_acceptance_job_state.py. "
            )
            if is_state_machine
            else (
                "Diseña la suite de aceptación independiente para pricing.quote. Especificación: "
                "unit_price debe ser >= 0; quantity debe ser > 0; discount_pct debe estar entre "
                "0 y 100 inclusive; el total es unit_price * quantity * (1-discount/100), "
                "redondeado con round(..., 2). Escribe únicamente "
                "tests/test_acceptance_pricing.py. "
            )
        )
        + (
            "Cubre happy path y cada frontera o transición inválida. No edites producción ni "
            "otros archivos. No conoces los mutantes ocultos. Persiste un AGENT-REPORT válido "
            "dentro del add_comment final, notifica al supervisor y cierra done."
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
    production_unchanged = target.read_text(encoding="utf-8") == production_source
    authored_files = durable_authored_files(workspace, production_filename)
    test_file = workspace / test_filename
    mutation = evaluate_mutation_suite(
        workspace,
        test_file,
        production_filename=production_filename,
        production_source=production_source,
        mutants=mutants,
    )
    checks = {
        "run_completed": run["status"] == "completed",
        "production_unchanged": production_unchanged,
        "only_expected_test_authored": authored_files == [test_filename],
        "baseline_passes": bool(mutation["baseline"]) and mutation["baseline"]["exit_code"] == 0,
        "all_hidden_mutants_killed": (
            bool(mutation["baseline"])
            and mutation["baseline"]["exit_code"] == 0
            and mutation["mutants_killed"] == mutation["mutants_total"]
        ),
        "durable_report_done": bool(report) and report.get("result") in {"done", "completed"},
        "issue_done": issue_status == "done",
        "production_restored_after_evaluation": (
            target.read_text(encoding="utf-8") == production_source
        ),
    }
    raw_usage = json.loads(str(run.get("usage_json") or "{}"))
    return {
        "schema_version": 1,
        "benchmark": "codex_terra_independent_test_designer",
        "profile_id": profile_id,
        "model": model,
        "contract_version": CONTRACT_VERSION,
        "case_family": case_family,
        "role": "test_designer",
        "seed": seed,
        "seconds": elapsed,
        "checks": checks,
        "mutation_evaluation": mutation,
        "authored_files": authored_files,
        "run": run,
        "report": report,
        "usage": raw_usage,
        "workspace": (
            workspace.resolve().relative_to(REPO_ROOT).as_posix()
            if workspace.resolve().is_relative_to(REPO_ROOT)
            else workspace.name
        ),
        "ok": all(checks.values()),
    }


def reevaluate_report(report: dict[str, Any]) -> dict[str, Any]:
    """Ignore runtime cache files without repeating provider inference."""
    updated = dict(report)
    authored_files = [
        str(path)
        for path in report.get("authored_files") or []
        if "__pycache__" not in Path(str(path)).parts
        and Path(str(path)).suffix != ".pyc"
    ]
    checks = dict(report.get("checks") or {})
    expected = (
        "tests/test_acceptance_job_state.py"
        if report.get("case_family") == STATE_MACHINE_FAMILY
        else "tests/test_acceptance_pricing.py"
    )
    checks["only_expected_test_authored"] = authored_files == [expected]
    updated["authored_files"] = authored_files
    updated["checks"] = checks
    updated["contract_version"] = CONTRACT_VERSION
    updated["ok"] = all(checks.values())
    updated["reevaluation"] = {
        "provider_rerun": False,
        "reason": "exclude_interpreter_cache_from_authored_source_surface",
    }
    return updated


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
    identities = {
        (
            report.get("profile_id"),
            report.get("model"),
            report.get("role"),
            report.get("contract_version"),
            report.get("case_family", PRICING_FAMILY),
        )
        for report in reports
    }
    comparable = bool(reports) and len(identities) == 1
    matrix_complete = (
        comparable
        and len(reports) == 3
        and {report.get("seed") for report in reports} == {1, 2, 3}
    )
    manifest = sorted(
        [
            {
                "receipt": report.get("_source_receipt"),
                "seed": report.get("seed"),
                "ok": report.get("ok") is True,
                "evidence_sha256": hashlib.sha256(
                    json.dumps(
                        {
                            "checks": report.get("checks"),
                            "mutation_evaluation": report.get("mutation_evaluation"),
                            "authored_files": report.get("authored_files"),
                            "report": report.get("report"),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            }
            for report in reports
        ],
        key=lambda row: int(row["seed"] or 0),
    )
    source_receipts = [str(row["receipt"]) for row in manifest if row["receipt"]]
    sources_bound = len(source_receipts) == 3 and len(set(source_receipts)) == 3
    calibrated = matrix_complete and sources_bound and passed == 3
    usage_observed = any(
        key.endswith("_tokens") and value
        for key, value in usage.items()
    )
    return {
        "schema_version": 1,
        "benchmark": "codex_terra_independent_test_designer_aggregate",
        "profile_id": reports[0].get("profile_id") if reports else None,
        "model": reports[0].get("model") if reports else None,
        "role": "test_designer",
        "contract_version": reports[0].get("contract_version") if reports else None,
        "case_family": (
            reports[0].get("case_family", PRICING_FAMILY) if reports else None
        ),
        "seeds": [report.get("seed") for report in reports],
        "matrix_complete": matrix_complete,
        "samples_passed": passed,
        "source_receipts": source_receipts,
        "sample_manifest": manifest,
        "integrity": {"sources_bound": sources_bound, "evidence_hashed": True},
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        "wall_seconds_median": round(statistics.median(seconds), 3) if seconds else None,
        "wall_seconds_range": [round(min(seconds), 3), round(max(seconds), 3)] if seconds else [],
        "usage": {
            **usage,
            "marginal_cost_cents": 0,
            "telemetry_status": "observed" if usage_observed else "unknown",
            "note": (
                "modelo local: sin coste API ni presión de cuota externa"
                if str(reports[0].get("profile_id") if reports else "").startswith(
                    "local_"
                )
                else "presión de cuota de suscripción; no coste API"
                if usage_observed
                else "Antigravity no expone usage headless comparable"
            ),
        },
        "conclusion": {
            "exact_pair_calibrated": calibrated,
            "default_change_allowed": False,
            "decision": "calibrate_exact_pair" if calibrated else "retain_requires_canary",
            "unmeasured_constructs": ["integración multi-módulo", "tests de browser", "property-based testing"],
        },
    }


def aggregate_diverse_family_reports(
    aggregates: list[dict[str, Any]], *, model: str, profile_id: str
) -> dict[str, Any]:
    """Une dos familias de mutación exactas y enlaza su procedencia."""
    families = sorted(
        {
            str(report.get("case_family") or PRICING_FAMILY)
            for report in aggregates
        }
    )
    identities = {
        (str(report.get("profile_id") or ""), str(report.get("model") or ""))
        for report in aggregates
    }
    sources = [str(report.get("_source_receipt") or "") for report in aggregates]
    hashes = [
        hashlib.sha256(
            json.dumps(
                {key: value for key, value in report.items() if key != "_source_receipt"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        for report in aggregates
    ]
    family_checks = [
        {
            "case_family": str(
                report.get("case_family") or PRICING_FAMILY
            ),
            "matrix_complete": report.get("matrix_complete") is True,
            "samples_passed": int(report.get("samples_passed") or 0),
            "exact_pair_calibrated": (
                report.get("conclusion", {}).get("exact_pair_calibrated") is True
            ),
            "source_receipt": report.get("_source_receipt"),
        }
        for report in aggregates
    ]
    calibrated = bool(
        len(aggregates) == 2
        and len(families) == 2
        and identities == {(profile_id, model)}
        and len(sources) == 2
        and all(sources)
        and len(set(sources)) == 2
        and all(
            row["matrix_complete"]
            and row["samples_passed"] == 3
            and row["exact_pair_calibrated"]
            for row in family_checks
        )
    )
    return {
        "schema_version": 1,
        "benchmark": "test_designer_behavioral_diversity_aggregate",
        "profile_id": profile_id,
        "model": model,
        "role": "test_designer",
        "contract_version": TEST_DESIGNER_DIVERSITY_CONTRACT,
        "case_families": families,
        "case_family_count": len(families),
        "seeds_per_family": 3,
        "samples_total": sum(
            int(report.get("samples_passed") or 0) for report in aggregates
        ),
        "family_checks": family_checks,
        "source_receipts": sources,
        "source_sha256": hashes,
        "integrity": {
            "same_exact_pair": identities == {(profile_id, model)},
            "two_distinct_families": len(families) == 2,
            "sources_bound": len(sources) == 2 and all(sources),
            "sources_hashed": len(hashes) == 2,
        },
        "conclusion": {
            "exact_pair_calibrated": calibrated,
            "case_diversity_passed": calibrated,
            "default_change_allowed": False,
            "decision": (
                "calibrate_two_family_exact_pair"
                if calibrated
                else "insufficient_diversity_evidence"
            ),
            "goodhart_risk": "moderate" if calibrated else "material",
            "unmeasured_constructs": [
                "integración multi-módulo",
                "tests de browser",
                "property-based testing",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--profile", choices=SUPPORTED_PROFILES, default="codex_subscription")
    parser.add_argument("--case-family", choices=SUPPORTED_CASE_FAMILIES, default=PRICING_FAMILY)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--aggregate-from", type=Path, nargs="+")
    parser.add_argument("--family-aggregate-input", type=Path, action="append")
    parser.add_argument("--reevaluate-from", type=Path)
    args = parser.parse_args()
    if args.family_aggregate_input:
        source_reports = []
        for path in args.family_aggregate_input:
            source = json.loads(path.read_text(encoding="utf-8"))
            source["_source_receipt"] = path.as_posix()
            source_reports.append(source)
        report = aggregate_diverse_family_reports(
            source_reports, model=args.model, profile_id=args.profile
        )
        ok = bool(report["conclusion"]["exact_pair_calibrated"])
    elif args.aggregate_from:
        source_reports = []
        for path in args.aggregate_from:
            source = json.loads(path.read_text(encoding="utf-8"))
            try:
                source["_source_receipt"] = path.resolve().relative_to(REPO_ROOT).as_posix()
            except ValueError:
                source["_source_receipt"] = path.resolve().as_posix()
            source_reports.append(source)
        report = aggregate_reports(source_reports)
        ok = bool(report["conclusion"]["exact_pair_calibrated"])
    elif args.reevaluate_from:
        report = reevaluate_report(
            json.loads(args.reevaluate_from.read_text(encoding="utf-8"))
        )
        ok = bool(report["ok"])
    else:
        if args.seed is None or args.workdir is None:
            parser.error("--seed and --workdir are required unless --aggregate-from is used")
        report = run_canary(
            workspace=args.workdir.resolve(), seed=args.seed, model=args.model,
            profile_id=args.profile, case_family=args.case_family,
        )
        ok = bool(report["ok"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": ok,
                "seeds": report.get("seeds"),
                "case_families": report.get("case_families"),
                "checks": report.get("checks"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
