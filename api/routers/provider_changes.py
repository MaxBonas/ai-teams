from __future__ import annotations

import uuid
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
from aiteam.provider_change_delivery import (
    ProviderChangeDeliveryConflictError,
    configure_destination,
    deliver_provider_change_outbox,
    delivery_summary,
    destination_endpoint_secret_ref,
    get_destination,
    list_destinations,
    set_destination_enabled,
    sync_provider_change_outbox,
    test_destination,
    validate_webhook_endpoint,
)
from aiteam.provider_change_notifications import build_provider_change_inbox
from aiteam.provider_change_runtime import machine_provider_change_db_path
from aiteam.user_config import store_secret
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


class ProviderChangeDestinationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    endpoint_url: str = Field(min_length=1, max_length=4096)
    explicit_consent: bool
    minimum_severity: str = Field(
        default="warning",
        pattern="^(critical|error|warning|info)$",
    )
    delivery_mode: str = Field(
        default="urgent_and_digest",
        pattern="^(urgent_and_digest|urgent_only|digest_only)$",
    )
    cooldown_sec: int = Field(default=3600, ge=60, le=604800)


class ProviderChangeDestinationUpdateRequest(
    ProviderChangeDestinationCreateRequest
):
    endpoint_url: str | None = Field(default=None, min_length=1, max_length=4096)
    expected_revision: int = Field(ge=1)


class ProviderChangeDestinationToggleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    expected_revision: int = Field(ge=1)


class ProviderChangeDestinationTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


def _inbox(*, include_snoozed: bool = False, limit: int = 100) -> dict[str, Any]:
    db_path = machine_provider_change_db_path()
    payload = build_provider_change_inbox(
        db_path,
        include_snoozed=include_snoozed,
        limit=limit,
    )
    delivery = delivery_summary(db_path)
    payload["scope"].update(
        {
            "external_delivery_enabled": delivery["external_delivery_enabled"],
            "external_delivery_reason": delivery["external_delivery_reason"],
        }
    )
    payload["delivery"] = delivery
    return payload


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
    return _inbox(include_snoozed=include_snoozed, limit=limit)


@router.get("/delivery/destinations")
async def get_delivery_destinations(request: Request) -> dict[str, Any]:
    _require_api_auth_request(request)
    db_path = machine_provider_change_db_path()
    return {
        "success": True,
        **delivery_summary(db_path),
        "destinations": list_destinations(db_path),
    }


@router.post("/delivery/destinations")
async def create_delivery_destination(
    body: ProviderChangeDestinationCreateRequest,
    request: Request,
) -> dict[str, Any]:
    _require_api_auth_request(request)
    db_path = machine_provider_change_db_path()
    destination_id = str(uuid.uuid4())
    try:
        endpoint = validate_webhook_endpoint(body.endpoint_url)
        secret_ref = store_secret(
            provider="provider-change-webhook",
            name=destination_id,
            secret=endpoint,
        )
        destination = configure_destination(
            db_path,
            destination_id=destination_id,
            label=body.label,
            endpoint_secret_ref=secret_ref,
            minimum_severity=body.minimum_severity,
            delivery_mode=body.delivery_mode,
            cooldown_sec=body.cooldown_sec,
            explicit_consent=body.explicit_consent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True, "destination": destination}


@router.put("/delivery/destinations/{destination_id}")
async def update_delivery_destination(
    destination_id: str,
    body: ProviderChangeDestinationUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    _require_api_auth_request(request)
    db_path = machine_provider_change_db_path()
    try:
        current = get_destination(db_path, destination_id)
        if current["revision"] != body.expected_revision:
            raise ProviderChangeDeliveryConflictError(destination_id)
        secret_ref = destination_endpoint_secret_ref(db_path, destination_id)
        if body.endpoint_url is not None:
            endpoint = validate_webhook_endpoint(body.endpoint_url)
            secret_ref = store_secret(
                provider="provider-change-webhook",
                # Una referencia nueva evita que una escritura stale cambie
                # por fuera de SQLite el endpoint de un destino activo.
                name=f"{destination_id}-{uuid.uuid4()}",
                secret=endpoint,
            )
        destination = configure_destination(
            db_path,
            destination_id=destination_id,
            expected_revision=body.expected_revision,
            label=body.label,
            endpoint_secret_ref=secret_ref,
            minimum_severity=body.minimum_severity,
            delivery_mode=body.delivery_mode,
            cooldown_sec=body.cooldown_sec,
            explicit_consent=body.explicit_consent,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="provider_notification_destination_not_found",
        ) from exc
    except ProviderChangeDeliveryConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="provider_notification_destination_revision_stale",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True, "destination": destination}


@router.post("/delivery/destinations/{destination_id}/test")
async def test_delivery_destination(
    destination_id: str,
    body: ProviderChangeDestinationTestRequest,
    request: Request,
) -> dict[str, Any]:
    _require_api_auth_request(request)
    try:
        destination = test_destination(
            machine_provider_change_db_path(),
            destination_id,
            expected_revision=body.expected_revision,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="provider_notification_destination_not_found",
        ) from exc
    except ProviderChangeDeliveryConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="provider_notification_destination_revision_stale",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True, "destination": destination}


@router.post("/delivery/destinations/{destination_id}/enabled")
async def toggle_delivery_destination(
    destination_id: str,
    body: ProviderChangeDestinationToggleRequest,
    request: Request,
) -> dict[str, Any]:
    _require_api_auth_request(request)
    try:
        destination = set_destination_enabled(
            machine_provider_change_db_path(),
            destination_id,
            enabled=body.enabled,
            expected_revision=body.expected_revision,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="provider_notification_destination_not_found",
        ) from exc
    except ProviderChangeDeliveryConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="provider_notification_destination_revision_stale",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True, "destination": destination}


@router.post("/delivery/dispatch")
async def dispatch_delivery_outbox(request: Request) -> dict[str, Any]:
    _require_api_auth_request(request)
    db_path = machine_provider_change_db_path()
    queued = sync_provider_change_outbox(db_path)
    delivered = deliver_provider_change_outbox(db_path)
    return {
        "success": True,
        "queued": queued,
        "delivery": delivered,
        "summary": delivery_summary(db_path),
    }


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
        "inbox": _inbox(include_snoozed=True),
    }
