from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.main as main_mod
import api.routers.workspace as workspace_mod
from api.routers.workspace import router
from api.routers.agents import router as agents_router
from api.utils import get_current_workspace, set_current_workspace


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _full_client() -> TestClient:
    """Client with both workspace and agents routers (for reconcile tests)."""
    from api.main import app
    return TestClient(app)


def test_workspace_endpoint_clears_deleted_workspace(tmp_path: Path) -> None:
    deleted = tmp_path / "deleted-project"
    previous = get_current_workspace()
    set_current_workspace(deleted)
    try:
        response = _client().get("/api/workspace")
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is False
    assert payload["workspace"] == ""
    assert payload["reason"] == "workspace_missing"


def test_workspace_endpoint_reports_missing_project_db(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    response = _client().get("/api/workspace", headers={"x-aiteam-workspace": str(project)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is False
    assert payload["workspace"] == ""
    assert payload["reason"] == "workspace_db_missing"


def test_create_project_requires_adapter_profile(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post("/api/projects/new", json={"name": "Demo"})
    finally:
        set_current_workspace(previous)

    assert response.status_code == 400
    assert "adapter" in response.json()["detail"]


def test_create_project_bootstraps_lead_with_selected_adapter(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post(
            "/api/projects/new",
            json={"name": "Demo", "initial_task": "Build it", "adapter_profile_ids": ["openai_api"]},
        )
        payload = response.json()
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    db_path = Path(payload["workspace"]) / ".aiteam" / "aiteam.db"
    import sqlite3
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT adapter_type, adapter_config_json FROM agents WHERE id = 'role:lead'").fetchone()
    assert row[0] == "openai_api"
    assert '"profile_id": "openai_api"' in row[1]


def test_create_project_bootstraps_minimum_org_chart(tmp_path: Path, monkeypatch) -> None:
    """Project creation must immediately create the full minimum org chart.

    Minimum roster:
      Tier 1 — role:lead
      Tier 3 — role:file_scout, role:web_scout, role:context_curator
    All must exist in the DB right after /api/projects/new, before any executor run.
    """
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post(
            "/api/projects/new",
            json={"name": "OrgChart", "adapter_profile_ids": ["openai_api"]},
        )
        payload = response.json()
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    db_path = Path(payload["workspace"]) / ".aiteam" / "aiteam.db"
    import sqlite3
    with sqlite3.connect(str(db_path)) as conn:
        ids = {r[0] for r in conn.execute("SELECT id FROM agents").fetchall()}

    MINIMUM_AGENTS = {
        "role:lead",
        "role:file_scout",
        "role:web_scout",
        "role:context_curator",
    }
    assert MINIMUM_AGENTS <= ids, f"Missing agents: {MINIMUM_AGENTS - ids}"


def test_reconcile_endpoint_is_idempotent_and_returns_repaired(tmp_path: Path, monkeypatch) -> None:
    """POST /api/agents/reconcile must be callable after project creation and be idempotent."""
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        # Create project (sets current workspace to new project dir)
        resp = _client().post(
            "/api/projects/new",
            json={"name": "Reconcile", "adapter_profile_ids": ["openai_api"]},
        )
        assert resp.status_code == 200
        workspace_path = Path(resp.json()["workspace"])
        set_current_workspace(workspace_path)

        client = _full_client()
        # First call: agents already bootstrapped — repaired list may be empty or small
        r1 = client.post("/api/agents/reconcile")
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1["success"] is True
        assert isinstance(body1["repaired"], list)

        # Second call: fully idempotent — nothing new to repair
        r2 = client.post("/api/agents/reconcile")
        assert r2.status_code == 200
        assert r2.json()["repaired"] == []

        # All minimum agents must still be present
        db_path = workspace_path / ".aiteam" / "aiteam.db"
        with sqlite3.connect(str(db_path)) as conn:
            ids = {r[0] for r in conn.execute("SELECT id FROM agents").fetchall()}
        assert {"role:lead", "role:file_scout", "role:web_scout", "role:context_curator"} <= ids
    finally:
        set_current_workspace(previous)


def test_delete_current_project_requires_delete_confirmation(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    project = tmp_path / "Demo"
    runtime = project / ".aiteam"
    runtime.mkdir(parents=True)
    (runtime / "aiteam.db").write_text("", encoding="utf-8")
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(project)
    try:
        bad = _client().request("DELETE", "/api/projects/current", json={"confirmation": "delete"})
        ok = _client().request("DELETE", "/api/projects/current", json={"confirmation": "DELETE"})
    finally:
        set_current_workspace(previous)

    assert bad.status_code == 400
    assert ok.status_code == 200
    assert ok.json()["configured"] is False
    assert not project.exists()


def test_delete_current_project_post_fallback(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    project = tmp_path / "Demo"
    runtime = project / ".aiteam"
    runtime.mkdir(parents=True)
    (runtime / "aiteam.db").write_text("", encoding="utf-8")
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(project)
    try:
        response = _client().post("/api/projects/current/delete", json={"confirmation": "DELETE"})
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert not project.exists()


def test_delete_current_project_moves_locked_folder_to_tombstone(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    project = tmp_path / "Demo"
    runtime = project / ".aiteam"
    runtime.mkdir(parents=True)
    (runtime / "aiteam.db").write_text("", encoding="utf-8")
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)

    def fake_rmtree(path: Path) -> None:
        if Path(path) == project:
            raise PermissionError("locked")
        raise PermissionError("still locked")

    monkeypatch.setattr(workspace_mod, "_rmtree_project_tree", fake_rmtree)
    previous = get_current_workspace()
    set_current_workspace(project)
    try:
        response = _client().post("/api/projects/current/delete", json={"confirmation": "DELETE"})
    finally:
        set_current_workspace(previous)

    payload = response.json()
    assert response.status_code == 200
    assert payload["deleted"] is True
    assert payload["cleanup_pending"] is True
    assert payload["reason"] == "moved_to_tombstone"
    assert not project.exists()
    assert Path(payload["cleanup_path"]).exists()
