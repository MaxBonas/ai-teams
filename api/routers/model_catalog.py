from __future__ import annotations

import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from aiteam.db.model_catalog_maintenance import (
    SCHEMA_VERSION as MODEL_CATALOG_MAINTENANCE_VERSION,
)
from aiteam.db.model_catalog_maintenance import list_model_catalog_maintenance
from aiteam.model_calibration_gate_board import (
    attach_calibration_gates,
    build_model_calibration_gate_board,
)
from aiteam.model_catalog_api import (
    CATALOG_STATE_NAMES,
    TIER1_AUTHORITY_FILTERS,
    filter_catalog_candidates,
    summarize_catalog_providers,
    summarize_tier1_authority,
)
from aiteam.model_catalog_service import (
    get_current_model_catalog,
    invalidate_model_catalog_cache,
)
from aiteam.model_default_rollout import evaluate_shadow_model_default
from aiteam.model_owner_preferences import (
    ModelOwnerPreferencesError,
    load_model_owner_preferences,
    set_model_owner_preference,
)
from aiteam.model_selection_context import contextual_model_selection
from aiteam.policies import canonical_role, role_status
from api.utils import (
    _require_api_auth_request,
    get_current_workspace,
    resolve_runtime_dir,
)

router = APIRouter(prefix="/api/model-catalog", tags=["model-catalog"])


class CatalogProviderSummary(BaseModel):
    profile_id: str
    provider: str
    channel: str
    capacity_pool: str | None = None
    model_count: int
    configured_count: int
    green_count: int
    selectable_count: int
    blocked_count: int
    data_policy: str | None = None
    privacy_note: str | None = None
    economy_classes: list[str] = Field(default_factory=list)


class CatalogCandidate(BaseModel):
    """Contrato estable de envoltura; el payload versionado sigue extensible."""

    model_config = ConfigDict(extra="allow")

    candidate_id: str
    identity: dict[str, Any]
    states: dict[str, Any]
    owner_preference: dict[str, Any] = Field(default_factory=dict)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    model_metadata: dict[str, Any]
    roles: list[dict[str, Any]] = Field(default_factory=list)
    canonical_role: str | None = None
    role_evaluation: dict[str, Any] | None = None
    rank: int | None = None
    selection_reason: str | None = None


class ModelCatalogResponse(BaseModel):
    success: bool = True
    schema_version: str
    score_version: str
    content_hash: str
    observed_at: str
    rollout: str
    filters: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    providers: list[CatalogProviderSummary] = Field(default_factory=list)
    runtime: dict[str, Any] = Field(default_factory=dict)
    tier1_coverage: dict[str, Any] = Field(default_factory=dict)
    candidates: list[CatalogCandidate] = Field(default_factory=list)


class ModelRoleCandidatesResponse(BaseModel):
    success: bool = True
    schema_version: str
    score_version: str
    content_hash: str
    observed_at: str
    rollout: str
    canonical_role: str
    compatibility_context: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    tier1_coverage: dict[str, Any] = Field(default_factory=dict)
    candidates: list[CatalogCandidate] = Field(default_factory=list)


class ModelSelectionRequest(BaseModel):
    role: str = Field(min_length=1)
    issue_id: str = ""
    run_profile: str = ""
    criticality: str = "medium"
    data_class: str = "public"
    required_capabilities: list[str] = Field(default_factory=list)


class ModelSelectionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    success: bool = True
    selection_version: str
    schema_version: str
    score_version: str
    content_hash: str
    rollout: str
    canonical_role: str
    context: dict[str, Any]
    default: dict[str, Any]
    counts: dict[str, int]
    candidates: list[dict[str, Any]]


class ShadowModelSelectionRequest(ModelSelectionRequest):
    selection_scope: str = Field(min_length=1)
    current_profile_id: str = ""
    current_model: str = ""


class ModelOwnerPreferenceRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=255)
    model_id: str = Field(min_length=1, max_length=255)
    state: Literal["high", "normal", "low", "archived"]
    reason: str = Field(min_length=1, max_length=1000)


class ModelCatalogMaintenanceResponse(BaseModel):
    success: bool = True
    schema_version: str
    retention: str
    count: int
    latest: dict[str, Any] | None = None
    snapshots: list[dict[str, Any]] = Field(default_factory=list)


class ModelCalibrationGateBoardResponse(BaseModel):
    success: bool = True
    schema_version: str
    source_schema_version: str | None = None
    source_content_hash: str | None = None
    stage_sequence: list[str]
    counts: dict[str, Any] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)


def _read_model() -> dict[str, Any]:
    db_path = resolve_runtime_dir(get_current_workspace()) / "aiteam.db"
    paths = (db_path,) if db_path.is_file() else ()
    return get_current_model_catalog(db_paths=paths)


def _validated_role(role: str) -> str:
    role_key = canonical_role(role)
    status = role_status(role_key)
    if status == "unknown":
        raise HTTPException(status_code=422, detail=f"Unknown role: {role}")
    if status == "deterministic":
        raise HTTPException(
            status_code=422,
            detail=f"Role {role_key} is deterministic and has no model candidates",
        )
    return role_key


@router.get("/preferences")
async def get_model_owner_preferences(request: Request) -> dict[str, Any]:
    """Preferencias locales; nunca se mezclan con score ni defaults compartidos."""
    _require_api_auth_request(request)
    try:
        document = load_model_owner_preferences()
    except ModelOwnerPreferencesError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"success": True, **document}


@router.put("/preferences")
async def put_model_owner_preference(
    request: Request,
    body: ModelOwnerPreferenceRequest,
) -> dict[str, Any]:
    """Crea, cambia, archiva o reactiva una identidad exacta."""
    _require_api_auth_request(request)
    try:
        preference = set_model_owner_preference(
            body.profile_id,
            body.model_id,
            state=body.state,
            reason=body.reason,
        )
        invalidate_model_catalog_cache()
        document = load_model_owner_preferences()
    except ModelOwnerPreferencesError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "success": True,
        "preference": preference,
        "schema_version": document["schema_version"],
        "updated_at": document["updated_at"],
    }


@router.get("/maintenance", response_model=ModelCatalogMaintenanceResponse)
async def get_model_catalog_maintenance(
    request: Request,
    limit: int = Query(default=24, ge=1, le=120),
) -> ModelCatalogMaintenanceResponse:
    """Histórico redacted de cambios/mensualidad; no contiene candidatos."""
    _require_api_auth_request(request)
    db_path = resolve_runtime_dir(get_current_workspace()) / "aiteam.db"
    if not db_path.is_file():
        history: list[dict[str, Any]] = []
    else:
        _read_model()
        history = list_model_catalog_maintenance(db_path, limit=limit)
    return ModelCatalogMaintenanceResponse(
        schema_version=MODEL_CATALOG_MAINTENANCE_VERSION,
        retention="append_only_no_age_deletion",
        count=len(history),
        latest=history[0] if history else None,
        snapshots=history,
    )


@router.get(
    "/calibration-gates",
    response_model=ModelCalibrationGateBoardResponse,
)
async def get_model_calibration_gates(
    request: Request,
    role: str = "",
    profile_id: str = "",
    actionable: bool | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
) -> ModelCalibrationGateBoardResponse:
    """Tablero exacto y ordenado; nunca ejecuta probes o calibraciones."""
    _require_api_auth_request(request)
    role_key = _validated_role(role) if role else ""
    board = build_model_calibration_gate_board(_read_model())
    rows = [
        row
        for row in board["rows"]
        if (not role_key or row["canonical_role"] == role_key)
        and (not profile_id or row["profile_id"] == profile_id)
        and (actionable is None or row["actionable"] is actionable)
    ]
    visible = rows[:limit]
    return ModelCalibrationGateBoardResponse(
        schema_version=str(board["schema_version"]),
        source_schema_version=board.get("source_schema_version"),
        source_content_hash=board.get("source_content_hash"),
        stage_sequence=list(board["stage_sequence"]),
        counts={
            **dict(board["counts"]),
            "filtered": len(rows),
            "returned": len(visible),
        },
        filters={
            "role": role_key or None,
            "profile_id": profile_id or None,
            "actionable": actionable,
            "limit": limit,
        },
        rows=visible,
    )


@router.get("", response_model=ModelCatalogResponse)
async def get_model_catalog(
    request: Request,
    role: str = "",
    provider: str = "",
    channel: str = "",
    tier: str = "",
    state: str = "",
    authority: str = "",
    configured: bool | None = None,
) -> ModelCatalogResponse:
    """Inventario global visible, incluidos candidatos bloqueados o inactivos."""
    _require_api_auth_request(request)
    role_key = _validated_role(role) if role else ""
    state_key = state.strip().lower()
    if state_key and state_key not in CATALOG_STATE_NAMES:
        raise HTTPException(status_code=422, detail=f"Unknown catalog state: {state}")
    authority_key = authority.strip().lower()
    if authority_key and authority_key not in TIER1_AUTHORITY_FILTERS:
        raise HTTPException(
            status_code=422, detail=f"Unknown Tier 1 authority: {authority}"
        )
    read_model = _read_model()
    candidates = filter_catalog_candidates(
        read_model,
        role=role_key,
        provider=provider,
        channel=channel,
        tier=tier,
        state=state_key,
        tier1_authority=authority_key,
        configured=configured,
    )
    candidates = attach_calibration_gates(candidates)
    return ModelCatalogResponse(
        schema_version=str(read_model["schema_version"]),
        score_version=str(read_model["score_version"]),
        content_hash=str(read_model["content_hash"]),
        observed_at=str(read_model["observed_at"]),
        rollout=str(read_model["rollout"]),
        filters={
            "role": role_key or None,
            "provider": provider or None,
            "channel": channel or None,
            "tier": tier or None,
            "state": state_key or None,
            "authority": authority_key or None,
            "configured": configured,
        },
        counts={
            "candidates": len(candidates),
            "providers": len(summarize_catalog_providers(candidates)),
        },
        providers=summarize_catalog_providers(candidates),
        runtime=dict(read_model.get("runtime") or {}),
        tier1_coverage=summarize_tier1_authority(
            list(read_model.get("candidates") or ())
        ),
        candidates=candidates,
    )


@router.get("/candidates", response_model=ModelRoleCandidatesResponse)
async def get_model_role_candidates(
    request: Request,
    role: str = Query(min_length=1),
    provider: str = "",
    channel: str = "",
    tier: str = "",
    state: str = "",
    authority: str = "",
    configured: bool | None = None,
) -> ModelRoleCandidatesResponse:
    """Ranking global shadow del par modelo+perfil para un rol canónico."""
    _require_api_auth_request(request)
    role_key = _validated_role(role)
    state_key = state.strip().lower()
    if state_key and state_key not in CATALOG_STATE_NAMES:
        raise HTTPException(status_code=422, detail=f"Unknown catalog state: {state}")
    authority_key = authority.strip().lower()
    if authority_key and authority_key not in TIER1_AUTHORITY_FILTERS:
        raise HTTPException(
            status_code=422, detail=f"Unknown Tier 1 authority: {authority}"
        )
    read_model = _read_model()
    candidates = filter_catalog_candidates(
        read_model,
        role=role_key,
        provider=provider,
        channel=channel,
        tier=tier,
        state=state_key,
        tier1_authority=authority_key,
        configured=configured,
    )
    candidates = attach_calibration_gates(candidates)
    return ModelRoleCandidatesResponse(
        schema_version=str(read_model["schema_version"]),
        score_version=str(read_model["score_version"]),
        content_hash=str(read_model["content_hash"]),
        observed_at=str(read_model["observed_at"]),
        rollout=str(read_model["rollout"]),
        canonical_role=role_key,
        compatibility_context={
            "run_profile": None,
            "criticality": "medium",
            "data_class": "public",
            "required_capabilities": "role_defaults",
            "projection": "base_role_score",
            "contextual_endpoint": "POST /api/model-catalog/selection",
        },
        counts={
            "candidates": len(candidates),
            "auto_eligible": sum(
                1
                for item in candidates
                if (item.get("role_evaluation") or {})
                .get("score", {})
                .get("auto_eligible")
                is True
            ),
        },
        tier1_coverage=summarize_tier1_authority(candidates),
        candidates=candidates,
    )


@router.post("/selection", response_model=ModelSelectionResponse)
async def select_model_for_role(
    request: Request, body: ModelSelectionRequest
) -> ModelSelectionResponse:
    """Ranking contextual compartido; esta consulta no muta defaults."""
    _require_api_auth_request(request)
    role_key = _validated_role(body.role)
    db_path = resolve_runtime_dir(get_current_workspace()) / "aiteam.db"
    projection = contextual_model_selection(
        db_path,
        role=role_key,
        issue_id=body.issue_id,
        run_profile=body.run_profile,
        criticality=body.criticality,
        data_class=body.data_class,
        required_capabilities=body.required_capabilities,
        read_model=_read_model(),
    )
    return ModelSelectionResponse(**projection)


@router.post("/selection/shadow")
async def shadow_model_default(
    request: Request, body: ShadowModelSelectionRequest
) -> dict[str, Any]:
    """Persiste una decisión M.7 reproducible sin mutar equipos ni defaults."""
    _require_api_auth_request(request)
    role_key = _validated_role(body.role)
    db_path = resolve_runtime_dir(get_current_workspace()) / "aiteam.db"
    projection = contextual_model_selection(
        db_path,
        role=role_key,
        issue_id=body.issue_id,
        run_profile=body.run_profile,
        criticality=body.criticality,
        data_class=body.data_class,
        required_capabilities=body.required_capabilities,
        read_model=_read_model(),
    )
    try:
        decision = evaluate_shadow_model_default(
            db_path,
            selection_scope=body.selection_scope,
            role=role_key,
            issue_id=body.issue_id,
            current_profile_id=body.current_profile_id,
            current_model=body.current_model,
            projection=projection,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except sqlite3.OperationalError as exc:
        status = 503 if "no such table" in str(exc).lower() else 500
        raise HTTPException(status_code=status, detail=str(exc))
    return {"success": True, **decision}
