from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from aiteam.db.finops import check_budget, record_cost
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.runs import create_run, finish_run, mark_run_running


def _init_db(db_path: Path, *, budget_monthly_cents: int = 100) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.execute(
            """
            INSERT INTO agents (id, role, name, budget_monthly_cents)
            VALUES (?, ?, ?, ?)
            """,
            ("agent-1", "engineer", "Engineer", budget_monthly_cents),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) VALUES (?, ?, ?, ?, ?)",
            ("issue-1", "goal-1", "Implement", "todo", "agent-1"),
        )
        conn.commit()


def _finished_run(db_path: Path, *, run_id: str = "run-1") -> None:
    create_run(
        db_path,
        run_id=run_id,
        agent_id="agent-1",
        issue_id="issue-1",
        provider="openai",
        model="configured",
        channel="api",
        estimated_savings_cents=20,
    )
    mark_run_running(db_path, run_id=run_id)
    finish_run(
        db_path,
        run_id=run_id,
        status="completed",
        usage={"input_tokens": 100, "output_tokens": 25},
        actual_cost_cents=35,
    )


def test_record_cost_inserts_cost_event_and_updates_agent_monthly_spend(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    _finished_run(db_path)

    row = record_cost(
        db_path,
        run_id="run-1",
        agent_id="agent-1",
        amount_cents=35,
        period="2026-05",
    )

    assert row["run_id"] == "run-1"
    assert row["agent_id"] == "agent-1"
    assert row["issue_id"] == "issue-1"
    assert row["cost_cents"] == 35
    assert row["period"] == "2026-05"
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 25
    assert row["estimated_savings_cents"] == 20
    assert json.loads(row["metadata_json"]) == {}
    with sqlite3.connect(str(db_path)) as conn:
        spent = conn.execute(
            "SELECT spent_monthly_cents FROM agents WHERE id = ?",
            ("agent-1",),
        ).fetchone()[0]
    assert spent == 35


def test_record_cost_is_idempotent_per_run(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    _finished_run(db_path)

    first = record_cost(db_path, run_id="run-1", agent_id="agent-1", amount_cents=35, period="2026-05")
    second = record_cost(db_path, run_id="run-1", agent_id="agent-1", amount_cents=99, period="2026-05")

    assert first["id"] == second["id"]
    assert second["cost_cents"] == 35


def test_check_budget_allows_unlimited_budget(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, budget_monthly_cents=0)
    _finished_run(db_path)
    record_cost(db_path, run_id="run-1", agent_id="agent-1", amount_cents=999, period="2026-05")

    status = check_budget(db_path, agent_id="agent-1", period="2026-05")

    assert status.allowed is True
    assert status.reason == "budget_unlimited"
    assert status.spent_cents == 999


def test_check_budget_reports_available_and_exceeded_states(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, budget_monthly_cents=100)
    _finished_run(db_path)
    record_cost(db_path, run_id="run-1", agent_id="agent-1", amount_cents=35, period="2026-05")

    available = check_budget(db_path, agent_id="agent-1", period="2026-05")
    assert available.allowed is True
    assert available.remaining_cents == 65
    assert available.reason == "budget_available"

    _finished_run(db_path, run_id="run-2")
    record_cost(db_path, run_id="run-2", agent_id="agent-1", amount_cents=65, period="2026-05")
    exceeded = check_budget(db_path, agent_id="agent-1", period="2026-05")
    assert exceeded.allowed is False
    assert exceeded.exceeded is True
    assert exceeded.spent_cents == 100
    assert exceeded.reason == "budget_exceeded"


# ── soft threshold ─────────────────────────────────────────────────────────────

def test_check_budget_near_limit_below_threshold(tmp_path: Path) -> None:
    """Below 80 % → near_limit is False."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, budget_monthly_cents=100)
    _finished_run(db_path)
    record_cost(db_path, run_id="run-1", agent_id="agent-1", amount_cents=50, period="2026-05")

    status = check_budget(db_path, agent_id="agent-1", period="2026-05")

    assert status.near_limit is False
    assert status.exceeded is False
    assert status.reason == "budget_available"


def test_check_budget_near_limit_at_threshold(tmp_path: Path) -> None:
    """Exactly at 80 % → near_limit is True, but not exceeded."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, budget_monthly_cents=100)
    _finished_run(db_path)
    record_cost(db_path, run_id="run-1", agent_id="agent-1", amount_cents=80, period="2026-05")

    status = check_budget(db_path, agent_id="agent-1", period="2026-05")

    assert status.near_limit is True
    assert status.exceeded is False
    assert status.allowed is True
    assert status.reason == "budget_near_limit"


def test_check_budget_near_limit_above_threshold_not_exceeded(tmp_path: Path) -> None:
    """Between 80 % and 100 % → near_limit is True."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, budget_monthly_cents=100)
    _finished_run(db_path)
    record_cost(db_path, run_id="run-1", agent_id="agent-1", amount_cents=95, period="2026-05")

    status = check_budget(db_path, agent_id="agent-1", period="2026-05")

    assert status.near_limit is True
    assert status.exceeded is False
    assert status.reason == "budget_near_limit"


def test_check_budget_exceeded_not_near_limit(tmp_path: Path) -> None:
    """When exceeded, near_limit must be False (exceeded takes priority)."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, budget_monthly_cents=100)
    _finished_run(db_path)
    record_cost(db_path, run_id="run-1", agent_id="agent-1", amount_cents=100, period="2026-05")

    status = check_budget(db_path, agent_id="agent-1", period="2026-05")

    assert status.exceeded is True
    assert status.near_limit is False
    assert status.reason == "budget_exceeded"


def test_check_budget_unlimited_never_near_limit(tmp_path: Path) -> None:
    """Unlimited budget (0) → near_limit is always False."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, budget_monthly_cents=0)
    _finished_run(db_path)
    record_cost(db_path, run_id="run-1", agent_id="agent-1", amount_cents=9999, period="2026-05")

    status = check_budget(db_path, agent_id="agent-1", period="2026-05")

    assert status.near_limit is False
    assert status.reason == "budget_unlimited"


def test_budget_status_to_dict_includes_near_limit(tmp_path: Path) -> None:
    """to_dict() must include near_limit key."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, budget_monthly_cents=100)
    _finished_run(db_path)
    record_cost(db_path, run_id="run-1", agent_id="agent-1", amount_cents=85, period="2026-05")

    d = check_budget(db_path, agent_id="agent-1", period="2026-05").to_dict()

    assert "near_limit" in d
    assert d["near_limit"] is True
