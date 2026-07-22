from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from api.utils import (
    _require_api_auth_request,
    get_current_workspace,
    resolve_runtime_dir,
)
from aiteam.model_catalog_api import (
    CATALOG_STATE_NAMES,
    filter_catalog_candidates,
    summarize_catalog_providers,
)
from aiteam.model_catalog_service import get_current_model_catalog
from aiteam.model_selection_context import contextual_model_selection
from aiteam.model_default_rollout import evaluate_shadow_model_default
from aiteam.policies import canonical_role, role_status


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


@router.get("", response_model=ModelCatalogResponse)
async def get_model_catalog(
    request: Request,
    role: str = "",
    provider: str = "",
    channel: str = "",
    tier: str = "",
    state: str = "",
    configured: bool | None = None,
) -> ModelCatalogResponse:
    """Inventario global visible, incluidos candidatos bloqueados o inactivos."""
    _require_api_auth_request(request)
    role_key = _validated_role(role) if role else ""
    state_key = state.strip().lower()
    if state_key and state_key not in CATALOG_STATE_NAMES:
        raise HTTPException(status_code=422, detail=f"Unknown catalog state: {state}")
    read_model = _read_model()
    candidates = filter_catalog_candidates(
        read_model,
        role=role_key,
        provider=provider,
        channel=channel,
        tier=tier,
        state=state_key,
        configured=configured,
    )
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
            "configured": configured,
        },
        counts={
            "candidates": len(candidates),
            "providers": len(summarize_catalog_providers(candidates)),
        },
        providers=summarize_catalog_providers(candidates),
        runtime=dict(read_model.get("runtime") or {}),
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
    configured: bool | None = None,
) -> ModelRoleCandidatesResponse:
    """Ranking global shadow del par modelo+perfil para un rol canónico."""
    _require_api_auth_request(request)
    role_key = _validated_role(role)
    state_key = state.strip().lower()
    if state_key and state_key not in CATALOG_STATE_NAMES:
        raise HTTPException(status_code=422, detail=f"Unknown catalog state: {state}")
    read_model = _read_model()
    candidates = filter_catalog_candidates(
        read_model,
        role=role_key,
        provider=provider,
        channel=channel,
        tier=tier,
        state=state_key,
        configured=configured,
    )
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
            "selection_score": "not_active_until_M.6",
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
        candidates=candidates,
    )


@router.post("/selection", response_model=ModelSelectionResponse)
async def select_model_for_role(
    request: Request, body: ModelSelectionRequest
) -> ModelSelectionResponse:
    """Ranking contextual compartido; en M.6 aún no muta defaults productivos."""
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
