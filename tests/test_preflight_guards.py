"""Silent preflight guards (anti-loop): terminal issues, pending product
decisions, and unchanged review evidence skip the run WITHOUT escalating —
they are dedupe, not errors. Seen live in capa-2: 4 reviewer runs posting
verdicts on an issue that was already done, and identical reviews re-run
while the user's product decision was pending."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.adapters.registry import AdapterDescriptor, AdapterRegistry, ExecutionResult
from aiteam.db.interactions import create_interaction
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


class _CountingRuntime:
    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def __init__(self) -> None:
        self.calls = 0

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(status="completed", output="done")


def _init(tmp_path: Path, *, issue_status: str = "in_progress", role: str = "engineer") -> Path:
    """Workspace at tmp/ws with db at tmp/ws/.aiteam/aiteam.db and one file."""
    ws = tmp_path / "ws"
    (ws / ".aiteam").mkdir(parents=True)
    (ws / "src").mkdir()
    (ws / "src" / "main.cs").write_text("class Main {}", encoding="utf-8")
    db_path = ws / ".aiteam" / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES (?, ?, ?, 'subscription_cli')",
            (f"role:{role}", role, role.title()),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id, parent_id)"
            " VALUES ('issue:root', 'goal-1', 'Root', 'in_progress', 'lead', NULL, NULL)"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id, parent_id)"
            " VALUES ('issue:work', 'goal-1', 'Work', ?, ?, ?, 'issue:root')",
            (issue_status, role, f"role:{role}"),
        )
        conn.commit()
    return db_path


def _dispatch(db_path: Path, *, role: str = "engineer", wake_reason: str | None = None) -> Any:
    enqueue_wakeup(
        db_path,
        agent_id=f"role:{role}",
        source="manual",
        reason=wake_reason or "assignment",
        payload={"issue_id": "issue:work", **({"wake_reason": wake_reason} if wake_reason else {})},
    )
    return HeartbeatScheduler(db_path).dispatch_next(agent_id=f"role:{role}")


def _run_status(db_path: Path, run_id: str) -> tuple[str, str]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, error_code FROM runs WHERE id = ?", (run_id,)).fetchone()
    return str(row["status"]), str(row["error_code"] or "")


def test_terminal_issue_wake_is_skipped(tmp_path: Path) -> None:
    db_path = _init(tmp_path, issue_status="done")
    runtime = _CountingRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))

    dispatch = _dispatch(db_path)
    executor.execute(dispatch)

    assert runtime.calls == 0
    assert _run_status(db_path, dispatch.run["id"]) == ("skipped", "issue_terminal")
    # silent: no escalation created
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM issue_thread_interactions").fetchone()[0] == 0


def test_pending_product_decision_pauses_children(tmp_path: Path) -> None:
    db_path = _init(tmp_path)
    create_interaction(
        db_path,
        issue_id="issue:root",
        kind="request_confirmation",
        payload={"reason": "decide_prototype_close", "options": ["A", "B", "C"]},
    )
    runtime = _CountingRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))

    dispatch = _dispatch(db_path)
    executor.execute(dispatch)

    assert runtime.calls == 0
    assert _run_status(db_path, dispatch.run["id"]) == ("skipped", "awaiting_user_decision")


def test_pending_operational_escalation_does_not_pause(tmp_path: Path) -> None:
    db_path = _init(tmp_path)
    create_interaction(
        db_path,
        issue_id="issue:root",
        kind="request_confirmation",
        payload={"reason": "cost_breaker_tripped"},
    )
    runtime = _CountingRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))

    dispatch = _dispatch(db_path)
    executor.execute(dispatch)

    assert runtime.calls == 1


def test_review_evidence_unchanged_skips_duplicate_review(tmp_path: Path) -> None:
    db_path = _init(tmp_path, role="reviewer")
    runtime = _CountingRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))

    first = _dispatch(db_path, role="reviewer")
    executor.execute(first)
    assert runtime.calls == 1

    # Same workspace → duplicate review skipped silently
    second = _dispatch(db_path, role="reviewer")
    executor.execute(second)
    assert runtime.calls == 1
    assert _run_status(db_path, second.run["id"]) == ("skipped", "review_evidence_unchanged")

    # Workspace changed → exactly one new round re-enabled
    target = db_path.parent.parent / "src" / "main.cs"
    target.write_text("class Main { int hp; }", encoding="utf-8")
    os.utime(target, (target.stat().st_atime + 5, target.stat().st_mtime + 5))
    third = _dispatch(db_path, role="reviewer")
    executor.execute(third)
    assert runtime.calls == 2


def test_engineer_not_affected_by_evidence_guard(tmp_path: Path) -> None:
    db_path = _init(tmp_path)
    runtime = _CountingRuntime()
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))

    executor.execute(_dispatch(db_path))
    executor.execute(_dispatch(db_path))

    assert runtime.calls == 2
