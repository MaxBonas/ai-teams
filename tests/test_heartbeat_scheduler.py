from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.scheduler import HeartbeatScheduler


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.executemany(
            """
            INSERT INTO agents
                (id, role, name, heartbeat_interval_sec, last_heartbeat_at, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("role:team_lead", "team_lead", "Team Lead", 60, None, "active"),
                (
                    "role:engineer",
                    "engineer",
                    "Engineer",
                    60,
                    "2026-05-04T11:59:30+00:00",
                    "active",
                ),
                ("role:qa", "qa", "QA", 0, None, "active"),
            ],
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, status, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("issue-1", "goal-1", "Implement", "todo", "role:engineer"),
        )
        conn.commit()


def test_tick_timers_enqueues_due_agents_and_updates_heartbeat(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    scheduler = HeartbeatScheduler(db_path)

    enqueued = scheduler.tick_timers("2026-05-04T12:00:00+00:00")

    assert len(enqueued) == 1
    assert enqueued[0]["agent_id"] == "role:team_lead"
    assert enqueued[0]["reason"] == "timer"
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        lead = conn.execute(
            "SELECT last_heartbeat_at FROM agents WHERE id = ?",
            ("role:team_lead",),
        ).fetchone()
        wakeups = conn.execute("SELECT COUNT(*) FROM wakeup_requests").fetchone()[0]
    assert lead["last_heartbeat_at"] == "2026-05-04T12:00:00+00:00"
    assert wakeups == 1


def test_tick_timers_coalesces_within_same_timer_bucket(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    scheduler = HeartbeatScheduler(db_path)

    first = scheduler.tick_timers("2026-05-04T12:00:00+00:00")
    second = scheduler.tick_timers("2026-05-04T12:00:10+00:00")

    assert len(first) == 1
    assert second == []
    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM wakeup_requests").fetchone()[0]
    assert count == 1


def test_dispatch_next_claims_wakeup_and_creates_run_with_wake_context(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    enqueue_wakeup(
        db_path,
        wakeup_id="wakeup-1",
        agent_id="role:engineer",
        source="assignment",
        reason="assignment",
        trigger_detail="new_issue",
        payload={
            "issue_id": "issue-1",
            "profile": "full_team",
            "delegation_reason": "bounded implementation",
            "complexity": "medium",
            "estimated_cost_cents": 4,
            "estimated_savings_cents": 12,
        },
    )
    scheduler = HeartbeatScheduler(db_path)

    result = scheduler.dispatch_next(agent_id="role:engineer")

    assert result is not None
    assert result.wakeup_request["status"] == "running"
    assert result.wakeup_request["run_id"] == result.run["id"]
    assert result.run["agent_id"] == "role:engineer"
    assert result.run["issue_id"] == "issue-1"
    assert result.run["profile"] == "full_team"
    assert result.run["invocation_source"] == "assignment"
    assert result.run["delegation_reason"] == "bounded implementation"
    assert result.run["estimated_cost_cents"] == 4
    assert result.run["estimated_savings_cents"] == 12
    assert json.loads(result.run["context_snapshot_json"]) == {
        "issue_id": "issue-1",
        "wake_reason": "assignment",
        "wake_source": "assignment",
        "wakeup_request_id": "wakeup-1",
    }

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        wakeup = conn.execute(
            "SELECT status, run_id FROM wakeup_requests WHERE id = ?",
            ("wakeup-1",),
        ).fetchone()
    assert wakeup["status"] == "running"
    assert wakeup["run_id"] == result.run["id"]


def test_dispatch_next_returns_none_when_queue_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    assert HeartbeatScheduler(db_path).dispatch_next() is None
