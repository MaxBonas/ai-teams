"""Canario durable de Luna para roles Tier 3 read-only, incluido MCP gobernado."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers.workspace import _initialize_project_runtime  # noqa: E402
from aiteam.adapters.registry import build_default_registry  # noqa: E402
from aiteam.db.agent_reports import latest_agent_report  # noqa: E402
from aiteam.db.agents import create_agent  # noqa: E402
from aiteam.db.comments import list_comments  # noqa: E402
from aiteam.db.issues import create_issue  # noqa: E402
from aiteam.db.wakeups import enqueue_wakeup  # noqa: E402
from aiteam.extensions import approve_mcp_server, approve_mcp_server_tools  # noqa: E402
from aiteam.heartbeat.executor import RunExecutor  # noqa: E402
from aiteam.heartbeat.scheduler import HeartbeatScheduler  # noqa: E402
from aiteam.mcp_runtime import check_and_activate_mcp_server  # noqa: E402
from aiteam.project_adapters import project_profiles, write_project_adapter_policy  # noqa: E402
from aiteam.tools.catalog import default_capabilities_for_role  # noqa: E402


CASES: dict[str, dict[str, Any]] = {
    "file_scout": {
        "description": (
            "Inspecciona exclusivamente los archivos reales del workspace. Localiza los defectos "
            "causales del checkout multi-tenant, identifica el test que falta y recomienda el "
            "siguiente owner. No edites archivos. Comenta evidencia concreta y termina done."
        ),
        "files": {
            "src/tenant_checkout.py": (
                "def checkout(conn, worker_id):\n"
                "    row = conn.execute(\"SELECT id FROM issues WHERE status='todo' ORDER BY priority LIMIT 1\").fetchone()\n"
                "    if row is None:\n        return None\n"
                "    conn.execute(\"UPDATE issues SET status='in_progress', lease_owner=? WHERE id=?\", (worker_id, row[0]))\n"
                "    return row[0]\n"
            ),
            "tests/test_checkout.py": (
                "def test_empty_queue_returns_none(conn):\n"
                "    assert checkout(conn, 'w1') is None\n"
            ),
        },
        "anchors": {
            "source_path": r"src/tenant_checkout\.py",
            "tenant_boundary": r"\btenant_id\b|multi[- ]tenant",
            "atomicity": r"at[oó]mic|transaction|begin immediate|race|carrera",
            "winner_check": r"rowcount|returning|winner|ganador|reserva",
            "missing_test": r"tests/test_checkout\.py.{0,160}(concurr|tenant|race|carrera)|"
                            r"(concurr|tenant|race|carrera).{0,160}tests/test_checkout\.py",
            "next_owner": r"engineer|ingenier[oa]",
        },
        "forbidden": (r"archivo[s]? (modificado|creado)", r"pytest.*passed"),
    },
    "worker": {
        "description": (
            "Trabajo read-only: convierte estos hechos en un checklist causal de decisión, sin "
            "editar archivos. Wave W3 está activa. Release Engineer es owner. La tasa de error "
            "es 2,1 %, supera el límite 1,0 % durante 5 minutos: pausar W3 y volver a W2. "
            "Reviewer acepta cuando logs y métrica confirmen recuperación. La propuesta de "
            "desplegar el jueves fue descartada y no debe aparecer. Comenta el checklist y termina done."
        ),
        "files": {"README.md": "fixture de benchmark; no modificar\n"},
        "anchors": {
            "active_wave": r"W3",
            "owner": r"Release Engineer",
            "observed_rate": r"2[,.]1\s*%",
            "threshold": r"1[,.]0\s*%",
            "window": r"5\s*(min|minutos)",
            "rollback": r"W2",
            "acceptance": r"Reviewer.{0,100}(logs|m[eé]trica)|(?:logs|m[eé]trica).{0,100}Reviewer",
        },
        "forbidden": (r"jueves", r"thursday", r"archivo[s]? (modificado|creado)"),
    },
    "web_scout": {
        "description": (
            "Consulta exclusivamente la tool MCP aprobada release_advisory_lookup para "
            "ACME-2026-017. Resume paquete, rango afectado, versión corregida, condición "
            "de exposición, fecha y fuente; recomienda siguiente owner. No uses web nativa, "
            "no edites y no invoques tools de escritura. Comenta evidencia y termina done."
        ),
        "files": {"README.md": "fixture MCP de benchmark; no modificar\n"},
        "anchors": {
            "advisory": r"ACME-2026-017",
            "package": r"acme-auth",
            "affected": r"4\.2\.0.{0,40}4\.2\.3|>=?\s*4\.2\.0.{0,40}<\s*4\.2\.3",
            "fixed": r"(?:fixed|corregid[ao]|actualiz).{0,40}4\.2\.3|4\.2\.3.{0,40}(?:fixed|corregid[ao])",
            "exposure": r"refresh[- ]token.{0,80}(internet|p[uú]blic|reachable)|(?:internet|p[uú]blic).{0,80}refresh[- ]token",
            "published": r"2026-07-20",
            "source": r"security\.example\.invalid/ACME-2026-017",
            "next_owner": r"engineer|ingenier[oa]|security",
        },
        "forbidden": (r"publish_advisory", r"archivo[s]? (modificado|creado)"),
    },
    "mcp_operator": {
        "description": (
            "Opera exclusivamente el servidor MCP owner-approved mcp-operator-canary. El "
            "control plane ya recuperó su health tras rechazar versión 0.9.0 frente al pin "
            "1.0.0. Usa solo release_advisory_lookup para ACME-2026-017 y verifica el marcador. "
            "Registra que publish_advisory está denegada y no la invoques. No apruebes, instales, "
            "reconfigures ni edites nada. Persiste un AGENT-REPORT dentro del add_comment final, "
            "notifica al supervisor y termina done."
        ),
        "files": {"README.md": "fixture MCP Operator de benchmark; no modificar\n"},
        "anchors": {
            "advisory": r"ACME-2026-017",
            "allowed_tool": r"release_advisory_lookup",
            "recovered_version": r"0\.9\.0.{0,100}1\.0\.0|1\.0\.0.{0,100}0\.9\.0|version mismatch",
            "active_health": r"health.{0,60}(active|activ[oa]|recuper|recover)|(?:active|activ[oa]|recuper|recover).{0,60}health",
            "write_denied": r"publish_advisory.{0,80}(deneg|deny|deni|no autoriz)|(?:deneg|deny|deni|no autoriz).{0,80}publish_advisory",
            "marker": r"acme-auth.{0,80}4\.2\.3|4\.2\.3.{0,80}acme-auth",
        },
        "forbidden": (r"archivo[s]? (modificado|creado)", r"(?:instal[éeo]|reconfigur[éeo]|aprob[éeo]).{0,40}servidor"),
    },
}


def evaluate_role_artifact(role: str, text: str) -> dict[str, Any]:
    case = CASES[role]
    anchors = {
        name: bool(re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL))
        for name, pattern in case["anchors"].items()
    }
    forbidden = [
        pattern for pattern in case["forbidden"]
        if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    ]
    return {
        "anchors": anchors,
        "anchors_retained": sum(anchors.values()),
        "anchors_total": len(anchors),
        "forbidden_matches": forbidden,
        "contract_passed": all(anchors.values()) and not forbidden,
    }


def run_canary(
    *, role: str, workspace: Path, model: str, reasoning_effort: str, seed: int
) -> dict[str, Any]:
    case = CASES[role]
    workspace.mkdir(parents=True, exist_ok=True)
    for relative, content in case["files"].items():
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    before = {
        relative: (workspace / relative).read_text(encoding="utf-8")
        for relative in case["files"]
    }
    runtime = workspace / ".aiteam"
    runtime.mkdir(parents=True, exist_ok=True)
    write_project_adapter_policy(runtime, profile_ids=["codex_subscription"])
    _initialize_project_runtime(workspace, run_profile="solo_lead")
    db = runtime / "aiteam.db"
    mcp_trace = runtime / f"{role.replace('_', '-')}-mcp-trace.jsonl"
    mcp_server_name = "web-scout-canary" if role == "web_scout" else "mcp-operator-canary"
    health_recovery: dict[str, Any] | None = None
    if role in {"web_scout", "mcp_operator"}:
        fixture = REPO_ROOT / "scripts" / "mcp_web_scout_fixture.py"
        os.environ["AITEAM_MCP_CANARY_TRACE"] = str(mcp_trace)
        os.environ["AITEAM_MCP_CANARY_VERSION"] = "0.9.0" if role == "mcp_operator" else "1.0.0"
        approve_mcp_server(
            runtime,
            name=mcp_server_name,
            source=sys.executable,
            version="1.0.0",
            args=[str(fixture)],
            env_required=["AITEAM_MCP_CANARY_TRACE", "AITEAM_MCP_CANARY_VERSION"],
            applies_to_roles=[role],
            justification="Canario local determinista; sin red ni escritura",
            approved_by="benchmark_owner",
        )
        first_health = None
        if role == "mcp_operator":
            first_health = check_and_activate_mcp_server(runtime, name=mcp_server_name, timeout_sec=5)
            os.environ["AITEAM_MCP_CANARY_VERSION"] = "1.0.0"
        health = check_and_activate_mcp_server(runtime, name=mcp_server_name, timeout_sec=5)
        if health.get("status") != "active":
            raise RuntimeError(f"MCP fixture no activo: {health}")
        if role == "mcp_operator":
            health_recovery = {"failed_probe": first_health, "recovered_probe": health}
        approve_mcp_server_tools(
            runtime,
            name=mcp_server_name,
            tools=[{"name": "release_advisory_lookup", "access": "read"}],
            approved_by="benchmark_owner",
        )
    profiles = project_profiles(runtime)
    profile = next(item for item in profiles if item["id"] == "codex_subscription")
    adapter_config = {
        "profile_id": "codex_subscription",
        "model": model,
        "model_reasoning_effort": reasoning_effort,
        "cli_kind": "codex",
        "command": ["codex"],
        "sandbox": "read-only",
        "approval_policy": "never",
        "timeout_sec": 240,
    }
    agent_id = f"role:{role}"
    issue_id = f"issue:{role}"
    create_agent(
        db,
        agent_id=agent_id,
        role=role,
        name=role.replace("_", " ").title(),
        seniority="cheap",
        adapter_type=str(profile["adapter_type"]),
        adapter_config=adapter_config,
        capabilities=default_capabilities_for_role(role),
        supervisor_agent_id="role:lead",
        metadata={"source": "codex_luna_tier3_canary", "seed": seed},
    )
    create_issue(
        db,
        issue_id=issue_id,
        goal_id="goal:intake",
        parent_id="issue:intake",
        title=f"Canario Luna {role} seed {seed}",
        description=str(case["description"]),
        status="todo",
        role=role,
        complexity="low",
        criticality="low",
        assignee_agent_id=agent_id,
    )
    enqueue_wakeup(
        db,
        agent_id=agent_id,
        source="codex_luna_tier3_canary",
        reason="delegated_work",
        payload={"issue_id": issue_id, "wake_reason": "delegated_work", "seed": seed},
        idempotency_key=f"codex-luna-tier3:{role}:{seed}",
    )
    scheduler = HeartbeatScheduler(db)
    executor = RunExecutor(db, build_default_registry())
    started = time.monotonic()
    attempts = 0
    for _ in range(2):
        dispatch = scheduler.dispatch_next(agent_id=agent_id)
        if dispatch is None:
            break
        attempts += 1
        executor.execute(dispatch)
        with sqlite3.connect(str(db)) as conn:
            status = str(conn.execute("SELECT status FROM issues WHERE id=?", (issue_id,)).fetchone()[0])
        if status in {"done", "blocked", "cancelled"}:
            break
    wall_seconds = round(time.monotonic() - started, 3)
    comments = list_comments(db, issue_id=issue_id)
    artifact = "\n\n".join(str(comment.get("body") or "") for comment in comments)
    evaluation = evaluate_role_artifact(role, artifact)
    report = latest_agent_report(db, issue_id=issue_id)
    after = {
        relative: (workspace / relative).read_text(encoding="utf-8")
        for relative in case["files"]
    }
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        issue_status = str(conn.execute("SELECT status FROM issues WHERE id=?", (issue_id,)).fetchone()[0])
        run = dict(conn.execute(
            "SELECT id,status,error_code,error,model,provider,channel,usage_json FROM runs "
            "WHERE issue_id=? ORDER BY rowid DESC LIMIT 1",
            (issue_id,),
        ).fetchone())
        usage = conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),"
            "COALESCE(SUM(cost_cents),0) FROM cost_events c JOIN runs r ON r.id=c.run_id "
            "WHERE r.issue_id=?",
            (issue_id,),
        ).fetchone()
        tool_access = [dict(row) for row in conn.execute(
            "SELECT tool_name,decision,reason FROM tool_access WHERE run_id=? ORDER BY rowid",
            (run["id"],),
        ).fetchall()]
    trace_rows = []
    if mcp_trace.is_file():
        trace_rows = [json.loads(line) for line in mcp_trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    checks = {
        "issue_done": issue_status == "done",
        "run_completed": run["status"] == "completed",
        "valid_assignee_report": report is not None,
        "artifact_contract": evaluation["contract_passed"],
        "workspace_unchanged": before == after,
        "single_attempt": attempts == 1,
    }
    if role in {"web_scout", "mcp_operator"}:
        checks.update({
            "governed_mcp_granted": any(
                row["tool_name"] == f"mcp:{mcp_server_name}:release_advisory_lookup"
                and row["decision"] == "allowed"
                for row in tool_access
            ),
            "write_tool_denied": any(
                row["tool_name"] == f"mcp:{mcp_server_name}:publish_advisory"
                and row["decision"] == "denied"
                for row in tool_access
            ),
            "approved_tool_called": any(
                row.get("method") == "tools/call" and row.get("tool") == "release_advisory_lookup"
                for row in trace_rows
            ),
            "write_tool_not_called": not any(
                row.get("method") == "tools/call" and row.get("tool") == "publish_advisory"
                for row in trace_rows
            ),
        })
    if role == "mcp_operator":
        checks.update({
            "health_failure_observed": bool(health_recovery)
            and health_recovery["failed_probe"].get("status") == "failed",
            "health_recovered_active": bool(health_recovery)
            and health_recovery["recovered_probe"].get("status") == "active",
        })
    return {
        "schema_version": 1,
        "benchmark": "codex_luna_tier3_role_canary",
        "profile_id": "codex_subscription",
        "model": model,
        "reasoning_effort": reasoning_effort,
        "role": role,
        "seed": seed,
        "wall_seconds": wall_seconds,
        "attempts": attempts,
        "checks": checks,
        "evaluation": evaluation,
        "agent_report": report,
        "artifact": artifact,
        "runtime": {
            "issue_status": issue_status,
            "run": run,
            "input_tokens": int(usage[0]),
            "output_tokens": int(usage[1]),
            "cost_cents": int(usage[2]),
            "db": str(db),
            "tool_access": tool_access,
            "mcp_trace": trace_rows,
            "mcp_health_recovery": health_recovery,
        },
        "ok": all(checks.values()),
    }


def aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    seconds = [float(report["wall_seconds"]) for report in reports]
    passed = sum(bool(report.get("ok")) for report in reports)
    checks_total = sum(len(report.get("checks", {})) for report in reports)
    checks_passed = sum(sum(bool(value) for value in report.get("checks", {}).values()) for report in reports)
    comparable = bool(reports) and len({(report.get("model"), report.get("role")) for report in reports}) == 1
    input_tokens = sum(int(report.get("runtime", {}).get("input_tokens") or 0) for report in reports)
    output_tokens = sum(int(report.get("runtime", {}).get("output_tokens") or 0) for report in reports)
    return {
        "schema_version": 1,
        "benchmark": "codex_tier_role_canary_aggregate",
        "profile_id": "codex_subscription",
        "model": reports[0].get("model") if reports else None,
        "role": reports[0].get("role") if reports else None,
        "seeds": [report.get("seed") for report in reports],
        "matrix_complete": comparable and len(reports) == 3,
        "samples_passed": passed,
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        "wall_seconds_median": round(statistics.median(seconds), 3) if seconds else None,
        "wall_seconds_range": [round(min(seconds), 3), round(max(seconds), 3)] if seconds else [],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "marginal_cost_cents": 0,
            "note": "presión de cuota de suscripción; no coste API",
        },
        "conclusion": {
            "exact_pair_calibrated": comparable and len(reports) == 3 and passed == 3,
            "default_change_allowed": False,
            "decision": "calibrate_exact_pair" if passed == 3 else "retain_requires_tool_fixture",
            "unmeasured_constructs": ["MCP remoto", "credenciales reales", "recovery durante una llamada activa"],
        },
    }


def reevaluate_report(report: dict[str, Any]) -> dict[str, Any]:
    """Recompute deterministic text gates without repeating provider inference."""
    updated = dict(report)
    evaluation = evaluate_role_artifact(str(report["role"]), str(report.get("artifact") or ""))
    checks = dict(report.get("checks", {}))
    checks["artifact_contract"] = bool(evaluation["contract_passed"])
    updated["evaluation"] = evaluation
    updated["checks"] = checks
    updated["ok"] = all(checks.values())
    updated["reevaluation"] = {"reason": "deterministic_evaluator_contract_update", "provider_rerun": False}
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=tuple(CASES))
    parser.add_argument("--seed", type=int)
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning-effort", default="low", choices=("low", "medium", "high"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--aggregate-from", type=Path, nargs="+")
    parser.add_argument("--reevaluate-from", type=Path)
    args = parser.parse_args()
    if args.aggregate_from:
        report = aggregate_reports([json.loads(path.read_text(encoding="utf-8")) for path in args.aggregate_from])
        ok = bool(report["conclusion"]["exact_pair_calibrated"])
    elif args.reevaluate_from:
        report = reevaluate_report(json.loads(args.reevaluate_from.read_text(encoding="utf-8")))
        ok = bool(report["ok"])
    else:
        if args.role is None or args.seed is None or args.workdir is None:
            parser.error("--role, --seed and --workdir are required unless aggregate/reevaluate mode is used")
        report = run_canary(
            role=args.role,
            workspace=args.workdir.resolve(),
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            seed=args.seed,
        )
        ok = bool(report["ok"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"role": report["role"], "seed": report.get("seed"), "checks": report.get("checks")}))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
