from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.utils import (
    PROJECT_ROOT,
    _require_api_auth_request,
    _workspace_from_request,
    get_current_workspace,
    resolve_runtime_dir,
)
from aiteam.db.runs import append_run_event

router = APIRouter()


class PostRunEventRequest(BaseModel):
    event_type: str
    payload: dict[str, Any] | None = None
    stream: str | None = None
    seq: int | None = None


@router.get("/api/runs")
async def list_runs(
    request: Request,
    agent_id: str | None = None,
    issue_id: str | None = None,
    status: str | None = None,
    liveness_state: str | None = None,
    limit: int = 100,
):
    _require_api_auth_request(request)
    db = _db(request)
    filters: list[str] = []
    params: list[Any] = []
    if agent_id:
        filters.append("agent_id = ?")
        params.append(agent_id)
    if issue_id:
        filters.append("issue_id = ?")
        params.append(issue_id)
    if status:
        filters.append("status = ?")
        params.append(status)
    if liveness_state:
        filters.append("liveness_state = ?")
        params.append(liveness_state)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(max(1, min(int(limit), 500)))
    try:
        rows = _fetch_all(
            db, f"SELECT * FROM runs {where} ORDER BY created_at DESC LIMIT ?", params
        )
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "runs": [_decode(r) for r in rows]}


@router.get("/api/runs/{run_id}/events")
async def get_run_events(run_id: str, request: Request, limit: int = 200):
    _require_api_auth_request(request)
    db = _db(request)
    capped = max(1, min(int(limit), 1000))
    try:
        rows = _fetch_all(
            db,
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY seq ASC LIMIT ?",
            [run_id, capped],
        )
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "run_id": run_id, "events": [_decode(r) for r in rows]}


@router.post("/api/runs/{run_id}/events")
async def post_run_event(run_id: str, body: PostRunEventRequest, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        row = append_run_event(
            db,
            run_id=run_id,
            event_type=body.event_type,
            payload=body.payload,
            stream=body.stream,
            seq=body.seq,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "event": _decode(dict(row))}


def _db(request: Request) -> Path:
    ws = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return resolve_runtime_dir(ws, PROJECT_ROOT) / "aiteam.db"


def _fetch_all(db_path: Path, sql: str, params: list) -> list[dict]:
    with contextlib.closing(sqlite3.connect(str(db_path), timeout=20.0)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _decode(row: dict) -> dict:
    out = dict(row)
    for k, v in list(out.items()):
        if k.endswith("_json") and isinstance(v, str):
            try:
                out[k[:-5]] = json.loads(v)
            except Exception:
                pass
    return out


def _schema_err(exc: sqlite3.OperationalError) -> HTTPException:
    if "no such table" in str(exc).lower():
        return HTTPException(status_code=503, detail="Schema not available")
    return HTTPException(status_code=500, detail=str(exc))
