"""Project-scoped extensions API.

PR 1 — CRUD over ``.aiteam/extensions.json`` skills so the owner can attach
local knowledge to a project from the Config tab.
PR 2 — read-only listing of MCP server proposals (approve/reject happens via
the existing pending-interactions popup, not a form here: the Lead proposes,
the owner answers the card — see ``task.md``, P2). The health endpoint performs
the guarded stdio handshake that promotes an approved entry to active.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.utils import (
    PROJECT_ROOT,
    _require_api_auth_request,
    _workspace_from_request,
    get_current_workspace,
    resolve_runtime_dir,
)
from aiteam.db.activity_log import log_activity
from aiteam.extensions import (
    approve_mcp_server_tools,
    delete_project_skill,
    list_mcp_servers,
    list_project_skills,
    skill_governance_policy,
    transition_mcp_server,
    set_project_skill_status,
    upsert_project_skill,
)
from aiteam.mcp_runtime import McpHealthError, check_and_activate_mcp_server
from aiteam.mcp_catalog import CATALOG_VERSION, list_mcp_catalog

router = APIRouter()


class SkillUpsertRequest(BaseModel):
    name: str
    body: str
    applies_to_roles: list[str] = []
    status: str = "active"


class SkillStatusRequest(BaseModel):
    status: str


class McpToolPolicyItem(BaseModel):
    name: str
    access: str


class McpToolPolicyRequest(BaseModel):
    tools: list[McpToolPolicyItem]


class McpLifecycleRequest(BaseModel):
    action: str


def _runtime_dir(request: Request) -> Path:
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    if workspace.resolve() == PROJECT_ROOT.resolve():
        raise HTTPException(status_code=409, detail="No workspace configured")
    return resolve_runtime_dir(workspace, PROJECT_ROOT)


def _audit(runtime_dir: Path, action: str, payload: dict) -> None:
    db = runtime_dir / "aiteam.db"
    if not db.exists():
        return
    try:
        log_activity(db, action=action, target_type="skill", target_id=str(payload.get("name") or ""), actor_user_id="user", payload=payload)
    except Exception:
        pass  # audit is best-effort; the config write already succeeded


@router.get("/api/project/skills")
async def get_project_skills(request: Request):
    _require_api_auth_request(request)
    runtime_dir = _runtime_dir(request)
    return {
        "success": True,
        "skills": list_project_skills(runtime_dir),
        "governance": skill_governance_policy(runtime_dir),
    }


@router.post("/api/project/skills")
async def post_project_skill(body: SkillUpsertRequest, request: Request):
    _require_api_auth_request(request)
    runtime_dir = _runtime_dir(request)
    try:
        entry = upsert_project_skill(
            runtime_dir,
            name=body.name,
            body=body.body,
            applies_to_roles=body.applies_to_roles,
            origin="owner",
            status=body.status,
            approved_by="user",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(runtime_dir, "skill.upserted", {"name": entry["name"], "applies_to_roles": entry.get("applies_to_roles"), "status": entry.get("status")})
    return {"success": True, "skill": entry}


@router.patch("/api/project/skills/{name}")
async def patch_project_skill(name: str, body: SkillStatusRequest, request: Request):
    _require_api_auth_request(request)
    runtime_dir = _runtime_dir(request)
    try:
        entry = set_project_skill_status(runtime_dir, name=name, status=body.status, changed_by="user")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    _audit(runtime_dir, "skill.status_changed", {"name": entry["name"], "status": entry.get("status")})
    return {"success": True, "skill": entry}


@router.delete("/api/project/skills/{name}")
async def delete_project_skill_endpoint(name: str, request: Request):
    _require_api_auth_request(request)
    runtime_dir = _runtime_dir(request)
    if not delete_project_skill(runtime_dir, name=name):
        raise HTTPException(status_code=404, detail="Skill not found")
    _audit(runtime_dir, "skill.deleted", {"name": name})
    return {"success": True}


@router.get("/api/project/extensions/mcp")
async def get_mcp_servers(request: Request):
    """Read-only. Proposals are pending interactions (see the Pendientes
    popup); this lists what the owner has already approved/rejected."""
    _require_api_auth_request(request)
    return {"success": True, "mcp_servers": list_mcp_servers(_runtime_dir(request))}


@router.get("/api/project/extensions/mcp/catalog")
async def get_mcp_catalog(request: Request):
    """Reviewed descriptors only; listing cannot install, approve or execute."""
    _require_api_auth_request(request)
    return {
        "success": True,
        "catalog_version": CATALOG_VERSION,
        "entries": list_mcp_catalog(),
    }


@router.post("/api/project/extensions/mcp/{name}/health")
async def post_mcp_server_health(name: str, request: Request):
    """Launch an approved executable without shell and require MCP initialize."""
    _require_api_auth_request(request)
    runtime_dir = _runtime_dir(request)
    try:
        result = check_and_activate_mcp_server(runtime_dir, name=name)
    except McpHealthError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail.lower() else 409
        raise HTTPException(status_code=status_code, detail=detail) from exc
    _audit(
        runtime_dir,
        "extension.health_checked",
        {"name": result.get("name"), "status": (result.get("health") or {}).get("status")},
    )
    return {"success": (result.get("health") or {}).get("status") == "ok", "mcp_server": result}


@router.put("/api/project/extensions/mcp/{name}/tools")
async def put_mcp_server_tools(name: str, body: McpToolPolicyRequest, request: Request):
    """Persist the owner's positive read/write policy for the current inventory."""
    _require_api_auth_request(request)
    runtime_dir = _runtime_dir(request)
    try:
        result = approve_mcp_server_tools(
            runtime_dir,
            name=name,
            tools=[item.model_dump() for item in body.tools],
            approved_by="user",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(
        runtime_dir,
        "extension.tools_approved",
        {"name": result["name"], "tools": result.get("approved_tools") or []},
    )
    return {"success": True, "mcp_server": result}


@router.patch("/api/project/extensions/mcp/{name}")
async def patch_mcp_server(name: str, body: McpLifecycleRequest, request: Request):
    """Owner lifecycle gate: retire or return a server to approved/unhealthy."""
    _require_api_auth_request(request)
    runtime_dir = _runtime_dir(request)
    try:
        result = transition_mcp_server(runtime_dir, name=name, action=body.action)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(
        runtime_dir,
        f"extension.{body.action.strip().lower()}",
        {"name": result["name"], "status": result.get("status")},
    )
    return {"success": True, "mcp_server": result}
