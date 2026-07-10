from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.extensions import router
from api.utils import get_current_workspace, set_current_workspace


def _client(workspace: Path) -> tuple[TestClient, Path]:
    (workspace / ".aiteam").mkdir(parents=True, exist_ok=True)
    app = FastAPI()
    app.include_router(router)
    previous = get_current_workspace()
    set_current_workspace(workspace)
    return TestClient(app), previous


def test_skill_crud_roundtrip(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client, previous = _client(workspace)
    try:
        # empty at first
        assert client.get("/api/project/skills").json()["skills"] == []

        created = client.post("/api/project/skills", json={
            "name": "Unity Scene Regen",
            "body": "Use Tools > Create Test Scene.",
            "applies_to_roles": ["engineer", "reviewer"],
        })
        assert created.status_code == 200
        assert created.json()["skill"]["name"] == "unity-scene-regen"

        listed = client.get("/api/project/skills").json()["skills"]
        assert len(listed) == 1
        assert listed[0]["body"].startswith("Use Tools")

        # retire
        patched = client.patch("/api/project/skills/unity-scene-regen", json={"status": "retired"})
        assert patched.status_code == 200
        assert patched.json()["skill"]["status"] == "retired"

        # delete
        assert client.delete("/api/project/skills/unity-scene-regen").status_code == 200
        assert client.get("/api/project/skills").json()["skills"] == []
    finally:
        set_current_workspace(previous)


def test_empty_body_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client, previous = _client(workspace)
    try:
        resp = client.post("/api/project/skills", json={"name": "x", "body": "  ", "applies_to_roles": []})
        assert resp.status_code == 400
    finally:
        set_current_workspace(previous)


def test_patch_missing_skill_404(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client, previous = _client(workspace)
    try:
        assert client.patch("/api/project/skills/nope", json={"status": "active"}).status_code == 404
    finally:
        set_current_workspace(previous)
