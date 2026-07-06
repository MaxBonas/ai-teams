from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aiteam.adapters.registry import AdapterRegistry
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.runs import append_run_event
from aiteam.db.interactions import resolve_interaction
from aiteam.heartbeat.executor import RunExecutor


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO goals (id, title) VALUES ('g1', 'G')"
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES ('role:lead', 'lead', 'Lead', 'openai_api')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) "
            "VALUES ('root', 'g1', 'Root', 'in_progress', 'lead', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role) "
            "VALUES ('child', 'g1', 'root', 'Child', 'in_progress', 'engineer')"
        )
        conn.commit()


def _insert_run(db_path: Path, run_id: str, *, issue_id: str, cost: int, created_at_sql: str = "datetime('now')") -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status, invocation_source, actual_cost_cents, created_at) "
            f"VALUES (?, 'role:lead', ?, 'completed', 'test', ?, {created_at_sql})",
            (run_id, issue_id, cost),
        )
        conn.commit()


def _breaker_interactions(db_path: Path) -> list[dict]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE idempotency_key LIKE 'cost_breaker:%' ORDER BY created_at"
        ).fetchall()
    return [dict(row) for row in rows]


@pytest.fixture()
def executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunExecutor:
    monkeypatch.setenv("AITEAM_COST_BREAKER_CENTS", "100")
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    return RunExecutor(db_path, AdapterRegistry([]))


def test_trips_when_spend_exceeds_threshold_without_progress(executor: RunExecutor) -> None:
    _insert_run(executor.db_path, "run:a", issue_id="root", cost=60)
    _insert_run(executor.db_path, "run:b", issue_id="child", cost=50)

    executor._check_cost_breaker(issue_id="child", run_id="run:b", agent_id="role:lead")

    interactions = _breaker_interactions(executor.db_path)
    assert len(interactions) == 1
    assert interactions[0]["issue_id"] == "root"  # escalated at the subtree root
    assert "110¢" in str(interactions[0]["title"])


def test_does_not_trip_below_threshold(executor: RunExecutor) -> None:
    _insert_run(executor.db_path, "run:a", issue_id="root", cost=99)

    executor._check_cost_breaker(issue_id="root", run_id="run:a", agent_id="role:lead")

    assert _breaker_interactions(executor.db_path) == []


def test_workspace_progress_resets_the_epoch(executor: RunExecutor) -> None:
    # Expensive runs BEFORE the progress event do not count against the epoch.
    _insert_run(executor.db_path, "run:old", issue_id="child", cost=500, created_at_sql="datetime('now', '-10 minutes')")
    append_run_event(
        executor.db_path, run_id="run:old", event_type="file_ops", stream="system",
        payload={"count": 2, "paths": ["a.py", "b.py"]},
    )
    _insert_run(executor.db_path, "run:new", issue_id="child", cost=40)

    executor._check_cost_breaker(issue_id="child", run_id="run:new", agent_id="role:lead")

    assert _breaker_interactions(executor.db_path) == []


def test_pending_breaker_does_not_duplicate(executor: RunExecutor) -> None:
    _insert_run(executor.db_path, "run:a", issue_id="root", cost=150)

    executor._check_cost_breaker(issue_id="root", run_id="run:a", agent_id="role:lead")
    executor._check_cost_breaker(issue_id="root", run_id="run:a", agent_id="role:lead")

    assert len(_breaker_interactions(executor.db_path)) == 1


def test_accept_resets_counter(executor: RunExecutor) -> None:
    _insert_run(executor.db_path, "run:a", issue_id="root", cost=150, created_at_sql="datetime('now', '-5 minutes')")
    executor._check_cost_breaker(issue_id="root", run_id="run:a", agent_id="role:lead")
    interaction = _breaker_interactions(executor.db_path)[0]
    resolve_interaction(
        executor.db_path, interaction_id=interaction["id"], action="accept", resolved_by_user_id="user",
    )

    # Small spend after the acceptance: no new trip.
    _insert_run(executor.db_path, "run:b", issue_id="root", cost=40)
    executor._check_cost_breaker(issue_id="root", run_id="run:b", agent_id="role:lead")

    assert len(_breaker_interactions(executor.db_path)) == 1


def test_reject_cancels_open_children_once(executor: RunExecutor) -> None:
    _insert_run(executor.db_path, "run:a", issue_id="root", cost=150)
    executor._check_cost_breaker(issue_id="root", run_id="run:a", agent_id="role:lead")
    interaction = _breaker_interactions(executor.db_path)[0]
    resolve_interaction(
        executor.db_path, interaction_id=interaction["id"], action="reject", resolved_by_user_id="user",
    )

    executor._check_cost_breaker(issue_id="root", run_id="run:a", agent_id="role:lead")

    with sqlite3.connect(str(executor.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        child = conn.execute("SELECT status FROM issues WHERE id = 'child'").fetchone()
        applied = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'cost_breaker.children_cancelled'"
        ).fetchone()
    assert child["status"] == "cancelled"
    assert applied[0] == 1

    # Re-running the check neither duplicates the cancel nor re-trips.
    executor._check_cost_breaker(issue_id="root", run_id="run:a", agent_id="role:lead")
    with sqlite3.connect(str(executor.db_path)) as conn:
        applied2 = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'cost_breaker.children_cancelled'"
        ).fetchone()
    assert applied2[0] == 1
