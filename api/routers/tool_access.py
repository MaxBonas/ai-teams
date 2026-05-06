from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from api.utils import PROJECT_ROOT, _require_api_auth_request, _workspace_from_request, get_current_workspace, resolve_runtime_dir
from aiteam.db.tool_access import list_tool_access
from aiteam.tools.catalog import CAPABILITY_CATALOG, DEFAULT_CAPABILITIES_BY_ROLE

router = APIRouter()


@router.get("/api/tools/catalog")
async def get_tools_catalog(request: Request):
    """Return the canonical capability catalog and per-role defaults."""
    _require_api_auth_request(request)
    return {
        "success": True,
        "catalog": CAPABILITY_CATALOG,
        "role_defaults": DEFAULT_CAPABILITIES_BY_ROLE,
    }


@router.get("/api/tool-access")
async def get_tool_access(
    request: Request,
    run_id: str | None = None,
    agent_id: str | None = None,
    issue_id: str | None = None,
    decision: str | None = None,
    limit: int = 100,
):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        rows = list_tool_access(
            db,
            run_id=run_id,
            agent_id=agent_id,
            issue_id=issue_id,
            decision=decision,
            limit=limit,
        )
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "tool_access": [_decode(row) for row in rows]}


def _db(request: Request) -> Path:
    ws = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return resolve_runtime_dir(ws, PROJECT_ROOT) / "aiteam.db"


def _decode(row: dict) -> dict:
    out = dict(row)
    for key, value in list(out.items()):
        if key.endswith("_json") and isinstance(value, str):
            try:
                out[key[:-5]] = json.loads(value)
            except Exception:
                out[key[:-5]] = value
    return out


def _schema_err(exc: sqlite3.OperationalError) -> HTTPException:
    if "no such table" in str(exc).lower():
        return HTTPException(status_code=503, detail="Schema not available")
    return HTTPException(status_code=500, detail=str(exc))
