"""API endpoints for application-level settings (projects root, etc.)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.utils import _require_api_auth_request, get_configured_projects_root
from aiteam.user_config import (
    get_app_settings,
    get_effective_app_settings,
    update_app_settings,
)

router = APIRouter()


class SettingsPayload(BaseModel):
    projects_root: str | None = None


@router.get("/api/settings")
async def get_settings(request: Request):
    """Return current application settings."""
    _require_api_auth_request(request)
    raw = get_app_settings()
    effective = get_effective_app_settings()
    configured_root = str(get_configured_projects_root().as_posix())
    return {
        "projects_root": raw.get("projects_root") or "",
        "projects_root_effective": configured_root,
        "projects_root_source": effective["provenance"].get("projects_root"),
        "configured": bool(raw.get("projects_root")),
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
    return {
        "success": True,
        "projects_root": current.get("projects_root") or "",
        "projects_root_effective": str(get_configured_projects_root().as_posix()),
        "projects_root_source": effective["provenance"].get("projects_root"),
        "configured": bool(current.get("projects_root")),
    }
