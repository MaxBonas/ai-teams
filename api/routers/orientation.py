from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from api.utils import (
    PROJECT_ROOT,
    _require_api_auth_request,
    _workspace_from_request,
    get_current_workspace,
    resolve_runtime_dir,
)
from aiteam.db.orientation_measurement import (
    end_orientation_session,
    erase_orientation_measurement,
    orientation_summary,
    record_orientation_event,
    set_measurement_consent,
)

router = APIRouter()


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class OrientationEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow: Literal["inbox", "profile_selection", "accepted_plan_to_task"]
    event: Literal[
        "flow_started",
        "flow_completed",
        "flow_abandoned",
        "profile_selected",
        "guidance_viewed",
        "ui_error",
    ]
    profile: Literal["solo_lead", "lead_quorum", "full_team"] | None = None


class EndSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "abandoned"]


@router.get("/api/orientation-measurement")
async def get_orientation_measurement(request: Request):
    _require_api_auth_request(request)
    return {"success": True, **orientation_summary(_db(request))}


@router.post("/api/orientation-measurement/consent")
async def post_orientation_consent(body: ConsentRequest, request: Request):
    _require_api_auth_request(request)
    return {"success": True, "consent": set_measurement_consent(_db(request), enabled=body.enabled)}


@router.post("/api/orientation-measurement/events")
async def post_orientation_event(body: OrientationEventRequest, request: Request):
    _require_api_auth_request(request)
    try:
        event = record_orientation_event(
            _db(request), flow=body.flow, event=body.event, profile=body.profile
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True, "event": event}


@router.post("/api/orientation-measurement/session/end")
async def post_orientation_session_end(body: EndSessionRequest, request: Request):
    _require_api_auth_request(request)
    try:
        session = end_orientation_session(_db(request), status=body.status)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True, "session": session}


@router.delete("/api/orientation-measurement")
async def delete_orientation_measurement(request: Request):
    _require_api_auth_request(request)
    return {"success": True, **erase_orientation_measurement(_db(request))}


def _db(request: Request) -> Path:
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return resolve_runtime_dir(workspace, PROJECT_ROOT) / "aiteam.db"
