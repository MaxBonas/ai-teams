from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.utils import PROJECT_ROOT, _require_api_auth_request, _workspace_from_request, get_current_workspace, resolve_runtime_dir
from aiteam.db.dependencies import (
    add_dependency,
    all_blockers_resolved,
    list_blocked_issues,
    list_dependencies,
    remove_dependency,
)

router = APIRouter()


class AddDependencyRequest(BaseModel):
    depends_on_issue_id: str
    relation_type: str = "blocks"


@router.post("/api/issues/{issue_id}/dependencies")
async def post_dependency(issue_id: str, body: AddDependencyRequest, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        row = add_dependency(
            db,
            issue_id=issue_id,
            depends_on_issue_id=body.depends_on_issue_id,
            relation_type=body.relation_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except sqlite3.IntegrityError as exc:
        msg = str(exc).lower()
        if "unique" in msg or "primary key" in msg:
            raise HTTPException(status_code=409, detail="Dependency already exists")
        raise HTTPException(status_code=400, detail=f"Invalid reference: {exc}")
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "dependency": row}


@router.get("/api/issues/{issue_id}/dependencies")
async def get_dependencies(issue_id: str, request: Request):
    """List blockers of issue_id (what it depends on)."""
    _require_api_auth_request(request)
    db = _db(request)
    try:
        deps = list_dependencies(db, issue_id=issue_id)
        blocked_by_me = list_blocked_issues(db, depends_on_issue_id=issue_id)
        resolved = all_blockers_resolved(db, issue_id=issue_id)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {
        "success": True,
        "issue_id": issue_id,
        "blocked_by": deps,
        "blocking": blocked_by_me,
        "all_blockers_resolved": resolved,
    }


@router.delete("/api/issues/{issue_id}/dependencies/{depends_on_issue_id}")
async def delete_dependency(issue_id: str, depends_on_issue_id: str, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        deleted = remove_dependency(db, issue_id=issue_id, depends_on_issue_id=depends_on_issue_id)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    if not deleted:
        raise HTTPException(status_code=404, detail="Dependency not found")
    return {"success": True}


def _db(request: Request) -> Path:
    ws = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return resolve_runtime_dir(ws, PROJECT_ROOT) / "aiteam.db"


def _schema_err(exc: sqlite3.OperationalError) -> HTTPException:
    if "no such table" in str(exc).lower():
        return HTTPException(status_code=503, detail="Schema not available")
    return HTTPException(status_code=500, detail=str(exc))
