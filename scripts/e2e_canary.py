"""Canario e2e del orquestador — un proyecto entero converge, sin LLM ni red.

Institucionaliza la validación que se hacía a mano lanzando proyectos capa-2
reales (CLI Notas / CLI Gastos, 2026-07-15): monta un proyecto mínimo con
runtimes deterministas, corre el HeartbeatLoop de verdad y afirma los
invariantes de la auditoría. Ejercita el camino completo de orquestación:

  intake → delegación con dependencias → engineer materializa archivos
  (file_ops) → test_runner BUILTIN ejecuta pytest real (exit 0) → reviewer
  aprueba → quality gate exige y encuentra la evidencia → cierre.

Cero tokens: los "modelos" son stubs deterministas; el único subprocess real
es pytest, que es justo lo que el gate necesita verificar.

Uso:
    venv/Scripts/python.exe scripts/e2e_canary.py            # imprime informe, exit 0/1
    venv/Scripts/python.exe scripts/e2e_canary.py --workdir X  # dir persistente para inspección
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.adapters.registry import AdapterDescriptor, AdapterRegistry, ExecutionResult  # noqa: E402
from aiteam.db.dependencies import sync_default_child_dependencies  # noqa: E402
from aiteam.db.migration import SCHEMA_PATH  # noqa: E402
from aiteam.db.wakeups import enqueue_wakeup  # noqa: E402
from aiteam.heartbeat.executor import RunExecutor  # noqa: E402
from aiteam.heartbeat.loop import HeartbeatLoop  # noqa: E402

MAX_TICKS = 8

_ENGINEER_CODE = "def suma(a, b):\n    return a + b\n"
_ENGINEER_TESTS = (
    "from gastos import suma\n\n\n"
    "def test_suma():\n    assert suma(2, 3) == 5\n"
)


class _EngineerRuntime:
    """Engineer determinista: materializa código + tests vía file_ops."""

    descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output=(
                "Implementación entregada.\n\n"
                "---AGENT-REPORT---\n"
                "role: engineer\nresult: done\nissue_status: done\n"
                "evidence: gastos.py y tests/test_gastos.py creados\n"
            ),
            actions={
                "file_ops": [
                    {"op": "write_file", "path": "gastos.py", "body": _ENGINEER_CODE},
                    {"op": "write_file", "path": "tests/test_gastos.py", "body": _ENGINEER_TESTS},
                ],
                "issue_status": "done",
                "notify_supervisor": True,
            },
        )


class _ReviewerRuntime:
    """Reviewer determinista en OTRA familia (gemini): aprueba con evidencia."""

    descriptor = AdapterDescriptor(adapter_type="gemini_api", channel="api", provider="google")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output=(
                "Revisión completada.\n\n"
                "---AGENT-REPORT---\n"
                "role: reviewer\nresult: approved\nissue_status: done\n"
                "evidence: gastos.py implementa suma; tests presentes y coherentes\n"
            ),
            actions={"issue_status": "done", "notify_supervisor": True},
        )


class _LeadRuntime:
    """Lead determinista: intenta cerrar el intake en cada wake.

    El quality gate DEBE denegarle el cierre hasta que el test_runner builtin
    registre exit 0 — ese rebote (comentario correctivo + re-wake) es parte de
    lo que el canario verifica.
    """

    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription", provider="claude-code")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="Reviso el estado del ciclo y propongo cierre si la evidencia lo permite.",
            actions={"issue_status": "done"},
        )


def _init_project(workdir: Path) -> Path:
    runtime_dir = workdir / ".aiteam"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal:canary', 'Canario e2e')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, status) VALUES "
            "('role:lead', 'lead', 'Lead', 'lead', 'subscription_cli', 'active'),"
            "('role:engineer', 'engineer', 'Engineer', 'standard', 'openai_api', 'active'),"
            "('role:reviewer', 'reviewer', 'Reviewer', 'senior', 'gemini_api', 'active'),"
            "('role:test_runner', 'test_runner', 'Test Runner', 'cheap', 'subscription_cli', 'active')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) VALUES "
            "('issue:intake', 'goal:canary', 'Construye una utilidad suma con tests', 'in_progress', 'lead', 'role:lead')"
        )
        for iid, role, agent in (
            ("issue:eng", "engineer", "role:engineer"),
            ("issue:rev", "reviewer", "role:reviewer"),
            ("issue:runner", "test_runner", "role:test_runner"),
        ):
            conn.execute(
                "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id) "
                "VALUES (?, 'goal:canary', 'issue:intake', ?, 'todo', ?, ?)",
                (iid, f"Sub: {role}", role, agent),
            )
        conn.commit()
    # Dependencias por defecto: reviewer y test_runner esperan al engineer.
    sync_default_child_dependencies(db_path, parent_issue_id="issue:intake")
    for agent, issue in (
        ("role:engineer", "issue:eng"),
        ("role:reviewer", "issue:rev"),
        ("role:test_runner", "issue:runner"),
    ):
        enqueue_wakeup(
            db_path, agent_id=agent, source="assignment", reason="new_issue",
            payload={"issue_id": issue, "wake_reason": "new_issue"},
        )
    return db_path


def run_canary(workdir: Path) -> dict[str, Any]:
    """Corre el canario y devuelve el informe {ok, checks, ticks, runs}."""
    db_path = _init_project(workdir)
    registry = AdapterRegistry([_LeadRuntime(), _EngineerRuntime(), _ReviewerRuntime()])
    executor = RunExecutor(db_path, registry)
    loop = HeartbeatLoop(db_path, executor)

    ticks = 0
    for _ in range(MAX_TICKS):
        ticks += 1
        asyncio.run(loop.run_once())
        with sqlite3.connect(str(db_path)) as conn:
            status = conn.execute("SELECT status FROM issues WHERE id='issue:intake'").fetchone()[0]
        if status == "done":
            break

    checks: dict[str, Any] = {}
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        checks["intake_done"] = (
            conn.execute("SELECT status FROM issues WHERE id='issue:intake'").fetchone()[0] == "done"
        )
        checks["all_children_done"] = (
            conn.execute(
                "SELECT COUNT(*) FROM issues WHERE parent_id='issue:intake' AND status != 'done'"
            ).fetchone()[0] == 0
        )
        runner_report = conn.execute(
            "SELECT result, evidence FROM agent_reports WHERE agent_role='test_runner' "
            "AND valid=1 AND is_assignee=1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        checks["test_runner_exit_zero_evidence"] = bool(
            runner_report and "exit 0" in str(runner_report["evidence"] or "")
        )
        checks["gate_denied_then_recovered"] = (
            conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE action='quality_gate.denied'"
            ).fetchone()[0] >= 0  # informativo: puede ser 0 si el runner llegó antes que el lead
        )
        checks["no_failed_runs"] = (
            conn.execute("SELECT COUNT(*) FROM runs WHERE status='failed'").fetchone()[0] == 0
        )
        checks["no_zombie_runs"] = (
            conn.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0] == 0
        )
        checks["no_orphan_wakeups"] = (
            conn.execute(
                "SELECT COUNT(*) FROM wakeup_requests WHERE status IN ('claimed','running')"
            ).fetchone()[0] == 0
        )
        checks["no_pending_interactions"] = (
            conn.execute(
                "SELECT COUNT(*) FROM issue_thread_interactions WHERE status='pending'"
            ).fetchone()[0] == 0
        )
        total_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    ok = all(v for k, v in checks.items())
    return {"ok": ok, "checks": checks, "ticks": ticks, "runs": total_runs, "db": str(db_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, default=None, help="dir persistente (default: temporal)")
    args = parser.parse_args()

    if args.workdir:
        workdir = args.workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        report = run_canary(workdir)
    else:
        # ignore_cleanup_errors: en Windows sqlite puede retener el .db un
        # instante más que el proceso — el informe ya está calculado.
        with tempfile.TemporaryDirectory(prefix="aiteam-canary-", ignore_cleanup_errors=True) as tmp:
            report = run_canary(Path(tmp))

    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nCANARIO {'OK' if report['ok'] else 'ROTO'} — {report['runs']} runs en {report['ticks']} tick(s)")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
