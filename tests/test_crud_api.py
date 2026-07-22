from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.utils as utils
from aiteam.db.migration import SCHEMA_PATH
from api.main import app
from aiteam.user_config import record_model_health


def _setup_db(tmp_path: Path) -> Path:
    utils.set_current_workspace(tmp_path)
    db_path = tmp_path / "runtime" / "aiteam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return db_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    for model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        record_model_health("openai_api", model, available=True, reason="test fixture")
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
        "adapter_type": "role_builtin",
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


def test_team_api_rejects_model_known_incompatible_with_installed_cli(
    client, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    import aiteam.user_config as config_mod

    monkeypatch.setattr(config_mod, "_codex_catalog_compatibility", lambda _config: {
        "status": "cli_update_required",
        "installed_version": "0.128.0",
        "catalog_client_version": "0.145.0",
        "models": ["gpt-5.6-luna"],
    })

    response = client.post("/api/agents", json={
        "role": "context_curator",
        "name": "Curator",
        "adapter_type": "subscription_cli",
        "adapter_config": {
            "profile_id": "codex_subscription",
            "model": "gpt-5.6-luna",
        },
    })

    assert response.status_code == 400
    assert "not executable" in response.json()["detail"]


def test_team_api_rejects_tier_3_model_for_lead(client) -> None:
    response = client.post("/api/agents", json={
        "role": "lead",
        "name": "Lead débil",
        "adapter_type": "openai_api",
        "adapter_config": {
            "profile_id": "openai_api",
            "model": "gpt-5.6-luna",
        },
        "run_profile": "full_team",
        "data_class": "internal",
    })

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "model_tier_insufficient"
    assert detail["alternatives"] == [{"value": "gpt-5.6-sol", "label": "GPT-5.6 Sol"}]


def test_team_api_rejects_incompatible_model_patch(client) -> None:
    created = client.post("/api/agents", json={
        "role": "lead",
        "name": "Lead",
        "adapter_type": "openai_api",
        "adapter_config": {
            "profile_id": "openai_api",
            "model": "gpt-5.6-sol",
        },
        "run_profile": "full_team",
        "data_class": "internal",
    })
    assert created.status_code == 200

    response = client.patch(f"/api/agents/{created.json()['agent']['id']}", json={
        "adapter_config": {
            "profile_id": "openai_api",
            "model": "gpt-5.6-luna",
        },
        "run_profile": "full_team",
        "data_class": "internal",
    })

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "model_tier_insufficient"


def test_agent_owner_selection_intent_survives_create_patch_and_reload(client) -> None:
    created = client.post("/api/agents", json={
        "role": "reviewer",
        "name": "Reviewer explícito",
        "adapter_type": "openai_api",
        "adapter_config": {
            "profile_id": "openai_api",
            "model": "gpt-5.6-terra",
        },
        "data_class": "internal",
    })
    assert created.status_code == 200
    agent_id = created.json()["agent"]["id"]
    initial = created.json()["agent"]["adapter_config"]
    assert initial["selection_intent"]["schema_version"] == "model_selection_intent_v1"
    assert initial["selection_intent"]["mode"] == "owner_explicit"
    assert initial["selection_intent"]["source"] == "agent_create_api"
    assert initial["selection_intent"]["candidate_id"]

    patched = client.patch(f"/api/agents/{agent_id}", json={
        "adapter_config": {
            "profile_id": "openai_api",
            "model": "gpt-5.6-terra",
            "model_reasoning_effort": "high",
        },
        "data_class": "internal",
    })
    assert patched.status_code == 200
    patched_config = patched.json()["agent"]["adapter_config"]
    assert patched_config["selection_intent"] == initial["selection_intent"]
    assert patched_config["model_reasoning_effort"] == "high"

    reloaded = client.get(f"/api/agents/{agent_id}")
    assert reloaded.status_code == 200
    assert reloaded.json()["agent"]["adapter_config"] == patched_config


def test_agent_api_rejects_selection_intent_for_another_candidate(client) -> None:
    response = client.post("/api/agents", json={
        "role": "reviewer",
        "name": "Reviewer inconsistente",
        "adapter_type": "openai_api",
        "adapter_config": {
            "profile_id": "openai_api",
            "model": "gpt-5.6-terra",
            "selection_intent": {
                "schema_version": "model_selection_intent_v1",
                "mode": "owner_explicit",
                "source": "model_role_selector",
                "candidate_id": "candidate:forged",
            },
        },
        "data_class": "internal",
    })

    assert response.status_code == 400
    assert "candidate_id does not match" in response.json()["detail"]


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


def test_issue_creation_auto_selects_and_persists_execution_profile(client):
    goal_id = client.post("/api/goals", json={"title": "G"}).json()["goal"]["id"]
    response = client.post(
        "/api/issues",
        json={
            "title": "Cambio acotado",
            "goal_id": goal_id,
            "criticality": "medium",
            "ambiguity": "low",
            "independent_verification": False,
            "parallel_workstreams": 1,
            "reversible": True,
            "run_profile": "auto",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["profile_selection"]["profile"] == "solo_lead"
    metadata = json.loads(payload["issue"]["metadata_json"])
    assert metadata["profile"] == "solo_lead"
    assert metadata["profile_selection"]["reason"] == "bounded_reversible_single_agent_work"


def test_issue_creation_defaults_to_team_and_honours_quorum_override(client):
    goal_id = client.post("/api/goals", json={"title": "G"}).json()["goal"]["id"]
    conservative = client.post(
        "/api/issues",
        json={"title": "Sin señales", "goal_id": goal_id},
    )
    planning = client.post(
        "/api/issues",
        json={"title": "Solo plan", "goal_id": goal_id, "run_profile": "lead_quorum"},
    )
    assert conservative.json()["profile_selection"]["profile"] == "full_team"
    assert conservative.json()["profile_selection"]["reason"] == "incomplete_signals_use_safe_team_default"
    assert planning.json()["profile_selection"]["profile"] == "lead_quorum"
    assert planning.json()["profile_selection"]["source"] == "explicit_override"


def test_issue_creation_only_inherits_final_plan_from_accepted_quorum(client, tmp_path):
    goal_id = client.post("/api/goals", json={"title": "G"}).json()["goal"]["id"]
    source_issue_id = client.post(
        "/api/issues", json={"title": "Planificar", "goal_id": goal_id, "run_profile": "lead_quorum"}
    ).json()["issue"]["id"]
    db_path = tmp_path / ".aiteam" / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO issue_documents "
            "(id, issue_id, key, title, body, current_revision_id, revision_number) "
            "VALUES ('doc:plan-source', ?, 'plan', 'Plan B', 'Plan aceptado', 'rev:accepted', 2)",
            (source_issue_id,),
        )
        conn.execute(
            "INSERT INTO issue_document_revisions "
            "(id, document_id, issue_id, key, title, body, revision_number) "
            "VALUES ('rev:accepted', 'doc:plan-source', ?, 'plan', 'Plan B', 'Plan aceptado', 2)",
            (source_issue_id,),
        )
        conn.execute(
            "INSERT INTO quorum_sessions "
            "(id, issue_id, base_plan_revision_id, status, final_plan_revision_id) "
            "VALUES ('qs:accepted', ?, 'rev:a', 'accepted', 'rev:accepted')",
            (source_issue_id,),
        )
        conn.commit()

    accepted = client.post(
        "/api/issues",
        json={
            "title": "Ejecutar",
            "goal_id": goal_id,
            "run_profile": "full_team",
            "metadata": {"source_plan_revision_id": "rev:accepted"},
        },
    )
    assert accepted.status_code == 200, accepted.text
    metadata = json.loads(accepted.json()["issue"]["metadata_json"])
    assert metadata["source_plan_issue_id"] == source_issue_id
    assert metadata["source_quorum_session_id"] == "qs:accepted"
    assert metadata["source_plan_status"] == "accepted"

    rejected = client.post(
        "/api/issues",
        json={
            "title": "Ejecutar borrador",
            "goal_id": goal_id,
            "run_profile": "full_team",
            "metadata": {"source_plan_revision_id": "rev:a"},
        },
    )
    assert rejected.status_code == 400
    assert "accepted quorum" in rejected.json()["detail"]


def test_issue_quorum_endpoint_is_read_only_and_returns_contract(client, tmp_path):
    goal_id = client.post("/api/goals", json={"title": "G"}).json()["goal"]["id"]
    issue_id = client.post(
        "/api/issues",
        json={"title": "Plan", "goal_id": goal_id, "run_profile": "lead_quorum"},
    ).json()["issue"]["id"]
    db_path = tmp_path / ".aiteam" / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO quorum_sessions "
            "(id,issue_id,base_plan_revision_id,status,requested_contributions,min_valid_contributions) "
            "VALUES ('qs',?,'rev-a','reviewing',2,2)",
            (issue_id,),
        )
        for ordinal, (agent, provider) in enumerate(
            (("role:quorum_auditor_1", "openai"), ("role:quorum_auditor_2", "google")),
            start=1,
        ):
            conn.execute(
                "INSERT INTO quorum_contributions "
                "(id,session_id,agent_id,ordinal,provider,model,channel,result,evidence,findings_json,valid) "
                "VALUES (?,?,?, ?,?,'senior','api','approved','e','[]',1)",
                (f"qc{ordinal}", "qs", agent, ordinal, provider),
            )
        conn.commit()

    response = client.get(f"/api/issues/{issue_id}/quorum")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["session"] == {
        "id": "qs",
        "issue_id": issue_id,
        "status": "reviewing",
        "requested_contributions": 2,
        "min_valid_contributions": 2,
        "skipped_reason": None,
        "final_plan_revision_id": None,
    }
    assert [row["ordinal"] for row in payload["contributions"]] == [1, 2]
    assert payload["gate"]["ready"] is True
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT status FROM quorum_sessions WHERE id='qs'").fetchone()[0] == "reviewing"

    without_session = client.post(
        "/api/issues", json={"title": "Normal", "goal_id": goal_id}
    ).json()["issue"]["id"]
    missing = client.get(f"/api/issues/{without_session}/quorum")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Quorum session not found"


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
