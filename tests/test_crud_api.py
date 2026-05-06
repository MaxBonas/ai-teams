from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.utils as utils
from aiteam.db.migration import SCHEMA_PATH
from api.main import app


def _setup_db(tmp_path: Path) -> Path:
    utils.set_current_workspace(tmp_path)
    db_path = tmp_path / "runtime" / "aiteam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return db_path


@pytest.fixture
def client(tmp_path):
    _setup_db(tmp_path)
    return TestClient(app, raise_server_exceptions=True)


# ── Goals ─────────────────────────────────────────────────────────────────────

def test_goal_crud(client):
    r = client.post("/api/goals", json={"title": "Ship v2"})
    assert r.status_code == 200
    goal_id = r.json()["goal"]["id"]

    r = client.get("/api/goals")
    assert r.status_code == 200
    assert any(g["id"] == goal_id for g in r.json()["goals"])

    r = client.get(f"/api/goals/{goal_id}")
    assert r.status_code == 200
    assert r.json()["goal"]["title"] == "Ship v2"

    r = client.get("/api/timeline")
    assert r.status_code == 200
    assert any(item["type"] == "activity" and item["title"] == "goal.created" for item in r.json()["items"])

    r = client.get("/api/goals/missing")
    assert r.status_code == 404


# ── Agents ────────────────────────────────────────────────────────────────────

def test_agent_crud(client):
    r = client.post("/api/agents", json={
        "role": "engineer",
        "name": "Bob",
        "adapter_type": "subscription_cli",
        "heartbeat_interval_sec": 30,
    })
    assert r.status_code == 200
    agent = r.json()["agent"]
    agent_id = agent["id"]
    assert agent["role"] == "engineer"

    r = client.get("/api/agents")
    assert r.status_code == 200
    assert any(a["id"] == agent_id for a in r.json()["agents"])

    r = client.get(f"/api/agents/{agent_id}")
    assert r.status_code == 200

    r = client.patch(f"/api/agents/{agent_id}", json={"status": "paused"})
    assert r.status_code == 200
    assert r.json()["agent"]["status"] == "paused"

    r = client.get("/api/timeline")
    assert r.status_code == 200
    activity_actions = [item["title"] for item in r.json()["items"] if item["type"] == "activity"]
    assert "agent.created" in activity_actions
    assert "agent.updated" in activity_actions

    r = client.get("/api/agents?role=engineer")
    assert r.status_code == 200
    assert len(r.json()["agents"]) == 1

    r = client.get("/api/agents/missing")
    assert r.status_code == 404


# ── Issues ────────────────────────────────────────────────────────────────────

def test_issue_crud(client):
    # Need a goal first
    goal_id = client.post("/api/goals", json={"title": "G"}).json()["goal"]["id"]

    r = client.post("/api/issues", json={
        "title": "Fix login bug",
        "goal_id": goal_id,
        "status": "todo",
        "priority": 5,
    })
    assert r.status_code == 200
    issue_id = r.json()["issue"]["id"]

    r = client.get("/api/issues")
    assert r.status_code == 200
    assert any(i["id"] == issue_id for i in r.json()["issues"])

    r = client.get(f"/api/issues/{issue_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["issue"]["title"] == "Fix login bug"
    assert "pending_interactions" in data

    r = client.patch(f"/api/issues/{issue_id}", json={"status": "in_progress", "complexity": "medium"})
    assert r.status_code == 200
    assert r.json()["issue"]["status"] == "in_progress"
    assert r.json()["issue"]["complexity"] == "medium"

    r = client.get(f"/api/timeline?issue_id={issue_id}")
    assert r.status_code == 200
    activity_actions = [item["title"] for item in r.json()["items"] if item["type"] == "activity"]
    assert set(activity_actions) == {"issue.created", "issue.updated"}

    r = client.get(f"/api/issues?goal_id={goal_id}")
    assert r.status_code == 200
    assert len(r.json()["issues"]) == 1

    r = client.get("/api/issues/missing")
    assert r.status_code == 404


def test_issue_get_includes_pending_interactions(client):
    goal_id = client.post("/api/goals", json={"title": "G"}).json()["goal"]["id"]
    issue_id = client.post("/api/issues", json={"title": "Deploy", "goal_id": goal_id}).json()["issue"]["id"]

    client.post(f"/api/issues/{issue_id}/interactions", json={
        "kind": "request_confirmation",
        "payload": {"message": "Deploy to prod?"},
    })

    r = client.get(f"/api/issues/{issue_id}")
    assert r.status_code == 200
    assert len(r.json()["pending_interactions"]) == 1
    assert r.json()["pending_interactions"][0]["kind"] == "request_confirmation"


# ── Runs list ─────────────────────────────────────────────────────────────────

def test_runs_list(client, tmp_path):
    db_path = tmp_path / "runtime" / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO agents (id, role, name) VALUES (?, ?, ?)", ("ag1", "lead", "Lead"))
        conn.execute(
            "INSERT INTO runs (id, agent_id, status) VALUES (?, ?, ?)",
            ("run-1", "ag1", "completed"),
        )
        conn.commit()

    r = client.get("/api/runs")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert any(run["id"] == "run-1" for run in runs)

    r = client.get("/api/runs?agent_id=ag1&status=completed")
    assert r.status_code == 200
    assert len(r.json()["runs"]) == 1

    r = client.get("/api/runs?status=failed")
    assert r.status_code == 200
    assert len(r.json()["runs"]) == 0


# ── activity_log ──────────────────────────────────────────────────────────────

def test_activity_log_write_and_list(tmp_path):
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    from aiteam.db.activity_log import log_activity, list_activity

    log_activity(db_path, action="issue.created", target_type="issue", target_id="iss-1",
                 actor_user_id="user-42", payload={"title": "Fix bug"})
    log_activity(db_path, action="issue.updated", target_type="issue", target_id="iss-1")

    all_entries = list_activity(db_path)
    assert len(all_entries) == 2

    filtered = list_activity(db_path, target_id="iss-1", target_type="issue")
    assert len(filtered) == 2

    actions = [e["action"] for e in filtered]
    assert "issue.created" in actions
    assert "issue.updated" in actions


def test_timeline_orders_project_events(client, tmp_path):
    db_path = tmp_path / "runtime" / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO agents (id, role, name) VALUES (?, ?, ?)", ("role:lead", "lead", "Lead"))
        conn.execute(
            """
            INSERT INTO issues (id, title, status, assignee_agent_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("issue-1", "Build timeline", "todo", "role:lead", "2026-05-04T10:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO issue_comments (id, issue_id, author_user_id, body, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("comment-1", "issue-1", "user", "Please do this", "2026-05-04T10:01:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO issue_thread_interactions (
                id, issue_id, kind, status, title, summary, created_by_agent_id, resolved_by_user_id,
                created_at, resolved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "interaction-1",
                "issue-1",
                "request_confirmation",
                "accepted",
                "Confirmar",
                "Aceptar plan",
                "role:lead",
                "user",
                "2026-05-04T10:02:00+00:00",
                "2026-05-04T10:03:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO runs (id, agent_id, issue_id, status, invocation_source, created_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                "role:lead",
                "issue-1",
                "completed",
                "manual",
                "2026-05-04T10:04:00+00:00",
                "2026-05-04T10:05:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO cost_events (id, run_id, agent_id, issue_id, cost_cents, period, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("cost-1", "run-1", "role:lead", "issue-1", 12, "2026-05", "2026-05-04T10:06:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO activity_log (id, run_id, actor_agent_id, action, target_type, target_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "activity-1",
                "run-1",
                "role:lead",
                "issue.updated",
                "issue",
                "issue-1",
                "2026-05-04T10:06:30+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO tool_access (id, run_id, agent_id, issue_id, tool_name, decision, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "tool-1",
                "run-1",
                "role:lead",
                "issue-1",
                "filesystem",
                "allowed",
                "read project files",
                "2026-05-04T10:07:00+00:00",
            ),
        )
        conn.commit()

    r = client.get("/api/timeline")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [item["id"] for item in items] == [
        "issue:issue-1",
        "comment:comment-1",
        "interaction-created:interaction-1",
        "interaction-resolved:interaction-1",
        "run:run-1",
        "cost:cost-1",
        "activity:activity-1",
        "tool:tool-1",
    ]

    r = client.get("/api/timeline?issue_id=issue-1&order=desc")
    assert r.status_code == 200
    assert r.json()["items"][0]["id"] == "tool:tool-1"

    r = client.get("/api/tool-access?issue_id=issue-1&decision=allowed")
    assert r.status_code == 200
    rows = r.json()["tool_access"]
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "filesystem"
