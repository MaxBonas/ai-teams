from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.utils import PROJECT_ROOT, _require_api_auth_request, _workspace_from_request, get_current_workspace, resolve_runtime_dir
from aiteam.db.activity_log import log_activity
from aiteam.db.comments import create_comment, get_comment, list_comments

router = APIRouter()


class CreateCommentRequest(BaseModel):
    body: str
    author_agent_id: str | None = None
    author_user_id: str | None = None
    source_run_id: str | None = None
    metadata: dict[str, Any] = {}


@router.post("/api/issues/{issue_id}/comments")
async def post_comment(issue_id: str, body: CreateCommentRequest, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        row = create_comment(
            db, issue_id=issue_id, body=body.body,
            author_agent_id=body.author_agent_id,
            author_user_id=body.author_user_id,
            source_run_id=body.source_run_id,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid reference: {exc}")
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    log_activity(
        db,
        action="comment.created",
        target_type="comment",
        target_id=row["id"],
        actor_agent_id=body.author_agent_id,
        actor_user_id=body.author_user_id,
        run_id=body.source_run_id,
        payload={"issue_id": issue_id, "body_preview": body.body[:180]},
    )
    return {"success": True, "comment": row}


@router.get("/api/issues/{issue_id}/comments")
async def get_issue_comments(issue_id: str, request: Request, limit: int = 200):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        rows = list_comments(db, issue_id=issue_id, limit=limit)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "comments": rows}


@router.get("/api/comments/{comment_id}")
async def get_comment_by_id(comment_id: str, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        row = get_comment(db, comment_id=comment_id)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    if row is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    return {"success": True, "comment": row}


def _db(request: Request) -> Path:
    ws = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return resolve_runtime_dir(ws, PROJECT_ROOT) / "aiteam.db"

def _schema_err(exc: sqlite3.OperationalError) -> HTTPException:
    if "no such table" in str(exc).lower():
        return HTTPException(status_code=503, detail="Schema not available")
    return HTTPException(status_code=500, detail=str(exc))
