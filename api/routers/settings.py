"""API endpoints for application-level settings (projects root, etc.)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from aiteam.project_hygiene import observe_project_hygiene
from aiteam.user_config import (
    get_app_settings,
    get_effective_app_settings,
    update_app_settings,
)
from api.utils import _require_api_auth_request, get_configured_projects_root

router = APIRouter()


class SettingsPayload(BaseModel):
    projects_root: str | None = None


class HygienePreviewPayload(BaseModel):
    projects_root: str


@router.get("/api/settings")
async def get_settings(request: Request):
    """Return current application settings."""
    _require_api_auth_request(request)
    raw = get_app_settings()
    effective = get_effective_app_settings()
    configured_root = str(get_configured_projects_root().as_posix())
    configured = bool(effective["values"].get("projects_root"))
    return {
        "projects_root": raw.get("projects_root") or "",
        "projects_root_effective": configured_root,
        "projects_root_source": effective["provenance"].get("projects_root"),
        "configured": configured,
        "project_hygiene": observe_project_hygiene(
            Path(configured_root),
            configured=configured,
        ),
    }


@router.post("/api/settings")
async def update_settings(payload: SettingsPayload, request: Request):
    """Persist application settings changes."""
    _require_api_auth_request(request)
    updates: dict = {}

    if payload.projects_root is not None:
        raw_path = payload.projects_root.strip()
        if raw_path:
            resolved = Path(raw_path).resolve()
            if not resolved.is_absolute():
                raise HTTPException(status_code=400, detail="projects_root must be an absolute path.")
            updates["projects_root"] = str(resolved)
        else:
            updates["projects_root"] = ""

    if updates:
        update_app_settings(updates)

    current = get_app_settings()
    effective = get_effective_app_settings()
    configured = bool(effective["values"].get("projects_root"))
    effective_root = get_configured_projects_root()
    return {
        "success": True,
        "projects_root": current.get("projects_root") or "",
        "projects_root_effective": str(effective_root.as_posix()),
        "projects_root_source": effective["provenance"].get("projects_root"),
        "configured": configured,
        "project_hygiene": observe_project_hygiene(
            effective_root,
            configured=configured,
        ),
    }


@router.post("/api/settings/project-hygiene/preview")
async def preview_project_hygiene(
    payload: HygienePreviewPayload,
    request: Request,
):
    """Inspect a proposed root without persisting or creating it."""
    _require_api_auth_request(request)
    raw_path = payload.projects_root.strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="projects_root is required.")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise HTTPException(
            status_code=400,
            detail="projects_root must be an absolute path.",
        )
    return {
        "success": True,
        "projects_root": str(candidate),
        "persisted": False,
        "project_hygiene": observe_project_hygiene(candidate, configured=True),
    }
