from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.utils import (
    PROJECT_ROOT,
    _require_api_auth_request,
    _workspace_from_request,
    get_current_workspace,
    resolve_runtime_dir,
)
from aiteam.db.activity_log import log_activity
from aiteam.db.interactions import (
    ConflictError,
    create_interaction,
    get_interaction,
    list_interactions,
    resolve_interaction,
)

router = APIRouter()


class CreateInteractionRequest(BaseModel):
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    continuation_policy: str = "wake_assignee"
    idempotency_key: str | None = None
    source_run_id: str | None = None
    source_comment_id: str | None = None
    created_by_agent_id: str | None = None
    title: str | None = None
    summary: str | None = None


class ResolveInteractionRequest(BaseModel):
    action: str  # accept | changes_requested | reject | answer | cancel
    result: dict[str, Any] | None = None
    resolution_data: dict[str, Any] | None = None  # optional payload override (e.g. modified team proposal)
    resolved_by_user_id: str | None = None
    resolved_by_agent_id: str | None = None


@router.post("/api/issues/{issue_id}/interactions")
async def post_interaction(issue_id: str, body: CreateInteractionRequest, request: Request):
    _require_api_auth_request(request)
    db_path = _db_path(request)
    try:
        row = create_interaction(
            db_path,
            issue_id=issue_id,
            kind=body.kind,
            payload=body.payload,
            continuation_policy=body.continuation_policy,
            idempotency_key=body.idempotency_key,
            source_run_id=body.source_run_id,
            source_comment_id=body.source_comment_id,
            created_by_agent_id=body.created_by_agent_id,
            title=body.title,
            summary=body.summary,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid reference: {exc}") from exc
    except sqlite3.OperationalError as exc:
        raise _schema_error(exc) from exc
    log_activity(
        db_path,
        action="interaction.created",
        target_type="interaction",
        target_id=row["id"],
        actor_agent_id=body.created_by_agent_id,
        run_id=body.source_run_id,
        payload={"issue_id": issue_id, "kind": body.kind, "title": body.title},
    )
    return {"success": True, "interaction": _decode(row)}


@router.get("/api/issues/{issue_id}/interactions")
async def get_issue_interactions(issue_id: str, request: Request):
    _require_api_auth_request(request)
    db_path = _db_path(request)
    try:
        rows = list_interactions(db_path, issue_id=issue_id)
    except sqlite3.OperationalError as exc:
        raise _schema_error(exc) from exc
    return {"success": True, "interactions": [_decode(r) for r in rows]}


@router.get("/api/interactions/{interaction_id}")
async def get_interaction_by_id(interaction_id: str, request: Request):
    _require_api_auth_request(request)
    db_path = _db_path(request)
    try:
        row = get_interaction(db_path, interaction_id=interaction_id)
    except sqlite3.OperationalError as exc:
        raise _schema_error(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Interaction not found")
    return {"success": True, "interaction": _decode(row)}


@router.patch("/api/interactions/{interaction_id}")
async def patch_interaction(interaction_id: str, body: ResolveInteractionRequest, request: Request):
    _require_api_auth_request(request)
    db_path = _db_path(request)
    try:
        row = resolve_interaction(
            db_path,
            interaction_id=interaction_id,
            action=body.action,
            result=body.result,
            resolution_data=body.resolution_data,
            resolved_by_user_id=body.resolved_by_user_id,
            resolved_by_agent_id=body.resolved_by_agent_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        raise _schema_error(exc) from exc
    log_activity(
        db_path,
        action=f"interaction.{body.action}",
        target_type="interaction",
        target_id=row["id"],
        actor_agent_id=body.resolved_by_agent_id,
        actor_user_id=body.resolved_by_user_id,
        payload={"issue_id": row["issue_id"], "kind": row["kind"], "status": row["status"]},
    )
    return {"success": True, "interaction": _decode(row)}


def _db_path(request: Request) -> Path:
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return resolve_runtime_dir(workspace, PROJECT_ROOT) / "aiteam.db"


def _decode(row: dict) -> dict:
    import json
    out = dict(row)
    for key, val in list(out.items()):
        if key.endswith("_json") and isinstance(val, str):
            try:
                out[key[:-5]] = json.loads(val)
            except Exception:
                out[key[:-5]] = val
    return out


def _schema_error(exc: sqlite3.OperationalError) -> HTTPException:
    if "no such table" in str(exc).lower():
        return HTTPException(status_code=503, detail="Control-plane v2 schema not available")
    return HTTPException(status_code=500, detail=str(exc))
