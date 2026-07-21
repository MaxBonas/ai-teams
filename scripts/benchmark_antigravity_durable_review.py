"""Canario vivo A/B de review durable mediante el runtime productivo.

Cada muestra ejecuta dos reviews del mismo modelo: primero sobre un cierre
defectuoso que debe producir ``changes_requested`` y crear un fix durable;
después sobre la corrección, que debe aprobar. Los workspaces son temporales y
el health/config de usuario se aísla dentro de cada muestra.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.adapters.registry import build_default_registry  # noqa: E402
from aiteam.db.migration import SCHEMA_PATH  # noqa: E402
from aiteam.db.wakeups import enqueue_wakeup  # noqa: E402
from aiteam.heartbeat.executor import RunExecutor  # noqa: E402
from aiteam.heartbeat.scheduler import HeartbeatScheduler  # noqa: E402
from aiteam.project_adapters import write_project_adapter_policy  # noqa: E402
from aiteam.user_config import record_model_health  # noqa: E402

ANTIGRAVITY_MODELS = ("gemini-3.5-flash-high", "gemini-3.6-flash-medium")
OPENCODE_MODELS = (
    "opencode/nemotron-3-ultra-free",
    "opencode/deepseek-v4-flash-free",
    "opencode/mimo-v2.5-free",
    "opencode/laguna-s-2.1-free",
)
MODELS = (*ANTIGRAVITY_MODELS, *OPENCODE_MODELS)

BROKEN = '''def close_issue(db, issue_id, actor):
    issue = db.query("SELECT * FROM issues WHERE id=?", issue_id)
    db.execute("UPDATE issues SET status='done' WHERE id=?", issue_id)
    try:
        db.insert("activity_log", {"issue_id": issue_id, "actor": actor})
        parent = db.query("SELECT parent_id FROM issues WHERE id=?", issue_id)
        enqueue_wakeup(parent, "child_done")
    except Exception:
        pass
'''

FIXED = '''def close_issue(db, issue_id, actor, enqueue_wakeup):
    with db.transaction():
        issue = db.query("SELECT * FROM issues WHERE id=?", issue_id)
        if issue is None:
            raise LookupError(issue_id)
        if actor.tenant != issue.tenant_id or actor.agent_id != issue.assignee_agent_id:
            raise PermissionError("actor is not the assigned agent for this tenant")
        db.execute("UPDATE issues SET status='done' WHERE id=?", issue_id)
        db.insert("activity_log", {"issue_id": issue_id, "actor": actor.agent_id})
        parent_id = issue.parent_id
        if parent_id is not None:
            enqueue_wakeup(parent_id, "child_done", idempotency_key=f"child_done:{issue_id}")
'''


def _init_sample(root: Path, model: str) -> Path:
    is_opencode = model.startswith("opencode/")
    profile_id = "opencode_zen_free" if is_opencode else "antigravity_subscription"
    os.environ["AITEAM_USER_CONFIG_DIR"] = str(root / "user-config")
    record_model_health(
        profile_id, model, available=True,
        reason="canary exact-model precondition",
    )
    write_project_adapter_policy(root, profile_ids=[profile_id])
    (root / "close_issue.py").write_text(BROKEN, encoding="utf-8")
    db_path = root / ".aiteam" / "aiteam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    config = json.dumps({
        "profile_id": profile_id,
        "command": ["opencode.cmd"] if is_opencode else ["agy"],
        "cli_kind": "opencode" if is_opencode else "antigravity",
        "model": model,
        "timeout_sec": 180,
        "sandbox": "read-only" if is_opencode else "workspace-write",
    })
    root_meta = json.dumps({
        "profile": "full_team",
        "data_class": "public" if is_opencode else "internal",
    })
    description = (
        "Revisa close_issue.py. Debe impedir cruce de tenant/assignee, persistir status+activity "
        "atómicamente, no tragar excepciones y encolar solo parent no nulo con idempotencia. "
        "Si incumple cualquiera, reporta changes_requested; si todo se cumple, approved. "
        "La revisión siempre termina: issue_status DEBE ser done incluso cuando result sea "
        "changes_requested; blocked es inválido porque impediría crear el fix durable. Añade "
        "comentario con ---AGENT-REPORT---, role reviewer, result, issue_status done, "
        "next_owner engineer si pide cambios o lead si aprueba, blocker y evidence concreta."
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id,title) VALUES ('goal:review','Durable review canary')")
        conn.execute(
            "INSERT INTO agents (id,role,name,seniority,adapter_type,adapter_config_json,status) "
            "VALUES ('role:lead','lead','Lead','lead','lead_builtin','{}','active'),"
            "('role:engineer','engineer','Engineer','standard','role_builtin','{}','active'),"
            "('role:reviewer','reviewer','Reviewer','senior','subscription_cli',?,'active')",
            (config,),
        )
        conn.execute(
            "INSERT INTO issues (id,goal_id,title,status,role,assignee_agent_id,metadata_json) "
            "VALUES ('issue:root','goal:review','Validar cierre durable','in_progress','lead','role:lead',?)",
            (root_meta,),
        )
        conn.execute(
            "INSERT INTO issues (id,goal_id,parent_id,title,description,status,role,assignee_agent_id,criticality) "
            "VALUES ('issue:eng','goal:review','issue:root','Implementación','', 'done','engineer','role:engineer','medium')"
        )
        conn.execute(
            "INSERT INTO issues (id,goal_id,parent_id,title,description,status,role,assignee_agent_id,criticality) "
            "VALUES ('issue:review','goal:review','issue:root','Revisar close_issue.py',?,'todo','reviewer','role:reviewer','medium')",
            (description,),
        )
        conn.commit()
    return db_path


def _wake_and_run(db_path: Path, phase: str) -> float:
    enqueue_wakeup(
        db_path, agent_id="role:reviewer", source="durable_review_canary",
        reason=phase, payload={"issue_id": "issue:review", "wake_reason": phase},
        idempotency_key=f"durable-review:{phase}",
    )
    started = time.monotonic()
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:reviewer")
    if dispatch is None:
        raise RuntimeError(f"reviewer wakeup was not dispatchable for {phase}")
    executor = RunExecutor(db_path, build_default_registry())
    executor.execute(dispatch)
    supervisor = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    if supervisor is not None:
        executor.execute(supervisor)
    return round(time.monotonic() - started, 3)


def _latest_report(db_path: Path) -> dict[str, Any] | None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM agent_reports WHERE issue_id='issue:review' AND valid=1 "
            "AND is_assignee=1 ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def run_sample(root: Path, *, model: str, seed: int) -> dict[str, Any]:
    is_opencode = model.startswith("opencode/")
    db_path = _init_sample(root, model)
    reject_seconds = _wake_and_run(db_path, f"reject-seed-{seed}")
    rejected = _latest_report(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        fix_rows = conn.execute(
            "SELECT id,status FROM issues WHERE json_extract(metadata_json,'$.source')="
            "'reviewer_changes_requested_fix'"
        ).fetchall()
    reject_ok = bool(rejected and rejected.get("result") == "changes_requested" and fix_rows)

    approve_seconds: float | None = None
    approved: dict[str, Any] | None = None
    if reject_ok:
        (root / "close_issue.py").write_text(FIXED, encoding="utf-8")
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE issues SET status='done' WHERE json_extract(metadata_json,'$.source')="
                "'reviewer_changes_requested_fix'"
            )
            conn.execute("UPDATE issues SET status='todo' WHERE id='issue:review'")
            conn.commit()
        approve_seconds = _wake_and_run(db_path, f"approve-seed-{seed}")
        approved = _latest_report(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        runs = [dict(row) for row in conn.execute(
            "SELECT id,status,adapter_type,provider,model,channel,error,error_code,usage_json,"
            "result_json,stdout_excerpt,stderr_excerpt,started_at,finished_at "
            "FROM runs ORDER BY rowid"
        )]
        live = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')"
        ).fetchone()[0]
        claimed = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE status IN ('claimed','running')"
        ).fetchone()[0]
    approve_ok = bool(approved and approved.get("result") in {"approved", "done", "completed"})
    provider_runs = [
        row for row in runs
        if row.get("adapter_type") == "subscription_cli" and row.get("model") == model
    ]
    usage_totals = _sum_usage(provider_runs)
    return {
        "schema_version": 1,
        "benchmark": "durable_review",
        "seed": seed,
        "profile_id": "opencode_zen_free" if is_opencode else "antigravity_subscription",
        "provider": "opencode-zen" if is_opencode else "google-antigravity",
        "channel": "free_gateway" if is_opencode else "subscription",
        "cli_version": "1.18.4" if is_opencode else "1.1.5",
        "model": model,
        "contract": "same_broken_diff_then_same_fix_v1",
        "reject": {"ok": reject_ok, "seconds": reject_seconds, "report": rejected},
        "approve": {"ok": approve_ok, "seconds": approve_seconds, "report": approved},
        "runs": runs,
        "quota_observation": {
            "provider_calls": len(provider_runs),
            "product_runs": len(runs),
            "wall_seconds": round(reject_seconds + (approve_seconds or 0), 3),
            "tokens": usage_totals,
            "marginal_cost_cents": 0,
        },
        "liveness": {"live_runs": live, "claimed_wakeups": claimed},
        "ok": reject_ok and approve_ok and live == 0 and claimed == 0,
    }


def _sum_usage(runs: list[dict[str, Any]]) -> dict[str, int] | None:
    totals: dict[str, int] = {}
    for run in runs:
        try:
            usage = json.loads(str(run.get("usage_json") or "{}"))
        except (TypeError, ValueError):
            continue
        if not isinstance(usage, dict):
            continue
        for key in (
            "input_tokens", "output_tokens", "reasoning_output_tokens",
            "cached_input_tokens", "cache_write_tokens", "total_tokens",
        ):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + int(value)
    return totals or None


def aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    arms: list[dict[str, Any]] = []
    models = list(dict.fromkeys(str(row.get("model") or "") for row in reports))
    for model in models:
        rows = [row for row in reports if row.get("model") == model]
        seconds = [float(row["quota_observation"]["wall_seconds"]) for row in rows]
        seeds = sorted(int(row.get("seed") or 0) for row in rows)
        arms.append({
            "model": model,
            "samples": len(rows),
            "seeds": seeds,
            "seed_matrix_complete": seeds == [1, 2, 3],
            "passed": sum(bool(row.get("ok")) for row in rows),
            "reject_passed": sum(bool(row.get("reject", {}).get("ok")) for row in rows),
            "approve_passed": sum(bool(row.get("approve", {}).get("ok")) for row in rows),
            "wall_seconds_median": round(statistics.median(seconds), 3) if seconds else None,
            "wall_seconds_range": [round(min(seconds), 3), round(max(seconds), 3)] if seconds else None,
            "provider_calls": sum(
                int(row.get("quota_observation", {}).get("provider_calls") or 2) for row in rows
            ),
            "product_runs": sum(
                int(row.get("quota_observation", {}).get("product_runs") or len(row.get("runs") or []))
                for row in rows
            ),
        })
    baseline = arms[0]
    challengers = arms[1:]
    balanced = all(arm["seed_matrix_complete"] for arm in arms)
    all_passed = all(arm["passed"] == arm["samples"] for arm in arms)
    manual_catalog_candidates = [
        arm["model"]
        for arm in challengers
        if arm["passed"] == 3 and baseline["passed"] < baseline["samples"]
    ]
    reason = (
        "challenger estable 3/3 frente a baseline inestable; solo candidato manual"
        if manual_catalog_candidates
        else "sin challenger estable 3/3 que justifique promoción"
    )
    return {
        "schema_version": 1,
        "benchmark": "antigravity_durable_review_aggregate",
        "providers": list(dict.fromkeys(str(row.get("provider") or "") for row in reports)),
        "channels": list(dict.fromkeys(str(row.get("channel") or "") for row in reports)),
        "contract": "same_broken_diff_then_same_fix_v1",
        "source_reports": len(reports),
        "arms": arms,
        "matrix_balanced": balanced,
        "conclusion": {
            "behavioral_contract_tied": balanced and all_passed,
            "default_change_allowed": False,
            "decision": "retain_baseline",
            "baseline": baseline["model"],
            "challengers": [arm["model"] for arm in challengers],
            "manual_catalog_candidates": manual_catalog_candidates,
            "median_wall_seconds_delta": {
                arm["model"]: round(
                    float(arm["wall_seconds_median"] or 0)
                    - float(baseline["wall_seconds_median"] or 0), 3
                ) for arm in challengers
            },
            "reason": reason,
            "tokens_available": False,
            "marginal_cost_cents": 0,
            "goodhart_risk": "residual: una familia de defecto y juez contractual determinista",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", choices=MODELS)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--input", action="append", type=Path, dest="inputs")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.inputs:
        report = aggregate_reports([
            json.loads(path.read_text(encoding="utf-8")) for path in args.inputs
        ])
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report["conclusion"], ensure_ascii=False))
        return 0 if report["matrix_balanced"] else 2
    if not args.model or args.seed is None:
        parser.error("--model and --seed are required unless --input is used")
    with tempfile.TemporaryDirectory(prefix="aiteam-durable-review-", ignore_cleanup_errors=True) as tmp:
        report = run_sample(Path(tmp), model=args.model, seed=args.seed)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "model": args.model, "seed": args.seed}, ensure_ascii=False))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
