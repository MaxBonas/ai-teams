from __future__ import annotations

import sys
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


def test_owner_can_review_edit_activate_and_retire_learned_skill(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client, previous = _client(workspace)
    try:
        from aiteam.extensions import propose_learned_skill

        propose_learned_skill(
            workspace / ".aiteam",
            name="local-rule",
            body="Borrador observado",
            applies_to_roles=["engineer"],
            evidence=["run-1", "run-2"],
            source_run_id="run-2",
        )
        listing = client.get("/api/project/skills").json()
        assert listing["skills"][0]["status"] == "proposed"
        assert listing["governance"]["learned_skills"] == 1

        edited = client.post("/api/project/skills", json={
            "name": "local-rule",
            "body": "Regla corregida por el owner",
            "applies_to_roles": ["engineer", "reviewer"],
            "status": "proposed",
        })
        assert edited.status_code == 200
        assert edited.json()["skill"]["origin"] == "learned"

        active = client.patch("/api/project/skills/local-rule", json={"status": "active"})
        assert active.status_code == 200
        assert active.json()["skill"]["approved_by"] == "user"
        retired = client.patch("/api/project/skills/local-rule", json={"status": "retired"})
        assert retired.status_code == 200
        assert retired.json()["skill"]["status"] == "retired"
    finally:
        set_current_workspace(previous)


def test_mcp_servers_listing_empty_then_populated(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client, previous = _client(workspace)
    try:
        assert client.get("/api/project/extensions/mcp").json()["mcp_servers"] == []

        from aiteam.extensions import approve_mcp_server
        approve_mcp_server(
            workspace / ".aiteam", name="unity", source="npx -y unity-mcp@1.2.0",
            version="1.2.0",
            applies_to_roles=["engineer"], justification="test", approved_by="user",
        )
        listed = client.get("/api/project/extensions/mcp").json()["mcp_servers"]
        assert len(listed) == 1
        assert listed[0]["name"] == "unity"
        assert listed[0]["status"] == "approved"
    finally:
        set_current_workspace(previous)


def test_mcp_catalog_endpoint_is_read_only_and_reviewed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client, previous = _client(workspace)
    try:
        response = client.get("/api/project/extensions/mcp/catalog")
        assert response.status_code == 200
        payload = response.json()
        assert payload["catalog_version"] == 1
        assert {entry["id"] for entry in payload["entries"]} == {
            "github-readonly", "playwright-browser", "filesystem-workspace",
        }
        assert list((workspace / ".aiteam").glob("*")) == []
    finally:
        set_current_workspace(previous)


def test_mcp_health_endpoint_activates_only_after_initialize(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    server = tmp_path / "server.py"
    server.write_text(
        "import json,sys\n"
        "r=json.loads(sys.stdin.readline())\n"
        "result={'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'fake','version':'1.0.0'}}\n"
        "print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}),flush=True)\n"
        "json.loads(sys.stdin.readline())\n"
        "r=json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'tools':[{'name':'read','annotations':{'readOnlyHint':True}}]}}),flush=True)\n"
        "sys.stdin.read()\n",
        encoding="utf-8",
    )
    client, previous = _client(workspace)
    try:
        from aiteam.extensions import approve_mcp_server

        approve_mcp_server(
            workspace / ".aiteam",
            name="fake",
            source=sys.executable,
            version="1.0.0",
            args=[str(server)],
            applies_to_roles=["engineer"],
            approved_by="user",
        )
        response = client.post("/api/project/extensions/mcp/fake/health")
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["mcp_server"]["status"] == "active"
        policy = client.put(
            "/api/project/extensions/mcp/fake/tools",
            json={"tools": [{"name": "read", "access": "read"}]},
        )
        assert policy.status_code == 200
        assert policy.json()["mcp_server"]["approved_tools"] == [
            {"name": "read", "access": "read"}
        ]
    finally:
        set_current_workspace(previous)


def test_mcp_health_endpoint_maps_missing_and_unapproved_contracts(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client, previous = _client(workspace)
    try:
        missing = client.post("/api/project/extensions/mcp/missing/health")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "MCP server not found"

        from aiteam.extensions import reject_mcp_server

        reject_mcp_server(workspace / ".aiteam", name="rejected", justification="unsafe")
        rejected = client.post("/api/project/extensions/mcp/rejected/health")
        assert rejected.status_code == 409
        assert "owner-approved" in rejected.json()["detail"]
    finally:
        set_current_workspace(previous)


def test_owner_can_retire_and_reactivate_mcp_but_reactivation_requires_health(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client, previous = _client(workspace)
    try:
        from aiteam.extensions import approve_mcp_server

        approve_mcp_server(
            workspace / ".aiteam",
            name="docs",
            source=sys.executable,
            version="1.0.0",
            approved_by="user",
        )
        retired = client.patch(
            "/api/project/extensions/mcp/docs", json={"action": "retire"}
        )
        assert retired.status_code == 200
        assert retired.json()["mcp_server"]["status"] == "retired"

        reactivated = client.patch(
            "/api/project/extensions/mcp/docs", json={"action": "reactivate"}
        )
        assert reactivated.status_code == 200
        entry = reactivated.json()["mcp_server"]
        assert entry["status"] == "approved"
        assert "health" not in entry

        invalid = client.patch(
            "/api/project/extensions/mcp/docs", json={"action": "reactivate"}
        )
        assert invalid.status_code == 409
    finally:
        set_current_workspace(previous)
