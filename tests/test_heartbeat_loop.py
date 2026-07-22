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
        first_batch = conn.execute(
            """
            SELECT decision, reason
            FROM dispatch_candidate_decisions
            WHERE batch_id = (
                SELECT batch_id FROM dispatch_candidate_decisions
                ORDER BY considered_at, batch_id LIMIT 1
            )
            ORDER BY requested_at, wakeup_request_id
            """
        ).fetchall()

    assert pending == 0
    assert completed_runs == 2
    assert first_batch == [("selected", "selected"), ("rejected", "sequential_mode")]


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


def test_reconcile_stale_runs_also_closes_out_its_wakeup_request(tmp_path: Path) -> None:
    """A crashed process leaves its run 'running' forever without ever calling
    finish_wakeup — the wakeup_request it came from must not be left dangling
    at status='running' once the run itself has been reconciled to 'failed'."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    wakeup = enqueue_wakeup(db_path, agent_id="agent-1", source="manual", reason="test")
    create_run(db_path, run_id="run-stale-2", agent_id="agent-1", wakeup_request_id=wakeup["id"])
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE runs SET status = 'running', started_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            ("run-stale-2",),
        )
        conn.execute(
            "UPDATE wakeup_requests SET status = 'running', run_id = ? WHERE id = ?",
            ("run-stale-2", wakeup["id"]),
        )

    recovered = reconcile_stale_runs(db_path, max_age_sec=60)

    assert recovered == ["run-stale-2"]
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, error FROM wakeup_requests WHERE id = ?", (wakeup["id"],)
        ).fetchone()

    assert row["status"] == "failed"
    assert row["error"] == "reconciled: liveness window exceeded"


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


# ── Paralelismo por canal (opt-in) ─────────────────────────────────────────────

def _cand(wid, agent, role, adapter, root):
    return {
        "wakeup_id": wid, "agent_id": agent, "role": role,
        "adapter_type": adapter, "adapter_config_json": "{}",
        "issue_id": root, "root_issue_id": root,
    }


def test_select_parallel_batch_distinct_providers_single_work_slot() -> None:
    from aiteam.heartbeat.scheduler import select_parallel_batch

    candidates = [
        _cand("w1", "role:engineer", "engineer", "subscription_cli", "root-a"),
        _cand("w2", "role:reviewer", "reviewer", "openai_api", "root-b"),   # 2º slot de trabajo → fuera
        _cand("w3", "role:lead", "lead", "openai_api", "root-c"),           # lead openai → entra
        _cand("w4", "role:lead2", "lead", "gemini_api", "root-c"),          # mismo root que w3 → fuera
        _cand("w5", "role:scout", "file_scout", "gemini_api", "root-d"),    # lector gemini → entra
    ]

    chosen = select_parallel_batch(candidates, max_runs=4)

    assert chosen == ["w1", "w3", "w5"]


def test_select_parallel_batch_same_provider_never_concurrent() -> None:
    from aiteam.heartbeat.scheduler import select_parallel_batch

    candidates = [
        _cand("w1", "a1", "lead", "openai_api", "r1"),
        _cand("w2", "a2", "lead", "openai_api", "r2"),  # mismo proveedor → fuera
    ]

    assert select_parallel_batch(candidates, max_runs=3) == ["w1"]


def test_select_parallel_batch_builtin_test_runner_skips_provider_constraint() -> None:
    from aiteam.heartbeat.scheduler import select_parallel_batch

    candidates = [
        _cand("w1", "a1", "lead", "subscription_cli", "r1"),
        _cand("w2", "a2", "test_runner", "subscription_cli", "r2"),  # builtin: no consume proveedor
    ]

    assert select_parallel_batch(candidates, max_runs=3) == ["w1", "w2"]


def test_parallel_drain_executes_batch_concurrently(tmp_path: Path, monkeypatch) -> None:
    """Dos runs de proveedores distintos y roots distintos deben solaparse
    de verdad (no solo despacharse): cada runtime espera a que el otro haya
    ARRANCADO antes de terminar — con dispatch secuencial esto deadlockearía."""
    import threading

    monkeypatch.setenv("AITEAM_PARALLEL_CHANNELS", "1")
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type, status) VALUES "
            "('role:lead', 'lead', 'L', 'subscription_cli', 'active'),"
            "('role:scout', 'web_scout', 'S', 'openai_api', 'active')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) VALUES "
            "('root-a', 'g1', 'A', 'in_progress', 'lead', 'role:lead'),"
            "('root-b', 'g1', 'B', 'in_progress', 'web_scout', 'role:scout')"
        )
        conn.commit()

    both_started = threading.Barrier(2, timeout=30)

    class _BarrierRuntime:
        def __init__(self, adapter_type: str, channel: str) -> None:
            self.descriptor = AdapterDescriptor(adapter_type=adapter_type, channel=channel)

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            both_started.wait()  # solo pasa si el otro run también está corriendo
            return ExecutionResult(status="completed", output="ok")

    enqueue_wakeup(db_path, agent_id="role:lead", source="manual", reason="t", payload={"issue_id": "root-a"})
    enqueue_wakeup(db_path, agent_id="role:scout", source="manual", reason="t", payload={"issue_id": "root-b"})

    registry = AdapterRegistry([
        _BarrierRuntime("subscription_cli", "subscription"),
        _BarrierRuntime("openai_api", "api"),
    ])
    executor = RunExecutor(db_path, registry)
    loop = HeartbeatLoop(db_path, executor)

    dispatched = asyncio.run(loop.run_once())

    assert dispatched == 2
    with sqlite3.connect(str(db_path)) as conn:
        statuses = [r[0] for r in conn.execute("SELECT status FROM runs")]
    assert statuses == ["completed", "completed"]


def test_parallel_flag_off_keeps_sequential_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AITEAM_PARALLEL_CHANNELS", raising=False)
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    enqueue_wakeup(db_path, agent_id="agent-1", source="manual", reason="t1")

    registry = AdapterRegistry([_OkRuntime()])
    loop = HeartbeatLoop(db_path, RunExecutor(db_path, registry))

    assert asyncio.run(loop.run_once()) == 1
