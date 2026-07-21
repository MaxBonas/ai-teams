from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.issues import router
from api.utils import get_current_workspace, set_current_workspace
from aiteam.db.migration import SCHEMA_PATH
from aiteam.subscription_quota import record_run_adapter_profile
from scripts.orchestrator_evals import evaluate_db


def test_loop_health_exposes_offline_eval_summary_additively(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = workspace / ".aiteam"
    runtime.mkdir(parents=True)
    db = runtime / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status) VALUES (?, ?, ?, ?)",
            ("issue-1", "goal-1", "Pending root", "todo"),
        )
        conn.commit()

    app = FastAPI()
    app.include_router(router)
    previous = get_current_workspace()
    set_current_workspace(workspace)
    try:
        response = TestClient(app).get("/api/loop-health")
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["orchestrator_evals"]["liveness"] == {
        "nonterminal_runs": 0,
        "stale_nonterminal_runs": 0,
        "claimed_or_running_wakeups": 0,
        "stale_claimed_or_running_wakeups": 0,
        "stranded_nonterminal_roots": 1,
        "healthy": False,
    }
    assert payload["orchestrator_evals"]["economy"]["total_tokens"] == 0
    assert payload["orchestrator_evals"]["quorum"]["available"] is True
    assert payload["summary"]["requires_attention"] is True


def test_loop_health_surfaces_observed_subscription_exhaustion(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = workspace / ".aiteam"
    runtime.mkdir(parents=True)
    db = runtime / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('role:lead', 'lead', 'Lead')")
        conn.execute(
            """
            INSERT INTO runs (
                id, agent_id, status, channel, provider, model, error_code,
                started_at, finished_at
            ) VALUES (
                'run-limit', 'role:lead', 'failed', 'subscription',
                'openai-codex', 'gpt-5.6-sol', 'subscription_cli_usage_limit',
                datetime('now', '-1 minute'), datetime('now')
            )
            """
        )
    record_run_adapter_profile(
        db,
        run_id="run-limit",
        profile_id="codex_subscription",
        provider="openai-codex",
        model="gpt-5.6-sol",
        channel="subscription",
    )

    app = FastAPI()
    app.include_router(router)
    previous = get_current_workspace()
    set_current_workspace(workspace)
    try:
        response = TestClient(app).get("/api/loop-health")
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    payload = response.json()
    assert payload["subscription_profiles_requiring_attention"] == ["codex_subscription"]
    assert payload["capacity_profiles_requiring_attention"] == ["codex_subscription"]
    assert payload["capacity_profiles"] == payload["subscription_quota"]
    assert payload["subscription_quota"][0]["state"] == "exhausted_observed"
    assert payload["subscription_quota"][0]["forecast"]["utilization"] is None
    assert payload["summary"]["requires_attention"] is True


def test_descendant_wakeup_keeps_nonterminal_root_live(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute(
            "INSERT INTO agents (id, role, name) VALUES ('role:engineer', 'engineer', 'Engineer')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status) VALUES ('root', 'goal-1', 'Root', 'in_progress')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status) "
            "VALUES ('child', 'goal-1', 'root', 'Child', 'todo')"
        )
        conn.execute(
            "INSERT INTO wakeup_requests "
            "(id, agent_id, source, reason, status, payload_json, idempotency_key) "
            "VALUES ('wake-child', 'role:engineer', 'test', 'new_issue', 'queued', "
            "'{\"issue_id\":\"child\"}', 'wake-child')"
        )
        conn.commit()

    report = evaluate_db(db)
    assert report["liveness"]["stranded_nonterminal_roots"] == 0
    assert report["liveness"]["healthy"] is True


def test_recent_running_work_is_live_but_stale_run_requires_attention(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = workspace / ".aiteam"
    runtime.mkdir(parents=True)
    db = runtime / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('role:lead', 'lead', 'Lead')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) "
            "VALUES ('root', 'goal-1', 'Root', 'in_progress', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status, started_at) "
            "VALUES ('run-live', 'role:lead', 'root', 'running', datetime('now'))"
        )
        conn.commit()

    app = FastAPI()
    app.include_router(router)
    previous = get_current_workspace()
    set_current_workspace(workspace)
    try:
        fresh = TestClient(app).get("/api/loop-health")
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE runs SET started_at=datetime('now', '-31 minutes') WHERE id='run-live'"
            )
            conn.commit()
        stale = TestClient(app).get("/api/loop-health")
    finally:
        set_current_workspace(previous)

    assert fresh.status_code == 200
    fresh_body = fresh.json()
    assert fresh_body["orchestrator_evals"]["liveness"]["nonterminal_runs"] == 1
    assert fresh_body["orchestrator_evals"]["liveness"]["stale_nonterminal_runs"] == 0
    assert fresh_body["summary"]["requires_attention"] is False

    assert stale.status_code == 200
    stale_body = stale.json()
    assert stale_body["orchestrator_evals"]["liveness"]["stale_nonterminal_runs"] == 1
    assert stale_body["summary"]["requires_attention"] is True


def test_stale_claimed_wakeup_is_unhealthy_but_recent_claim_is_not(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('role:lead', 'lead', 'Lead')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) "
            "VALUES ('root', 'goal-1', 'Root', 'in_progress', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO wakeup_requests "
            "(id, agent_id, source, reason, status, payload_json, idempotency_key, claimed_at) "
            "VALUES ('wake-live', 'role:lead', 'test', 'continue', 'claimed', "
            "'{\"issue_id\":\"root\"}', "
            "'wake-live', datetime('now'))"
        )
        conn.commit()

    fresh = evaluate_db(db)["liveness"]
    assert fresh["claimed_or_running_wakeups"] == 1
    assert fresh["stale_claimed_or_running_wakeups"] == 0
    assert fresh["healthy"] is True

    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE wakeup_requests SET claimed_at=datetime('now', '-31 minutes') "
            "WHERE id='wake-live'"
        )
        conn.commit()

    stale = evaluate_db(db)["liveness"]
    assert stale["stale_claimed_or_running_wakeups"] == 1
    assert stale["healthy"] is False


def test_descendant_interaction_keeps_root_live_but_open_child_alone_does_not(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status) VALUES ('root', 'goal-1', 'Root', 'in_progress')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status) "
            "VALUES ('child', 'goal-1', 'root', 'Child', 'blocked')"
        )
        conn.commit()

    assert evaluate_db(db)["liveness"]["stranded_nonterminal_roots"] == 1

    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO issue_thread_interactions "
            "(id, issue_id, kind, status, title, summary, idempotency_key) "
            "VALUES ('interaction-child', 'child', 'request_confirmation', 'pending', "
            "'Decision', 'Need owner input', 'interaction-child')"
        )
        conn.commit()

    report = evaluate_db(db)
    assert report["liveness"]["stranded_nonterminal_roots"] == 0
    assert report["liveness"]["healthy"] is True
