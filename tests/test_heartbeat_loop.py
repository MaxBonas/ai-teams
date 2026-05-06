from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.adapters.registry import AdapterDescriptor, AdapterRegistry, ExecutionResult
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.runs import create_run, mark_run_running, reconcile_stale_runs
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.loop import HeartbeatLoop


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type, heartbeat_interval_sec, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("agent-1", "engineer", "Engineer", "subscription_cli", 0, "active"),
        )
        conn.commit()


class _OkRuntime:
    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(status="completed", output="ok")


def test_run_once_drains_wakeup_queue(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    enqueue_wakeup(db_path, agent_id="agent-1", source="manual", reason="test")
    enqueue_wakeup(db_path, agent_id="agent-1", source="manual", reason="test2")

    registry = AdapterRegistry([_OkRuntime()])
    executor = RunExecutor(db_path, registry)
    loop = HeartbeatLoop(db_path, executor)

    dispatched = asyncio.run(loop.run_once())

    assert dispatched == 2

    with sqlite3.connect(str(db_path)) as conn:
        pending = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE status = 'queued'",
        ).fetchone()[0]
        completed_runs = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status = 'completed'",
        ).fetchone()[0]

    assert pending == 0
    assert completed_runs == 2


def test_run_once_returns_zero_when_queue_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    registry = AdapterRegistry([_OkRuntime()])
    executor = RunExecutor(db_path, registry)
    loop = HeartbeatLoop(db_path, executor)

    assert asyncio.run(loop.run_once()) == 0


def test_reconcile_stale_runs_marks_old_running_as_failed(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    create_run(db_path, run_id="run-stale", agent_id="agent-1")
    # Force started_at to be far in the past
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE runs SET status = 'running', started_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            ("run-stale",),
        )

    recovered = reconcile_stale_runs(db_path, max_age_sec=60)

    assert recovered == ["run-stale"]

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT status, error_code FROM runs WHERE id = ?", ("run-stale",)).fetchone()

    assert run["status"] == "failed"
    assert run["error_code"] == "liveness_timeout"


def test_reconcile_stale_runs_skips_recent_running(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    create_run(db_path, run_id="run-fresh", agent_id="agent-1")
    mark_run_running(db_path, run_id="run-fresh")

    recovered = reconcile_stale_runs(db_path, max_age_sec=300)

    assert "run-fresh" not in recovered

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT status FROM runs WHERE id = ?", ("run-fresh",)).fetchone()

    assert run["status"] == "running"
