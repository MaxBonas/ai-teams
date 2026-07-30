from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from aiteam.db.provider_change_workflows import (
    CASE_STATUSES,
    ProviderChangeConflictError,
    get_provider_change_case,
    list_provider_change_cases,
    reconcile_provider_change_cases,
    record_provider_change_notification_action,
    transition_provider_change_case,
)
from aiteam.model_catalog_service import invalidate_model_catalog_cache
from aiteam.provider_change_notifications import build_provider_change_inbox
from aiteam.provider_change_runtime import machine_provider_change_db_path
from api.utils import _require_api_auth_request

router = APIRouter(prefix="/api/provider-changes", tags=["provider-changes"])


class ProviderChangeTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class ProviderChangeNotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern="^(acknowledge|snooze|manage)$")
    expected_revision: int = Field(ge=1)
    snooze_hours: int | None = Field(default=None, ge=1, le=720)


@router.post("/reconcile")
async def reconcile_cases(request: Request) -> dict[str, Any]:
    _require_api_auth_request(request)
    created = reconcile_provider_change_cases(
        machine_provider_change_db_path()
    )
    return {
        "success": True,
        "schema_version": "provider_change_workflow_v1",
        "created": len(created),
        "cases": created,
    }


@router.get("/inbox")
async def get_inbox(
    request: Request,
    include_snoozed: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    _require_api_auth_request(request)
    return build_provider_change_inbox(
        machine_provider_change_db_path(),
        include_snoozed=include_snoozed,
        limit=limit,
    )


@router.get("/cases")
async def get_cases(
    request: Request,
    status: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    _require_api_auth_request(request)
    statuses = set(status or ())
    if statuses and not statuses <= CASE_STATUSES:
        raise HTTPException(
            status_code=422,
            detail="provider_change_case_status_invalid",
        )
    rows = list_provider_change_cases(
        machine_provider_change_db_path(),
        statuses=statuses or None,
        limit=limit,
    )
    return {
        "success": True,
        "schema_version": "provider_change_workflow_v1",
        "cases": rows,
    }


@router.get("/cases/{case_id}")
async def get_case(case_id: str, request: Request) -> dict[str, Any]:
    _require_api_auth_request(request)
    try:
        row = get_provider_change_case(
            machine_provider_change_db_path(),
            case_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="provider_change_case_not_found",
        ) from exc
    return {"success": True, "case": row}


@router.post("/cases/{case_id}/transition")
async def transition_case(
    case_id: str,
    body: ProviderChangeTransitionRequest,
    request: Request,
) -> dict[str, Any]:
    _require_api_auth_request(request)
    try:
        row = transition_provider_change_case(
            machine_provider_change_db_path(),
            case_id,
            action=body.action,
            expected_revision=body.expected_revision,
            actor="owner",
            payload=body.payload,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="provider_change_case_not_found",
        ) from exc
    except ProviderChangeConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="provider_change_case_revision_stale",
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    invalidate_model_catalog_cache()
    return {"success": True, "case": row}


@router.post("/cases/{case_id}/notification")
async def notification_action(
    case_id: str,
    body: ProviderChangeNotificationRequest,
    request: Request,
) -> dict[str, Any]:
    _require_api_auth_request(request)
    try:
        row = record_provider_change_notification_action(
            machine_provider_change_db_path(),
            case_id,
            action=body.action,
            expected_revision=body.expected_revision,
            actor="owner",
            snooze_hours=body.snooze_hours,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="provider_change_case_not_found",
        ) from exc
    except ProviderChangeConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="provider_change_case_revision_stale",
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "success": True,
        "case": row,
        "inbox": build_provider_change_inbox(
            machine_provider_change_db_path(),
            include_snoozed=True,
        ),
    }
