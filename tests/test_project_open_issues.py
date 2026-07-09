"""project_open_issues — the Lead's global view of open work.

Live bug: with several root issues (each user task can start a new root), a
Lead woken on a finished root truthfully answered "no hay issues abiertas"
— its wake payload only carries that issue's subtree — while another tree
had two live issues, one of them failing every run.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from aiteam.adapters.registry import AdapterDescriptor, AdapterRegistry, ExecutionResult
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wake_payload import project_open_issues
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


def _init_db(tmp_path: Path) -> Path:
    """Two roots: issue:intake (done) and a second live tree."""
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES"
            " ('role:lead', 'lead', 'Lead', 'subscription_cli'),"
            " ('role:engineer', 'engineer', 'Engineer', 'subscription_cli')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:intake', 'goal-1', 'First task', 'done', 'lead', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:root2', 'goal-1', 'Second task', 'in_progress', 'reviewer', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:root2:child', 'goal-1', 'issue:root2', 'Child work', 'in_progress', 'engineer', 'role:engineer')"
        )
        conn.commit()
    return db_path


def test_helper_lists_all_roots_and_excludes_current(tmp_path: Path) -> None:
    db_path = _init_db(tmp_path)

    rows = project_open_issues(db_path, exclude_issue_id="issue:root2")

    ids = [r["id"] for r in rows]
    assert "issue:root2:child" in ids
    assert "issue:root2" not in ids  # excluded — it's the wake's own issue
    assert "issue:intake" not in ids  # terminal


class _CapturingRuntime:
    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        import json
        raw = str(wake_context.get("wake_payload_json") or "")
        self.payloads.append(json.loads(raw) if raw else {})
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(status="completed", output="ok")


def _dispatch(db_path: Path, *, agent_id: str, issue_id: str) -> Any:
    enqueue_wakeup(
        db_path, agent_id=agent_id, source="manual", reason="manual",
        payload={"issue_id": issue_id},
    )
    return HeartbeatScheduler(db_path).dispatch_next(agent_id=agent_id)


def test_lead_payload_includes_global_open_issues(tmp_path: Path) -> None:
    """The exact live scenario: Lead woken on the DONE root must still see
    the other tree's open issues in its payload."""
    db_path = _init_db(tmp_path)
    runtime = _CapturingRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))

    executor.execute(_dispatch(db_path, agent_id="role:lead", issue_id="issue:intake"))

    assert runtime.payloads, "runtime should have received a payload"
    open_ids = {r["id"] for r in runtime.payloads[0].get("project_open_issues") or []}
    assert {"issue:root2", "issue:root2:child"} <= open_ids


def test_worker_payload_does_not_carry_global_view(tmp_path: Path) -> None:
    db_path = _init_db(tmp_path)
    runtime = _CapturingRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))

    executor.execute(_dispatch(db_path, agent_id="role:engineer", issue_id="issue:root2:child"))

    assert runtime.payloads
    assert "project_open_issues" not in runtime.payloads[0]
