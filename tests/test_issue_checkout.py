from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from aiteam.db.issues import checkout_issue
from aiteam.db.migration import SCHEMA_PATH


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO goals (id, title) VALUES (?, ?)",
            ("goal-1", "Goal"),
        )
        conn.executemany(
            "INSERT INTO agents (id, role, name) VALUES (?, ?, ?)",
            [
                ("role:team_lead", "team_lead", "Team Lead"),
                ("role:engineer", "engineer", "Engineer"),
                ("role:reviewer", "reviewer", "Reviewer"),
            ],
        )
        conn.execute(
            """
            INSERT INTO issues
                (id, goal_id, title, status, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("issue-1", "goal-1", "Implement", "todo", None),
        )
        conn.executemany(
            """
            INSERT INTO runs
                (id, agent_id, issue_id, invocation_source, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("run-eng", "role:engineer", "issue-1", "manual", "queued"),
                ("run-review", "role:reviewer", "issue-1", "manual", "queued"),
            ],
        )
        conn.commit()


def test_checkout_issue_claims_with_conditional_update(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    row = checkout_issue(
        db_path,
        issue_id="issue-1",
        agent_id="role:engineer",
        expected_statuses=["todo"],
        run_id="run-eng",
        locked_at="2026-05-04T12:00:00+00:00",
    )

    assert row is not None
    assert row["status"] == "in_progress"
    assert row["assignee_agent_id"] == "role:engineer"
    assert row["checkout_run_id"] == "run-eng"
    assert row["execution_run_id"] == "run-eng"
    assert row["execution_locked_at"] == "2026-05-04T12:00:00+00:00"


def test_checkout_issue_returns_none_on_conflict(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    first = checkout_issue(
        db_path,
        issue_id="issue-1",
        agent_id="role:engineer",
        expected_statuses=["todo"],
        run_id="run-eng",
    )
    second = checkout_issue(
        db_path,
        issue_id="issue-1",
        agent_id="role:reviewer",
        expected_statuses=["todo"],
        run_id="run-review",
    )

    assert first is not None
    assert second is None


def test_checkout_issue_is_idempotent_for_same_agent_and_run(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    first = checkout_issue(
        db_path,
        issue_id="issue-1",
        agent_id="role:engineer",
        expected_statuses=["todo"],
        run_id="run-eng",
    )
    second = checkout_issue(
        db_path,
        issue_id="issue-1",
        agent_id="role:engineer",
        expected_statuses=["in_progress"],
        run_id="run-eng",
    )

    assert first is not None
    assert second is not None
    assert second["assignee_agent_id"] == "role:engineer"
    assert second["checkout_run_id"] == "run-eng"


def test_checkout_issue_allows_only_one_concurrent_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    barrier = threading.Barrier(2)
    results: list[dict[str, object] | None] = []
    lock = threading.Lock()

    def _claim(agent_id: str, run_id: str) -> None:
        barrier.wait()
        result = checkout_issue(
            db_path,
            issue_id="issue-1",
            agent_id=agent_id,
            expected_statuses=["todo"],
            run_id=run_id,
        )
        with lock:
            results.append(result)

    threads = [
        threading.Thread(target=_claim, args=("role:engineer", "run-eng")),
        threading.Thread(target=_claim, args=("role:reviewer", "run-review")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 2
    assert sum(result is not None for result in results) == 1
    assert sum(result is None for result in results) == 1
