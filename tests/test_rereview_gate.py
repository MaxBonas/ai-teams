"""Re-review churn gate: after N completed runs on the same reviewer issue,
another wake escalates to the user instead of burning a run.

capa-2 burned 6 reviewer runs on one issue in 15 minutes because the Lead
kept re-waking the reviewer without changing anything about its evidence.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.adapters.registry import AdapterDescriptor, AdapterRegistry, ExecutionResult
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


class _ReviewRuntime:
    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def __init__(self) -> None:
        self.calls = 0

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(status="completed", output="review done")


def _init_db(db_path: Path, *, completed_runs: int) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES"
            " ('role:reviewer', 'reviewer', 'Reviewer', 'subscription_cli')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:review', 'goal-1', 'Revisar entrega', 'in_progress', 'reviewer', 'role:reviewer')"
        )
        for i in range(completed_runs):
            conn.execute(
                "INSERT INTO runs (id, agent_id, issue_id, invocation_source, status)"
                " VALUES (?, 'role:reviewer', 'issue:review', 'heartbeat', 'completed')",
                (f"run-prior-{i}",),
            )
        conn.commit()


def _dispatch(db_path: Path, *, ctx: dict[str, Any] | None = None) -> Any:
    # The scheduler takes wake_reason from the wakeup's reason field first
    # (payload wake_reason is the fallback) — mirror the real enqueue sites.
    reason = str((ctx or {}).get("wake_reason") or "review_requested")
    enqueue_wakeup(
        db_path,
        agent_id="role:reviewer",
        source="manual",
        reason=reason,
        payload={"issue_id": "issue:review", **(ctx or {})},
    )
    return HeartbeatScheduler(db_path).dispatch_next(agent_id="role:reviewer")


def _pending_rereview_interactions(db_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE status = 'pending'"
        ).fetchall()
    return [
        dict(row) for row in rows
        if json.loads(row["payload_json"]).get("reason") == "rereview_limit_reached"
    ]


def test_under_limit_runs_normally(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, completed_runs=2)
    runtime = _ReviewRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))

    dispatch = _dispatch(db_path)
    executor.execute(dispatch)

    assert runtime.calls == 1
    assert _pending_rereview_interactions(db_path) == []


def test_at_limit_skips_and_escalates_once(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, completed_runs=4)  # default AITEAM_REREVIEW_LIMIT = 4
    runtime = _ReviewRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))

    dispatch = _dispatch(db_path)
    executor.execute(dispatch)

    assert runtime.calls == 0
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (dispatch.run["id"],)).fetchone()
    assert run["status"] == "skipped"
    assert run["error_code"] == "rereview_limit_reached"
    assert len(_pending_rereview_interactions(db_path)) == 1

    # A second capped wake dedupes onto the same escalation (idempotent per round)
    dispatch2 = _dispatch(db_path)
    executor.execute(dispatch2)
    assert runtime.calls == 0
    assert len(_pending_rereview_interactions(db_path)) == 1


def test_user_authorised_round_bypasses_gate(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, completed_runs=6)
    runtime = _ReviewRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))

    dispatch = _dispatch(db_path, ctx={"wake_reason": "interaction_resolved", "action": "accept"})
    executor.execute(dispatch)

    assert runtime.calls == 1


def test_parallel_capped_issues_share_one_card(tmp_path: Path) -> None:
    """3 review issues tripping together must not flood the user with 3
    identical cards — one pending rereview escalation at a time."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, completed_runs=4)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:review2', 'goal-1', 'Revisar otra entrega', 'in_progress', 'reviewer', 'role:reviewer')"
        )
        for i in range(4):
            conn.execute(
                "INSERT INTO runs (id, agent_id, issue_id, invocation_source, status)"
                " VALUES (?, 'role:reviewer', 'issue:review2', 'heartbeat', 'completed')",
                (f"run-b-{i}",),
            )
        conn.commit()
    runtime = _ReviewRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))

    executor.execute(_dispatch(db_path))  # issue:review → creates the card
    enqueue_wakeup(
        db_path,
        agent_id="role:reviewer",
        source="manual",
        reason="review_requested",
        payload={"issue_id": "issue:review2"},
    )
    executor.execute(HeartbeatScheduler(db_path).dispatch_next(agent_id="role:reviewer"))

    assert runtime.calls == 0
    assert len(_pending_rereview_interactions(db_path)) == 1


def test_engineer_issues_not_capped(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, completed_runs=10)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET role = 'engineer' WHERE id = 'role:reviewer'")
        conn.execute("UPDATE issues SET role = 'engineer' WHERE id = 'issue:review'")
        conn.commit()
    runtime = _ReviewRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))

    dispatch = _dispatch(db_path)
    executor.execute(dispatch)

    assert runtime.calls == 1
