"""Canario determinista de `solo_lead`: un agente escribe, verifica y cierra."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers.workspace import _initialize_project_runtime  # noqa: E402
from aiteam.adapters.registry import AdapterDescriptor, AdapterRegistry, ExecutionResult  # noqa: E402
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


class _SoloCanaryRuntime:
    descriptor = AdapterDescriptor(adapter_type="solo_canary", channel="local")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="Implementación y verificación completadas por el agente único.",
            actions={
                "file_ops": [
                    {"op": "write_file", "path": "solo_result.txt", "body": "solo-lead-ok\n"}
                ],
                "create_issues": [{"title": "Delegación prohibida", "role": "reviewer"}],
                "issue_status": "done",
            },
        )


def run_canary(workdir: Path) -> dict[str, Any]:
    workspace = workdir / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    _initialize_project_runtime(
        workspace,
        initial_task="Crea solo_result.txt y cierra la tarea.",
        run_profile="solo_lead",
    )
    db_path = workspace / ".aiteam" / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE agents SET adapter_type='solo_canary', adapter_config_json='{}' "
            "WHERE id='role:lead'"
        )
        conn.commit()
    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="canary",
        reason="new_project",
        payload={"issue_id": "issue:intake", "wake_reason": "new_project", "profile": "solo_lead"},
        idempotency_key="solo-canary:start",
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    if dispatch is None:
        raise RuntimeError("solo_lead canary did not dispatch")
    RunExecutor(db_path, AdapterRegistry([_SoloCanaryRuntime()])).execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        issue_status = conn.execute(
            "SELECT status FROM issues WHERE id='issue:intake'"
        ).fetchone()[0]
        checks = {
            "single_product_agent": conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 1,
            "workspace_written": (workspace / "solo_result.txt").read_text(encoding="utf-8") == "solo-lead-ok\n",
            "root_done": issue_status == "done",
            "no_children": conn.execute(
                "SELECT COUNT(*) FROM issues WHERE parent_id='issue:intake'"
            ).fetchone()[0] == 0,
            "delegation_rejected": conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE action='profile.delegation_constrained'"
            ).fetchone()[0] == 1,
            "no_queued_work": conn.execute(
                "SELECT COUNT(*) FROM wakeup_requests WHERE status IN ('queued','claimed','running')"
            ).fetchone()[0] == 0,
        }
    return {"ok": all(checks.values()), "checks": checks, "db": str(db_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, default=None)
    args = parser.parse_args()
    if args.workdir:
        args.workdir.mkdir(parents=True, exist_ok=True)
        report = run_canary(args.workdir.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="aiteam-solo-canary-", ignore_cleanup_errors=True) as tmp:
            report = run_canary(Path(tmp))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nCANARIO SOLO_LEAD {'OK' if report['ok'] else 'ROTO'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
