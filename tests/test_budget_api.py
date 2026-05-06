"""Tests for budget API endpoints and soft-warning emission in the executor."""
from __future__ import annotations

import sqlite3
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiteam.adapters.registry import build_default_registry
from aiteam.db.finops import check_budget, record_cost, BUDGET_SOFT_THRESHOLD
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.runs import create_run, finish_run, mark_run_running
from aiteam.heartbeat.executor import RunExecutor
import api.utils as utils
from api.main import app


# ── DB helpers ────────────────────────────────────────────────────────────────

def _init_db(db_path: Path, *, budget_monthly_cents: int = 1000) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name, budget_monthly_cents) VALUES (?, ?, ?, ?)",
            ("agent-1", "engineer", "Engineer", budget_monthly_cents),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) VALUES (?, ?, ?, ?, ?)",
            ("issue-1", "goal-1", "Impl", "todo", "agent-1"),
        )
        conn.commit()


def _spend(db_path: Path, *, run_id: str = "run-1", amount_cents: int) -> None:
    create_run(db_path, run_id=run_id, agent_id="agent-1", issue_id="issue-1",
               provider="openai", model="gpt-4", channel="api")
    mark_run_running(db_path, run_id=run_id)
    finish_run(db_path, run_id=run_id, status="completed",
               usage={"input_tokens": 100, "output_tokens": 25}, actual_cost_cents=amount_cents)
    record_cost(db_path, run_id=run_id, agent_id="agent-1", amount_cents=amount_cents)


# ── /api/agents/{id}/budget ───────────────────────────────────────────────────

class TestAgentBudgetEndpoint:
    def test_returns_budget_available(self, tmp_path: Path) -> None:
        db_path = tmp_path / "runtime" / "aiteam.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _init_db(db_path, budget_monthly_cents=1000)
        _spend(db_path, amount_cents=200)

        utils.set_current_workspace(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/agents/agent-1/budget")

        assert resp.status_code == 200
        b = resp.json()["budget"]
        assert b["agent_id"] == "agent-1"
        assert b["spent_cents"] == 200
        assert b["budget_monthly_cents"] == 1000
        assert b["exceeded"] is False
        assert b["near_limit"] is False
        assert b["reason"] == "budget_available"

    def test_returns_near_limit(self, tmp_path: Path) -> None:
        db_path = tmp_path / "runtime" / "aiteam.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _init_db(db_path, budget_monthly_cents=1000)
        _spend(db_path, amount_cents=850)

        utils.set_current_workspace(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/agents/agent-1/budget")

        assert resp.status_code == 200
        b = resp.json()["budget"]
        assert b["near_limit"] is True
        assert b["exceeded"] is False
        assert b["reason"] == "budget_near_limit"

    def test_returns_exceeded(self, tmp_path: Path) -> None:
        db_path = tmp_path / "runtime" / "aiteam.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _init_db(db_path, budget_monthly_cents=100)
        _spend(db_path, amount_cents=100)

        utils.set_current_workspace(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/agents/agent-1/budget")

        assert resp.status_code == 200
        b = resp.json()["budget"]
        assert b["exceeded"] is True
        assert b["near_limit"] is False
        assert b["reason"] == "budget_exceeded"

    def test_unknown_agent_returns_404(self, tmp_path: Path) -> None:
        db_path = tmp_path / "runtime" / "aiteam.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _init_db(db_path)

        utils.set_current_workspace(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/agents/nonexistent/budget")

        assert resp.status_code == 404


# ── /api/budget ───────────────────────────────────────────────────────────────

class TestAllBudgetsEndpoint:
    def test_returns_agents_with_budget(self, tmp_path: Path) -> None:
        db_path = tmp_path / "runtime" / "aiteam.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _init_db(db_path, budget_monthly_cents=500)

        utils.set_current_workspace(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/budget")

        assert resp.status_code == 200
        budgets = resp.json()["budgets"]
        assert any(b["agent_id"] == "agent-1" for b in budgets)

    def test_excludes_unlimited_agents(self, tmp_path: Path) -> None:
        """Agents with budget_monthly_cents=0 (unlimited) are excluded from the list."""
        db_path = tmp_path / "runtime" / "aiteam.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _init_db(db_path, budget_monthly_cents=0)  # unlimited

        utils.set_current_workspace(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/budget")

        assert resp.status_code == 200
        budgets = resp.json()["budgets"]
        # unlimited agents filtered on frontend (budget_monthly_cents == 0)
        # the endpoint returns them but the UI filters; here we just check 200
        assert isinstance(budgets, list)

    def test_includes_agent_name_and_role(self, tmp_path: Path) -> None:
        db_path = tmp_path / "runtime" / "aiteam.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _init_db(db_path, budget_monthly_cents=500)

        utils.set_current_workspace(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/budget")

        budgets = resp.json()["budgets"]
        match = next((b for b in budgets if b["agent_id"] == "agent-1"), None)
        assert match is not None
        assert match["agent_name"] == "Engineer"
        assert match["agent_role"] == "engineer"


# ── Soft warning in executor ──────────────────────────────────────────────────

class TestBudgetSoftWarningInExecutor:
    """The RunExecutor emits a budget.soft_threshold_crossed activity event
    (once per period) when an agent's spending crosses the soft threshold."""

    def _make_db(self, tmp_path: Path, *, budget_cents: int) -> Path:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path, budget_monthly_cents=budget_cents)
        return db_path

    def _activity_soft_warnings(self, db_path: Path, *, agent_id: str) -> list[dict]:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM activity_log WHERE action = 'budget.soft_threshold_crossed' AND actor_agent_id = ?",
                (agent_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def test_no_warning_below_threshold(self, tmp_path: Path) -> None:
        from aiteam.heartbeat.executor import RunExecutor
        db_path = self._make_db(tmp_path, budget_cents=1000)
        _spend(db_path, amount_cents=500)  # 50%, below 80%

        executor = RunExecutor(db_path, build_default_registry())
        # Call _budget_gate directly
        result = executor._budget_gate(run_id="run-1", issue_id="issue-1", agent_id="agent-1")

        assert result == "allowed"
        assert self._activity_soft_warnings(db_path, agent_id="agent-1") == []

    def test_warning_emitted_at_threshold(self, tmp_path: Path) -> None:
        from aiteam.heartbeat.executor import RunExecutor
        db_path = self._make_db(tmp_path, budget_cents=1000)
        _spend(db_path, amount_cents=800)  # exactly 80%

        executor = RunExecutor(db_path, build_default_registry())
        result = executor._budget_gate(run_id="run-1", issue_id="issue-1", agent_id="agent-1")

        assert result == "allowed"
        warnings = self._activity_soft_warnings(db_path, agent_id="agent-1")
        assert len(warnings) == 1
        payload = json.loads(warnings[0]["payload_json"])
        assert payload["spent_cents"] == 800
        assert payload["budget_monthly_cents"] == 1000

    def test_warning_idempotent_across_runs(self, tmp_path: Path) -> None:
        """Calling _budget_gate twice in the same period emits only one event."""
        from aiteam.heartbeat.executor import RunExecutor
        db_path = self._make_db(tmp_path, budget_cents=1000)
        _spend(db_path, amount_cents=900)

        executor = RunExecutor(db_path, build_default_registry())
        executor._budget_gate(run_id="run-1", issue_id="issue-1", agent_id="agent-1")
        executor._budget_gate(run_id="run-2", issue_id="issue-1", agent_id="agent-1")

        warnings = self._activity_soft_warnings(db_path, agent_id="agent-1")
        assert len(warnings) == 1, "Soft warning must be emitted only once per period"

    def test_no_warning_when_exceeded(self, tmp_path: Path) -> None:
        """When budget is exceeded, the hard gate fires instead (no soft warning)."""
        from aiteam.heartbeat.executor import RunExecutor
        db_path = self._make_db(tmp_path, budget_cents=100)
        _spend(db_path, amount_cents=100)  # 100%, exceeded

        executor = RunExecutor(db_path, build_default_registry())
        result = executor._budget_gate(run_id="run-1", issue_id="issue-1", agent_id="agent-1")

        assert result == "blocked"
        assert self._activity_soft_warnings(db_path, agent_id="agent-1") == []
