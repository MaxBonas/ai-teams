from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.utils import PROJECT_ROOT, _require_api_auth_request, _workspace_from_request, get_current_workspace, resolve_runtime_dir
from aiteam.db.activity_log import log_activity
from aiteam.db.goals import create_goal, get_goal, list_goals

router = APIRouter()


class CreateGoalRequest(BaseModel):
    title: str
    description: str | None = None
    status: str = "active"
    source: str = "api"
    metadata: dict[str, Any] = {}


@router.post("/api/goals")
async def post_goal(body: CreateGoalRequest, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        row = create_goal(db, title=body.title, description=body.description,
                          status=body.status, source=body.source, metadata=body.metadata)
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    log_activity(
        db,
        action="goal.created",
        target_type="goal",
        target_id=row["id"],
        actor_user_id="user",
        payload={"title": row.get("title"), "status": row.get("status"), "source": row.get("source")},
    )
    return {"success": True, "goal": row}


@router.get("/api/goals")
async def get_goals(request: Request, status: str | None = None, limit: int = 100):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        rows = list_goals(db, status=status, limit=limit)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "goals": rows}


@router.get("/api/goals/{goal_id}")
async def get_goal_by_id(goal_id: str, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        row = get_goal(db, goal_id=goal_id)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    if row is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    return {"success": True, "goal": row}


def _db(request: Request) -> Path:
    ws = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return resolve_runtime_dir(ws, PROJECT_ROOT) / "aiteam.db"

def _schema_err(exc: sqlite3.OperationalError) -> HTTPException:
    if "no such table" in str(exc).lower():
        return HTTPException(status_code=503, detail="Schema not available")
    return HTTPException(status_code=500, detail=str(exc))
