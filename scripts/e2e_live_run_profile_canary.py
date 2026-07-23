"""Canarios vivos y aislados de creación por run profile (fuera de CI)."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers.workspace import _initialize_project_runtime  # noqa: E402
from aiteam.adapters.registry import build_default_registry  # noqa: E402
from aiteam.db.wakeups import enqueue_wakeup  # noqa: E402
from aiteam.heartbeat.executor import RunExecutor  # noqa: E402
from aiteam.heartbeat.scheduler import HeartbeatScheduler  # noqa: E402
from aiteam.project_adapters import write_project_adapter_policy  # noqa: E402
from aiteam.user_config import record_model_health  # noqa: E402


def _cli_version() -> str:
    proc = subprocess.run(
        ["agy", "--version"], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=20,
    )
    return (proc.stdout or proc.stderr).strip()


def run_solo_lead(root: Path) -> dict[str, Any]:
    workspace = root / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "test_canary.py").write_text(
        "def test_live_solo_workspace():\n    assert True\n", encoding="utf-8"
    )
    os.environ["AITEAM_USER_CONFIG_DIR"] = str(root / "user-config")
    model = "gemini-3.1-pro-high"
    record_model_health(
        "antigravity_subscription", model, available=True,
        reason="live run-profile canary exact model",
    )
    runtime_dir = workspace / ".aiteam"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    write_project_adapter_policy(runtime_dir, profile_ids=["antigravity_subscription"])
    _initialize_project_runtime(
        workspace,
        initial_task=(
            "Canario reversible solo_lead. Trabaja tú mismo sin delegar. Crea "
            "solo_result.txt con el texto exacto solo-lead-live-ok, ejecuta la "
            "verificación disponible y cierra issue:intake."
        ),
        run_profile="solo_lead",
        data_class="internal",
    )
    db_path = runtime_dir / "aiteam.db"
    config = json.dumps({
        "profile_id": "antigravity_subscription", "command": ["agy"],
        "cli_kind": "antigravity", "model": model, "timeout_sec": 240,
    })
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE agents SET adapter_type='subscription_cli', adapter_config_json=? "
            "WHERE id='role:lead'", (config,),
        )
        conn.commit()
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="live_run_profile_canary",
        reason="new_project", payload={
            "issue_id": "issue:intake", "wake_reason": "new_project", "profile": "solo_lead",
        }, idempotency_key="live-profile:solo-lead:start",
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    if dispatch is None:
        raise RuntimeError("solo_lead live canary did not dispatch")
    started = time.monotonic()
    RunExecutor(db_path, build_default_registry()).execute(dispatch)
    seconds = round(time.monotonic() - started, 3)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        root_issue = dict(conn.execute(
            "SELECT status,metadata_json FROM issues WHERE id='issue:intake'"
        ).fetchone())
        runs = [dict(row) for row in conn.execute(
            "SELECT id,agent_id,issue_id,status,error_code,usage_json FROM runs ORDER BY rowid"
        )]
        checks = {
            "root_done": root_issue["status"] == "done",
            "single_agent": conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 1,
            "no_children": conn.execute(
                "SELECT COUNT(*) FROM issues WHERE parent_id='issue:intake'"
            ).fetchone()[0] == 0,
            "workspace_written": (workspace / "solo_result.txt").exists(),
            "verification_passed": conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE action='solo_lead.verification_passed'"
            ).fetchone()[0] == 1,
            "no_live_runs": conn.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')"
            ).fetchone()[0] == 0,
            "no_claimed_wakeups": conn.execute(
                "SELECT COUNT(*) FROM wakeup_requests WHERE status IN ('claimed','running')"
            ).fetchone()[0] == 0,
        }
    return {
        "schema_version": 1,
        "benchmark": "live_run_profile_canary",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profile": "solo_lead",
        "data_class": "internal",
        "adapter_profile_id": "antigravity_subscription",
        "provider": "google-antigravity",
        "channel": "subscription",
        "cli_version": _cli_version(),
        "model": model,
        "role": "lead",
        "seconds": seconds,
        "tokens": None,
        "marginal_cost_cents": 0,
        "checks": checks,
        "runs": runs,
        "root_issue": root_issue,
        "ok": all(checks.values()),
    }


def run_full_team(root: Path) -> dict[str, Any]:
    workspace = root / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ["AITEAM_USER_CONFIG_DIR"] = str(root / "user-config")
    for profile_id, model in (
        ("codex_subscription", "gpt-5.6-sol"),
        ("antigravity_subscription", "gemini-3.1-pro-high"),
        ("antigravity_subscription", "claude-sonnet-4-6"),
        ("antigravity_subscription", "gemini-3.5-flash-high"),
        ("antigravity_subscription", "gemini-3.5-flash-low"),
    ):
        record_model_health(profile_id, model, available=True, reason="live full_team canary")
    runtime_dir = workspace / ".aiteam"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    write_project_adapter_policy(
        runtime_dir, profile_ids=["codex_subscription", "antigravity_subscription"]
    )
    _initialize_project_runtime(
        workspace,
        initial_task=(
            "Crea greeting.py con greet(name) que devuelva exactamente 'Hola, {name}!' y "
            "tests/test_greeting.py con pruebas para Ana y una cadena vacía. Ejecuta pytest. "
            "Es una tarea reversible, acotada y sin dependencias externas."
        ),
        run_profile="full_team",
        data_class="internal",
    )
    db_path = runtime_dir / "aiteam.db"
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="live_run_profile_canary",
        reason="new_project", payload={
            "issue_id": "issue:intake", "wake_reason": "new_project", "profile": "full_team",
        }, idempotency_key="live-profile:full-team:start",
    )
    executor = RunExecutor(db_path, build_default_registry())
    scheduler = HeartbeatScheduler(db_path)
    started = time.monotonic()
    ticks = 0
    while ticks < 14 and time.monotonic() - started < 720:
        dispatch = scheduler.dispatch_next()
        if dispatch is None:
            break
        ticks += 1
        executor.execute(dispatch)
        # El Lead Codex crea el blueprint en la primera run. A partir de ahí
        # fijamos los especialistas al routing Antigravity ya calibrado para
        # que el canario pruebe los pares exactos y no una monocultura Codex.
        with sqlite3.connect(str(db_path)) as conn:
            configs = {
                "engineer": "claude-sonnet-4-6",
                "software_engineer": "claude-sonnet-4-6",
                "reviewer": "gemini-3.5-flash-high",
                "code_reviewer": "gemini-3.5-flash-high",
                "test_designer": "gemini-3.5-flash-high",
                "file_scout": "gemini-3.5-flash-low",
                "web_scout": "gemini-3.5-flash-low",
            }
            for role, model in configs.items():
                cfg = json.dumps({
                    "profile_id": "antigravity_subscription", "command": ["agy"],
                    "cli_kind": "antigravity", "model": model, "timeout_sec": 240,
                })
                conn.execute(
                    "UPDATE agents SET adapter_type='subscription_cli', adapter_config_json=? "
                    "WHERE role=?", (cfg, role),
                )
            conn.commit()
        with sqlite3.connect(str(db_path)) as conn:
            status = conn.execute(
                "SELECT status FROM issues WHERE id='issue:intake'"
            ).fetchone()[0]
        if status in {"done", "cancelled"}:
            break
    seconds = round(time.monotonic() - started, 3)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        root_status = conn.execute(
            "SELECT status FROM issues WHERE id='issue:intake'"
        ).fetchone()[0]
        issues = [dict(row) for row in conn.execute(
            "SELECT id,parent_id,role,status,assignee_agent_id FROM issues ORDER BY rowid"
        )]
        runs = [dict(row) for row in conn.execute(
            "SELECT id,agent_id,issue_id,status,provider,model,channel,error_code,usage_json "
            "FROM runs ORDER BY rowid"
        )]
        reports = [dict(row) for row in conn.execute(
            "SELECT agent_role,result,issue_status,evidence,valid,is_assignee FROM agent_reports "
            "ORDER BY rowid"
        )]
        reviewer_ok = any(
            row["agent_role"] in {"reviewer", "code_reviewer"}
            and row["result"] in {"approved", "done", "completed"}
            and row["valid"] and row["is_assignee"]
            for row in reports
        )
        test_ok = any(
            row["agent_role"] == "test_runner" and "exit 0" in str(row["evidence"] or "")
            for row in reports
        )
        checks = {
            "root_done": root_status == "done",
            "engineer_present": any(row["role"] == "engineer" for row in issues),
            "reviewer_present": any(row["role"] == "reviewer" for row in issues),
            "reviewer_approved": reviewer_ok,
            "test_runner_exit_zero": test_ok,
            "workspace_written": (workspace / "greeting.py").exists(),
            "no_live_runs": conn.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')"
            ).fetchone()[0] == 0,
            "no_claimed_wakeups": conn.execute(
                "SELECT COUNT(*) FROM wakeup_requests WHERE status IN ('claimed','running')"
            ).fetchone()[0] == 0,
            "no_queued_wakeups": conn.execute(
                "SELECT COUNT(*) FROM wakeup_requests WHERE status='queued'"
            ).fetchone()[0] == 0,
        }
    return {
        "schema_version": 1, "benchmark": "live_run_profile_canary",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "profile": "full_team",
        "data_class": "internal",
        "adapter_profile_ids": ["codex_subscription", "antigravity_subscription"],
        "seconds": seconds, "ticks": ticks, "tokens_partial": True,
        "marginal_cost_cents": 0, "checks": checks, "issues": issues,
        "runs": runs, "reports": reports, "ok": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=["solo_lead", "full_team"])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aiteam-live-profile-", ignore_cleanup_errors=True) as tmp:
        report = (
            run_solo_lead(Path(tmp)) if args.profile == "solo_lead"
            else run_full_team(Path(tmp))
        )
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "profile": report["profile"]}, ensure_ascii=False))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
