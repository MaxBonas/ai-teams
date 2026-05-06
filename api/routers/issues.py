from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.utils import PROJECT_ROOT, _require_api_auth_request, _workspace_from_request, get_current_workspace, resolve_runtime_dir
from aiteam.db.activity_log import log_activity
from aiteam.db.dependencies import list_dependencies, resolve_blocker_wakeups
from aiteam.db.documents import get_document
from aiteam.db.interactions import list_interactions
from aiteam.db.issues import create_issue, get_issue, list_issues, update_issue
from aiteam.db.liveness import diagnose_issue
from aiteam.project_adapters import ensure_quorum_agents, project_profiles
from aiteam.run_profiles import normalize_run_profile, LEAD_QUORUM

router = APIRouter()


class CreateIssueRequest(BaseModel):
    title: str
    status: str = "backlog"
    goal_id: str | None = None
    parent_id: str | None = None
    description: str | None = None
    role: str | None = None
    complexity: str | None = None
    priority: int = 0
    assignee_agent_id: str | None = None
    metadata: dict[str, Any] = {}


class UpdateIssueRequest(BaseModel):
    status: str | None = None
    title: str | None = None
    description: str | None = None
    assignee_agent_id: str | None = None
    priority: int | None = None
    complexity: str | None = None
    criticality: str | None = None
    metadata: dict[str, Any] | None = None


@router.post("/api/issues")
async def post_issue(body: CreateIssueRequest, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        row = create_issue(
            db, title=body.title, status=body.status, goal_id=body.goal_id,
            parent_id=body.parent_id, description=body.description, role=body.role,
            complexity=body.complexity, priority=body.priority,
            assignee_agent_id=body.assignee_agent_id, metadata=body.metadata,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)

    # If this is a lead_quorum task, ensure quorum agents exist so the Lead
    # can immediately assign sub-issues to them without FK failures.
    raw_profile = (body.metadata or {}).get("profile") or ""
    if normalize_run_profile(raw_profile) == LEAD_QUORUM:
        try:
            workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
            profiles = project_profiles(runtime_dir)
            ensure_quorum_agents(db, profiles=profiles)
        except Exception:
            pass  # never fail issue creation because of agent bootstrap

    log_activity(
        db,
        action="issue.created",
        target_type="issue",
        target_id=row["id"],
        actor_user_id="user",
        payload={"title": row.get("title"), "status": row.get("status"), "assignee_agent_id": row.get("assignee_agent_id")},
    )
    return {"success": True, "issue": row}


@router.get("/api/issues")
async def get_issues(
    request: Request,
    goal_id: str | None = None,
    parent_id: str | None = None,
    status: str | None = None,
    assignee_agent_id: str | None = None,
    limit: int = 200,
):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        rows = list_issues(db, goal_id=goal_id, parent_id=parent_id,
                           status=status, assignee_agent_id=assignee_agent_id, limit=limit)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "issues": rows}


@router.get("/api/issues/{issue_id}")
async def get_issue_by_id(issue_id: str, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        row = get_issue(db, issue_id=issue_id)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    if row is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    # Include pending interactions inline
    try:
        interactions = list_interactions(db, issue_id=issue_id)
        pending = [i for i in interactions if i.get("status") == "pending"]
    except Exception:
        pending = []
    try:
        plan_document = get_document(db, issue_id=issue_id, key="plan")
    except Exception:
        plan_document = None
    return {"success": True, "issue": row, "pending_interactions": pending, "plan_document": plan_document}


@router.get("/api/issues/{issue_id}/liveness")
async def get_issue_liveness(issue_id: str, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        diagnosis = diagnose_issue(db, issue_id=issue_id)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "issue_id": issue_id, "diagnosis": diagnosis}


@router.patch("/api/issues/{issue_id}")
async def patch_issue(issue_id: str, body: UpdateIssueRequest, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        row = update_issue(
            db, issue_id=issue_id, status=body.status, title=body.title,
            description=body.description, assignee_agent_id=body.assignee_agent_id,
            priority=body.priority, complexity=body.complexity,
            criticality=body.criticality, metadata=body.metadata,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    if row is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    log_activity(
        db,
        action="issue.updated",
        target_type="issue",
        target_id=row["id"],
        actor_user_id="user",
        payload={
            "status": body.status,
            "title": body.title,
            "assignee_agent_id": body.assignee_agent_id,
            "priority": body.priority,
            "complexity": body.complexity,
            "criticality": body.criticality,
        },
    )
    # Unblock dependent issues when this one reaches a terminal state
    if body.status in ("done", "cancelled"):
        try:
            resolve_blocker_wakeups(db, resolved_issue_id=issue_id)
        except Exception:
            pass
    return {"success": True, "issue": row}


def _db(request: Request) -> Path:
    ws = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return resolve_runtime_dir(ws, PROJECT_ROOT) / "aiteam.db"

def _schema_err(exc: sqlite3.OperationalError) -> HTTPException:
    if "no such table" in str(exc).lower():
        return HTTPException(status_code=503, detail="Schema not available")
    return HTTPException(status_code=500, detail=str(exc))
