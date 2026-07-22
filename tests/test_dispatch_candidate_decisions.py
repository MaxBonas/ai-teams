from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from aiteam.db.migration import SCHEMA_PATH
from aiteam.heartbeat.scheduler import (
    HeartbeatScheduler,
    plan_parallel_batch,
    plan_sequential_batch,
)


@contextmanager
def _init_db(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO goals (id, title) VALUES ('goal', 'Goal')")
    try:
        yield conn
    finally:
        conn.close()


def _agent(
    conn: sqlite3.Connection,
    agent_id: str,
    role: str,
    pool: str,
    *,
    adapter: str = "openai_api",
) -> None:
    conn.execute(
        """
        INSERT INTO agents (id, role, name, adapter_type, adapter_config_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (agent_id, role, agent_id, adapter, json.dumps({"capacity_pool": pool})),
    )


def _issue(
    conn: sqlite3.Connection,
    issue_id: str,
    agent_id: str,
    *,
    role: str = "lead",
) -> None:
    conn.execute(
        """
        INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)
        VALUES (?, 'goal', ?, 'in_progress', ?, ?)
        """,
        (issue_id, issue_id, role, agent_id),
    )


def _wakeup(
    conn: sqlite3.Connection,
    wakeup_id: str,
    agent_id: str,
    issue_id: str,
    ordinal: int,
) -> None:
    conn.execute(
        """
        INSERT INTO wakeup_requests (
            id, agent_id, source, reason, payload_json, requested_at
        ) VALUES (?, ?, 'test', 'test', ?, ?)
        """,
        (
            wakeup_id,
            agent_id,
            json.dumps({"issue_id": issue_id}),
            f"2026-07-22T10:00:{ordinal:02d}+00:00",
        ),
    )


def test_parallel_plan_persists_every_constraint_reason(tmp_path: Path) -> None:
    path = tmp_path / "decisions.db"
    with _init_db(path) as conn:
        for agent_id, role, pool in (
            ("a1", "lead", "pool-a"),
            ("a2", "file_scout", "pool-b"),
            ("a3", "file_scout", "pool-a"),
            ("a4", "engineer", "pool-c"),
            ("a5", "reviewer", "pool-d"),
        ):
            _agent(conn, agent_id, role, pool)
        conn.execute("UPDATE agents SET adapter_type = 'gemini_api' WHERE id = 'a3'")
        for issue_id, agent_id, role in (
            ("root-a", "a1", "lead"),
            ("root-b", "a1", "lead"),
            ("root-c", "a3", "file_scout"),
            ("root-d", "a4", "engineer"),
            ("root-e", "a5", "reviewer"),
        ):
            _issue(conn, issue_id, agent_id, role=role)
        for args in (
            ("w1", "a1", "root-a", 1),
            ("w2", "a1", "root-b", 2),
            ("w3", "a2", "root-a", 3),
            ("w4", "a3", "root-c", 4),
            ("w5", "a4", "root-d", 5),
            ("w6", "a5", "root-e", 6),
        ):
            _wakeup(conn, *args)
        conn.commit()

    plan = plan_parallel_batch(path, max_runs=4)

    assert plan.selected_wakeup_ids == ["w1", "w5"]
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT wakeup_request_id, decision, reason, capacity_pool,
                   is_work_slot, ready_at
            FROM dispatch_candidate_decisions
            WHERE batch_id = ? ORDER BY requested_at
            """,
            (plan.batch_id,),
        ).fetchall()
    by_id = {row["wakeup_request_id"]: dict(row) for row in rows}
    assert {key: row["reason"] for key, row in by_id.items()} == {
        "w1": "selected",
        "w2": "same_agent",
        "w3": "same_root_issue",
        "w4": "same_capacity_pool",
        "w5": "selected",
        "w6": "second_work_slot",
    }
    assert by_id["w1"]["capacity_pool"] == "pool-a"
    assert by_id["w5"]["is_work_slot"] == 1
    assert all(row["ready_at"] for row in by_id.values())

    second_plan = plan_parallel_batch(path, max_runs=4)
    with sqlite3.connect(str(path)) as conn:
        second_ready_at = conn.execute(
            """
            SELECT ready_at FROM dispatch_candidate_decisions
            WHERE batch_id = ? AND wakeup_request_id = 'w1'
            """,
            (second_plan.batch_id,),
        ).fetchone()[0]
    assert second_ready_at == by_id["w1"]["ready_at"]


def test_not_ready_candidates_persist_dependency_and_checkout(tmp_path: Path) -> None:
    path = tmp_path / "not-ready.db"
    with _init_db(path) as conn:
        _agent(conn, "dep-agent", "lead", "pool-a")
        _agent(conn, "checkout-agent", "file_scout", "pool-b")
        _issue(conn, "blocker", "dep-agent")
        _issue(conn, "dependent", "dep-agent")
        _issue(conn, "checked", "checkout-agent", role="file_scout")
        conn.execute(
            "INSERT INTO issue_dependencies (issue_id, depends_on_issue_id) VALUES ('dependent', 'blocker')"
        )
        conn.execute(
            """
            INSERT INTO runs (id, agent_id, status, adapter_type)
            VALUES ('active-checkout', 'checkout-agent', 'running', 'openai_api')
            """
        )
        conn.execute(
            "UPDATE issues SET checkout_run_id = 'active-checkout' WHERE id = 'checked'"
        )
        _wakeup(conn, "w-dependency", "dep-agent", "dependent", 1)
        _wakeup(conn, "w-checkout", "checkout-agent", "checked", 2)
        conn.commit()

    plan = plan_parallel_batch(path, max_runs=3)

    assert plan.selected_wakeup_ids == []
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        parallel = conn.execute(
            """
            SELECT wakeup_request_id, reason, ready_at, details_json
            FROM dispatch_candidate_decisions
            WHERE batch_id = ? ORDER BY requested_at
            """,
            (plan.batch_id,),
        ).fetchall()
    assert [row["reason"] for row in parallel] == ["dependency_blocked", "checkout_active"]
    assert all(row["ready_at"] is None for row in parallel)
    assert json.loads(parallel[0]["details_json"])["blockers"] == [
        {"issue_id": "blocker", "status": "in_progress"}
    ]
    assert json.loads(parallel[1]["details_json"])["checkout_run_id"] == "active-checkout"

    assert HeartbeatScheduler(path).dispatch_next() is None
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        wakeups = conn.execute(
            "SELECT id, status, error FROM wakeup_requests ORDER BY requested_at"
        ).fetchall()
        sequential = conn.execute(
            """
            SELECT reason FROM dispatch_candidate_decisions
            WHERE dispatch_mode = 'sequential' ORDER BY considered_at
            """
        ).fetchall()
    assert [tuple(row) for row in wakeups] == [
        ("w-dependency", "skipped", "issue_dependencies_blocked"),
        ("w-checkout", "skipped", "issue_checkout_active"),
    ]
    assert [row["reason"] for row in sequential] == ["dependency_blocked", "checkout_active"]


def test_sequential_selection_records_ready_provenance(tmp_path: Path) -> None:
    path = tmp_path / "sequential.db"
    with _init_db(path) as conn:
        _agent(conn, "lead", "lead", "lead-pool")
        _issue(conn, "root", "lead")
        _wakeup(conn, "w-ready", "lead", "root", 1)
        conn.commit()

    dispatch = HeartbeatScheduler(path).dispatch_next()

    assert dispatch is not None
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT dispatch_mode, decision, reason, root_issue_id,
                   capacity_pool, ready_at
            FROM dispatch_candidate_decisions
            """
        ).fetchone()
    assert {key: row[key] for key in (
        "dispatch_mode",
        "decision",
        "reason",
        "root_issue_id",
        "capacity_pool",
    )} == {
        "dispatch_mode": "sequential",
        "decision": "selected",
        "reason": "selected",
        "root_issue_id": "root",
        "capacity_pool": "lead-pool",
    }
    assert row["ready_at"] is not None


def test_sequential_plan_snapshots_ready_waiters_and_blockers(tmp_path: Path) -> None:
    path = tmp_path / "sequential-full-queue.db"
    with _init_db(path) as conn:
        for agent_id, pool in (("a1", "pool-a"), ("a2", "pool-b"), ("a3", "pool-c")):
            _agent(conn, agent_id, "file_scout", pool)
            _issue(conn, f"root-{agent_id}", agent_id, role="file_scout")
        conn.execute(
            "INSERT INTO issue_dependencies (issue_id, depends_on_issue_id) "
            "VALUES ('root-a3', 'root-a1')"
        )
        _wakeup(conn, "w1", "a1", "root-a1", 1)
        _wakeup(conn, "w2", "a2", "root-a2", 2)
        _wakeup(conn, "w3", "a3", "root-a3", 3)
        conn.commit()

    plan = plan_sequential_batch(path)

    assert plan.selected_wakeup_ids == ["w1"]
    assert {
        item["wakeup_id"]: (item["decision"], item["reason"])
        for item in plan.decisions
    } == {
        "w1": ("selected", "selected"),
        "w2": ("rejected", "sequential_mode"),
        "w3": ("rejected", "dependency_blocked"),
    }
    with sqlite3.connect(str(path)) as conn:
        rows = conn.execute(
            "SELECT wakeup_request_id, reason, ready_at "
            "FROM dispatch_candidate_decisions WHERE batch_id = ? ORDER BY requested_at",
            (plan.batch_id,),
        ).fetchall()
    assert [row[1] for row in rows] == ["selected", "sequential_mode", "dependency_blocked"]
    assert rows[0][2] is not None
    assert rows[1][2] == rows[0][2]
    assert rows[2][2] is None
