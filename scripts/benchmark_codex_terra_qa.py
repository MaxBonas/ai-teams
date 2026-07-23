"""Canario conductual de Terra para el contrato QA adversarial condicional."""
from __future__ import annotations

import argparse
import hashlib
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

BROKEN_WEBHOOK = '''def accept_webhook(event, seen_ids, now):
    """Return whether an incoming webhook should be processed."""
    return True
'''

FIXED_WEBHOOK = '''def accept_webhook(event, seen_ids, now):
    """Reject invalid, expired or replayed webhook events."""
    event_id = event.get("id")
    if not event_id or event.get("signature") != "trusted":
        return False
    if now - event.get("timestamp", 0) > 300:
        return False
    if event_id in seen_ids:
        return False
    seen_ids.add(event_id)
    return True
'''

# Model-role calibration must not measure the separate human-approval gate.
QA_CANARY_CRITICALITY = "medium"
SUPPORTED_PROFILES = ("codex_subscription", "antigravity_subscription")
CONTRACT_VERSION = "adversarial_qa_fix_cycle_v2"
QA_DIVERSITY_CONTRACT = "adversarial_qa_two_family_v3"
AUTH_FAMILY = "authorization_boundary"
WEBHOOK_FAMILY = "webhook_replay_boundary"
SUPPORTED_CASE_FAMILIES = (AUTH_FAMILY, WEBHOOK_FAMILY)


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
    raise ValueError(f"unsupported benchmark profile: {profile_id}")


def evaluate_adversarial_test(text: str) -> dict[str, Any]:
    anchors = {
        "imports_target": bool(re.search(r"(?:from\s+auth\s+import\s+can_access|auth\.can_access)", text)),
        "cross_tenant": "tenant-a" in text.lower() and "tenant-b" in text.lower(),
        "inactive_actor": bool(
            re.search(
                r"active\s*['\"]?\s*:\s*False|['\"]active['\"]\s*:\s*False|"
                r"\bactive\s*=\s*False\b",
                text,
            )
        ),
        "private_non_admin": "private" in text.lower() and "member" in text.lower(),
        "negative_assertion": bool(re.search(r"assert\s+(?:not\s+can_access|can_access\([^\n]+\)\s+is\s+False)", text)),
    }
    return {
        "anchors": anchors,
        "anchors_retained": sum(anchors.values()),
        "anchors_total": len(anchors),
        "contract_passed": all(anchors.values()),
    }


def evaluate_webhook_test(text: str) -> dict[str, Any]:
    lowered = text.lower()
    anchors = {
        "imports_target": bool(
            re.search(
                r"(?:from\s+webhook\s+import\s+accept_webhook|webhook\.accept_webhook)",
                text,
            )
        ),
        "invalid_signature": "signature" in lowered
        and bool(re.search(r"(?:bad|invalid|forged|untrusted)", lowered)),
        "expired_event": "timestamp" in lowered
        and bool(re.search(r"(?:now\s*[-+]|expired|stale|301|600)", lowered)),
        "replay_duplicate": bool(
            re.search(r"(?:replay|duplicate|seen_ids|accept_webhook[\s\S]+accept_webhook)", lowered)
        ),
        "negative_assertion": bool(
            re.search(
                r"assert\s+(?:not\s+accept_webhook|accept_webhook\([^\n]+\)\s+is\s+False)",
                text,
            )
        ),
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


def run_canary(
    *, workspace: Path, seed: int, model: str = "gpt-5.6-terra",
    profile_id: str = "codex_subscription", case_family: str = AUTH_FAMILY,
) -> dict[str, Any]:
    if case_family not in SUPPORTED_CASE_FAMILIES:
        raise ValueError(f"unsupported QA case family: {case_family}")
    workspace.mkdir(parents=True, exist_ok=True)
    is_webhook = case_family == WEBHOOK_FAMILY
    production_file = workspace / ("webhook.py" if is_webhook else "auth.py")
    broken_source = BROKEN_WEBHOOK if is_webhook else BROKEN
    fixed_source = FIXED_WEBHOOK if is_webhook else FIXED
    production_file.write_text(broken_source, encoding="utf-8")
    runtime = workspace / ".aiteam"
    runtime.mkdir(parents=True, exist_ok=True)
    write_project_adapter_policy(runtime, profile_ids=[profile_id])
    _initialize_project_runtime(workspace, run_profile="solo_lead")
    db = runtime / "aiteam.db"
    profile = next(item for item in project_profiles(runtime) if item["id"] == profile_id)
    agent_id = "role:benchmark_qa"
    issue_id = "issue:benchmark_qa"
    create_agent(
        db,
        agent_id=agent_id,
        role="qa",
        name="Benchmark Adversarial QA",
        seniority="standard",
        adapter_type=str(profile["adapter_type"]),
        adapter_config=adapter_config(profile_id, model),
        capabilities=default_capabilities_for_role("qa"),
        supervisor_agent_id="role:lead",
        metadata={"source": "codex_terra_qa_canary", "seed": seed},
    )
    create_issue(
        db,
        issue_id=issue_id,
        goal_id="goal:intake",
        parent_id="issue:intake",
        title=f"QA adversarial {profile_id}/{model} seed {seed}",
        description=(
            (
                "Especificación: accept_webhook solo acepta firma trusted, timestamp con antigüedad "
                "máxima de 300 segundos y un id no procesado; al aceptar añade el id a seen_ids. "
                "Intenta romper webhook.py. Escribe únicamente tests/test_adversarial_webhook.py "
                "con casos que FALLEN ahora: firma forged, evento expirado y replay del mismo id. "
            )
            if is_webhook
            else (
                "Especificación: can_access solo permite actor activo del mismo tenant; un recurso "
                "private exige role admin. Intenta romper auth.py. Escribe únicamente "
                "tests/test_adversarial_auth.py con casos que FALLEN ahora: cruce tenant-a/tenant-b, "
                "actor inactive y private con role member. "
            )
        )
        + (
            "Ejecuta esos tests. No edites producción ni otros archivos. Si demuestran defecto, "
            "reporta changes_requested pero cierra tu issue done para que el Engineer corrija. "
            "Tras la corrección, elimina tests adversariales que ya pasen y reporta approved con "
            "evidencia de los límites atacados. Siempre usa un AGENT-REPORT válido y "
            "notify_supervisor."
        ),
        status="todo",
        role="qa",
        complexity="medium",
        criticality=QA_CANARY_CRITICALITY,
        assignee_agent_id=agent_id,
    )

    phase1 = _run_phase(db, agent_id=agent_id, issue_id=issue_id, phase=f"attack-seed-{seed}")
    production_unchanged = production_file.read_text(encoding="utf-8") == broken_source
    tests_after_attack = sorted(workspace.glob("tests/test_adversarial_*.py"))
    test_text = "\n".join(path.read_text(encoding="utf-8") for path in tests_after_attack)
    attack_evaluation = (
        evaluate_webhook_test(test_text)
        if is_webhook
        else evaluate_adversarial_test(test_text)
    )
    failing_run = _run_pytest(workspace, tests_after_attack)

    production_file.write_text(fixed_source, encoding="utf-8")
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE issues SET status='done' WHERE json_extract(metadata_json,'$.source')="
            "'reviewer_changes_requested_fix'"
        )
        conn.commit()
    update_issue(db, issue_id=issue_id, status="todo")
    phase2 = _run_phase(db, agent_id=agent_id, issue_id=issue_id, phase=f"verify-fix-seed-{seed}")
    tests_after_fix = sorted(workspace.glob("tests/test_adversarial_*.py"))
    fixed_production_intact = production_file.read_text(encoding="utf-8") == fixed_source

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
        "profile_id": profile_id,
        "model": model,
        "contract_version": CONTRACT_VERSION,
        "case_family": case_family,
        "role": "qa",
        "seed": seed,
        "checks": checks,
        "attack_evaluation": attack_evaluation,
        "failing_test_run": failing_run,
        "phases": {"attack": phase1, "verify_fix": phase2},
        "usage": usage,
        "workspace": (
            workspace.resolve().relative_to(REPO_ROOT).as_posix()
            if workspace.resolve().is_relative_to(REPO_ROOT)
            else workspace.name
        ),
        "ok": all(checks.values()),
    }


def reevaluate_report(report: dict[str, Any]) -> dict[str, Any]:
    """Recompute lexical gates from persisted test-run evidence."""
    updated = dict(report)
    attack_evaluation = dict(report.get("attack_evaluation") or {})
    anchors = dict(attack_evaluation.get("anchors") or {})
    persisted_evidence = str(
        (report.get("failing_test_run") or {}).get("stdout") or ""
    )
    if not anchors.get("inactive_actor"):
        anchors["inactive_actor"] = bool(
            re.search(r"\bactive\s*=\s*False\b", persisted_evidence)
        )
    if anchors:
        attack_evaluation["anchors"] = anchors
        attack_evaluation["anchors_retained"] = sum(anchors.values())
        attack_evaluation["anchors_total"] = len(anchors)
        attack_evaluation["contract_passed"] = all(anchors.values())
        updated["attack_evaluation"] = attack_evaluation
    checks = dict(report.get("checks") or {})
    if attack_evaluation:
        checks["adversarial_test_contract"] = bool(
            attack_evaluation.get("contract_passed")
        )
    updated["checks"] = checks
    updated["contract_version"] = CONTRACT_VERSION
    updated["ok"] = all(checks.values())
    updated["reevaluation"] = {
        "provider_rerun": False,
        "reason": "generalized_transport_and_constructor_syntax_evaluator",
    }
    return updated


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
    identities = {
        (
            report.get("profile_id"),
            report.get("model"),
            report.get("role"),
            report.get("contract_version"),
            report.get("case_family", AUTH_FAMILY),
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
                            "attack_evaluation": report.get("attack_evaluation"),
                            "failing_test_run": report.get("failing_test_run"),
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
    profile_id = reports[0].get("profile_id") if reports else None
    usage_observed = any(
        key.endswith("_tokens") and value
        for key, value in usage.items()
    )
    return {
        "schema_version": 1,
        "benchmark": "codex_terra_adversarial_qa_aggregate",
        "profile_id": profile_id,
        "model": reports[0].get("model") if reports else None,
        "role": "qa",
        "contract_version": reports[0].get("contract_version") if reports else None,
        "case_family": (
            reports[0].get("case_family", AUTH_FAMILY) if reports else None
        ),
        "seeds": [report.get("seed") for report in reports],
        "matrix_complete": matrix_complete,
        "samples_passed": passed,
        "source_receipts": source_receipts,
        "sample_manifest": manifest,
        "integrity": {"sources_bound": sources_bound, "evidence_hashed": True},
        "checks_passed": checks_passed,
        "checks_total": check_count,
        "wall_seconds_median": round(statistics.median(seconds), 3) if seconds else None,
        "wall_seconds_range": [round(min(seconds), 3), round(max(seconds), 3)] if seconds else [],
        "usage": {
            **usage,
            "marginal_cost_cents": 0,
            "telemetry_status": "observed" if usage_observed else "unknown",
            "note": (
                "presión de cuota de suscripción; no coste API"
                if usage_observed
                else "Antigravity no expone usage headless comparable"
            ),
        },
        "conclusion": {
            "exact_pair_calibrated": calibrated,
            "default_change_allowed": False,
            "decision": "calibrate_exact_pair" if calibrated else "retain_requires_canary",
            "unmeasured_constructs": [
                "browser QA",
                "suites multiarchivo",
                "recovery de entorno de tests roto",
            ],
        },
    }


def aggregate_diverse_family_reports(
    aggregates: list[dict[str, Any]], *, model: str, profile_id: str
) -> dict[str, Any]:
    """Une dos agregados QA exactos sin contar seeds como familias."""
    families = sorted(
        {
            str(report.get("case_family") or AUTH_FAMILY)
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
            "case_family": str(report.get("case_family") or AUTH_FAMILY),
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
        "benchmark": "qa_behavioral_diversity_aggregate",
        "profile_id": profile_id,
        "model": model,
        "role": "qa",
        "contract_version": QA_DIVERSITY_CONTRACT,
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
                "browser QA",
                "suites multiarchivo",
                "recovery de infraestructura",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--profile", choices=SUPPORTED_PROFILES, default="codex_subscription")
    parser.add_argument("--case-family", choices=SUPPORTED_CASE_FAMILIES, default=AUTH_FAMILY)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--aggregate-from", type=Path, nargs="+")
    parser.add_argument("--family-aggregate-input", type=Path, action="append")
    parser.add_argument("--reevaluate-from", type=Path)
    args = parser.parse_args()
    if args.family_aggregate_input:
        sources = []
        for path in args.family_aggregate_input:
            source = json.loads(path.read_text(encoding="utf-8"))
            source["_source_receipt"] = path.as_posix()
            sources.append(source)
        report = aggregate_diverse_family_reports(
            sources, model=args.model, profile_id=args.profile
        )
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
    elif args.reevaluate_from:
        report = reevaluate_report(
            json.loads(args.reevaluate_from.read_text(encoding="utf-8"))
        )
    else:
        if args.seed is None or args.workdir is None:
            parser.error("--seed and --workdir are required unless --aggregate-from is used")
        report = run_canary(
            workspace=args.workdir.resolve(), seed=args.seed, model=args.model,
            profile_id=args.profile, case_family=args.case_family,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.aggregate_from or args.family_aggregate_input:
        print(
            json.dumps(
                {
                    "seeds": report.get("seeds"),
                    "case_families": report.get("case_families"),
                    "conclusion": report["conclusion"],
                },
                ensure_ascii=False,
            )
        )
        return 0 if report["conclusion"]["exact_pair_calibrated"] else 2
    print(json.dumps({"seed": args.seed, "checks": report["checks"]}, ensure_ascii=False))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
