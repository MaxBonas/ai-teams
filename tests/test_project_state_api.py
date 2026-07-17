from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.project_state import router
from api.utils import get_current_workspace, set_current_workspace
from aiteam.db.migration import SCHEMA_PATH


def _client_for_workspace(workspace: Path) -> tuple[TestClient, Path]:
    app = FastAPI()
    app.include_router(router)
    previous = get_current_workspace()
    set_current_workspace(workspace)
    return TestClient(app), previous


def _init_db(workspace: Path) -> None:
    runtime_dir = workspace / ".aiteam"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type)"
            " VALUES ('role:lead', 'lead', 'Lead', 'lead', 'manual')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:intake', 'goal-1', 'Build', 'in_progress', 'lead', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, invocation_source, status)"
            " VALUES ('run-1', 'role:lead', 'issue:intake', 'manual', 'completed')"
        )
        conn.execute(
            "INSERT INTO issue_comments (id, issue_id, author_agent_id, body)"
            " VALUES ('comment-1', 'issue:intake', 'role:lead', 'Plan ready')"
        )
        conn.execute(
            """
            INSERT INTO issue_thread_interactions
                (id, issue_id, kind, status, continuation_policy, payload_json, title, summary)
            VALUES
                ('interaction-1', 'issue:intake', 'request_confirmation', 'pending',
                 'wake_assignee', '{}', 'Cerrar ciclo', 'Confirmar cierre')
            """
        )
        conn.execute(
            """
            INSERT INTO issue_documents
                (id, issue_id, key, title, format, body, current_revision_id, revision_number)
            VALUES
                ('doc-1', 'issue:intake', 'plan', 'Plan', 'markdown', '## Plan', 'rev-1', 1)
            """
        )
        conn.commit()


def test_project_state_comments_cap_keeps_newest(tmp_path: Path) -> None:
    """The payload cap must keep the NEWEST comments (the chat-feed bug —
    oldest-first + LIMIT — froze the window once the thread outgrew it)."""
    from api.routers import project_state as ps_mod

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _init_db(workspace)
    db_path = workspace / ".aiteam" / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        for i in range(10):
            conn.execute(
                "INSERT INTO issue_comments (id, issue_id, author_agent_id, body, created_at)"
                " VALUES (?, 'issue:intake', 'role:lead', ?, ?)",
                (f"c{i:03d}", f"msg {i}", f"2027-01-01 00:{i:02d}:00"),
            )
        conn.commit()

    client, previous = _client_for_workspace(workspace)
    try:
        original_cap = ps_mod._COMMENTS_CAP
        ps_mod._COMMENTS_CAP = 5
        try:
            response = client.get("/api/project/state")
        finally:
            ps_mod._COMMENTS_CAP = original_cap
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    bodies = [c["body"] for c in response.json()["comments"]]
    assert len(bodies) == 5
    assert bodies == [f"msg {i}" for i in range(5, 10)]  # newest, chronological


def test_project_state_returns_cockpit_snapshot(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _init_db(workspace)
    client, previous = _client_for_workspace(workspace)
    try:
        response = client.get("/api/project/state?selected_issue_id=issue%3Aintake")
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["configured"] is True
    assert payload["selected_issue_id"] == "issue:intake"
    assert [issue["id"] for issue in payload["issues"]] == ["issue:intake"]
    assert [agent["id"] for agent in payload["agents"]] == ["role:lead"]
    assert [run["id"] for run in payload["runs"]] == ["run-1"]
    assert [comment["id"] for comment in payload["comments"]] == ["comment-1"]
    assert [interaction["id"] for interaction in payload["interactions"]] == ["interaction-1"]
    assert payload["plan_document"]["id"] == "doc-1"
    assert payload["timeline"]
    assert payload["cursor"]
    assert payload["autonomy"] == "supervised"
    issue = payload["issues"][0]
    assert issue["phase"] == "gate"
    assert issue["active_run"] is None
    assert issue["active_agent"] is None


def test_project_state_adds_phase_and_active_owner_per_issue(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _init_db(workspace)
    db_path = workspace / ".aiteam" / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO agents (id,role,name,adapter_type) "
            "VALUES ('role:engineer','engineer','Engineer','subscription_cli')"
        )
        conn.execute(
            "INSERT INTO issues (id,parent_id,goal_id,title,status,role,assignee_agent_id) "
            "VALUES ('issue:build','issue:intake','goal-1','Build','in_progress','engineer','role:engineer')"
        )
        conn.execute(
            "INSERT INTO runs "
            "(id,agent_id,issue_id,status,adapter_type,provider,model,channel,started_at) "
            "VALUES ('run:active','role:engineer','issue:build','running',"
            "'subscription_cli','openai-codex','gpt','subscription','2027-01-01T00:00:00Z')"
        )
        conn.commit()

    client, previous = _client_for_workspace(workspace)
    try:
        response = client.get("/api/project/state")
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    issues = {row["id"]: row for row in response.json()["issues"]}
    build = issues["issue:build"]
    assert build["phase"] == "engineer"
    assert build["active_run"]["id"] == "run:active"
    assert build["active_run"]["status"] == "running"
    assert build["active_agent"] == {
        "id": "role:engineer", "role": "engineer", "name": "Engineer"
    }


def test_project_autonomy_endpoint_roundtrip(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _init_db(workspace)
    client, previous = _client_for_workspace(workspace)
    try:
        bad = client.post("/api/project/autonomy", json={"mode": "yolo"})
        assert bad.status_code == 400

        response = client.post("/api/project/autonomy", json={"mode": "autonomous"})
        assert response.status_code == 200
        assert response.json()["autonomy"] == "autonomous"

        state = client.get("/api/project/state")
        assert state.json()["autonomy"] == "autonomous"
    finally:
        set_current_workspace(previous)
