from __future__ import annotations

import json
import contextlib
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.utils import (
    PROJECT_ROOT,
    _require_api_auth_request,
    _workspace_from_request,
    require_configured_workspace,
    get_current_workspace,
    resolve_runtime_dir,
)
from aiteam.db.activity_log import log_activity
from aiteam.db.issues import checkout_issue
from aiteam.db.liveness import reconcile_unassigned_role_issues, reconcile_unqueued_assigned_issues
from aiteam.db.wakeups import claim_next_wakeup, enqueue_wakeup, finish_wakeup, reconcile_stale_wakeups
from aiteam.adapters.registry import build_default_registry
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler
from aiteam.project_adapters import reconcile_project_agent_policy


router = APIRouter()


class CheckoutIssueRequest(BaseModel):
    agent_id: str
    run_id: str
    expected_statuses: list[str] = Field(default_factory=lambda: ["todo", "backlog"])
    locked_at: str | None = None


class EnqueueWakeupRequest(BaseModel):
    agent_id: str
    source: str = "manual"
    reason: str = "manual"
    trigger_detail: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class ClaimWakeupRequest(BaseModel):
    agent_id: str | None = None
    claimed_at: str | None = None


class FinishWakeupRequest(BaseModel):
    status: str
    run_id: str | None = None
    error: str | None = None
    finished_at: str | None = None


class RunOnceRequest(BaseModel):
    agent_id: str | None = None
    max_runs: int = 10
    include_new_wakeups: bool = False


@router.post("/api/issues/{issue_id}/checkout")
async def post_issue_checkout(issue_id: str, payload: CheckoutIssueRequest, request: Request):
    _require_api_auth_request(request)
    db_path = _db_path_from_request(request)
    try:
        row = checkout_issue(
            db_path,
            issue_id=issue_id,
            agent_id=payload.agent_id,
            expected_statuses=payload.expected_statuses,
            run_id=payload.run_id,
            locked_at=payload.locked_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid checkout reference: {exc}") from exc
    except sqlite3.OperationalError as exc:
        raise _schema_unavailable(exc) from exc
    if row is None:
        raise HTTPException(status_code=409, detail="Issue checkout conflict")
    log_activity(
        db_path,
        action="issue.checkout",
        target_type="issue",
        target_id=issue_id,
        actor_agent_id=payload.agent_id,
        run_id=payload.run_id,
        payload={"expected_statuses": payload.expected_statuses},
    )
    return {"success": True, "issue": _decode_json_columns(row)}


@router.get("/api/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    _require_api_auth_request(request)
    db_path = _db_path_from_request(request)
    try:
        row = _fetch_one(db_path, "SELECT * FROM runs WHERE id = ?", (run_id,))
    except sqlite3.OperationalError as exc:
        raise _schema_unavailable(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    events = _fetch_all(
        db_path,
        "SELECT * FROM run_events WHERE run_id = ? ORDER BY seq ASC, created_at ASC",
        (run_id,),
    )
    return {
        "success": True,
        "run": _decode_json_columns(row),
        "events": [_decode_json_columns(event) for event in events],
    }


@router.get("/api/wakeup-requests")
async def list_wakeup_requests(
    request: Request,
    agent_id: str | None = None,
    status: str | None = None,
    reason: str | None = None,
    limit: int = 100,
):
    _require_api_auth_request(request)
    db_path = _db_path_from_request(request)
    filters: list[str] = []
    params: list[Any] = []
    if agent_id:
        filters.append("agent_id = ?")
        params.append(agent_id)
    if status:
        filters.append("status = ?")
        params.append(status)
    if reason:
        filters.append("reason = ?")
        params.append(reason)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(max(1, min(int(limit), 500)))
    try:
        rows = _fetch_all(
            db_path,
            f"SELECT * FROM wakeup_requests {where} ORDER BY requested_at DESC LIMIT ?",
            tuple(params),
        )
    except sqlite3.OperationalError as exc:
        raise _schema_unavailable(exc) from exc
    return {"success": True, "wakeup_requests": [_decode_json_columns(r) for r in rows]}


@router.post("/api/wakeup-requests")
async def post_wakeup_request(payload: EnqueueWakeupRequest, request: Request):
    _require_api_auth_request(request)
    require_configured_workspace(request)
    db_path = _db_path_from_request(request)
    try:
        row = enqueue_wakeup(
            db_path,
            agent_id=payload.agent_id,
            source=payload.source,
            reason=payload.reason,
            trigger_detail=payload.trigger_detail,
            payload=payload.payload,
            idempotency_key=payload.idempotency_key,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid wakeup reference: {exc}") from exc
    except sqlite3.OperationalError as exc:
        raise _schema_unavailable(exc) from exc
    log_activity(
        db_path,
        action="wakeup.enqueued",
        target_type="wakeup",
        target_id=row["id"],
        actor_user_id="user",
        payload={"agent_id": payload.agent_id, "source": payload.source, "reason": payload.reason},
    )
    return {"success": True, "wakeup_request": _decode_json_columns(row)}


@router.post("/api/wakeup-requests/claim")
async def post_claim_wakeup(payload: ClaimWakeupRequest, request: Request):
    _require_api_auth_request(request)
    db_path = _db_path_from_request(request)
    try:
        row = claim_next_wakeup(
            db_path,
            agent_id=payload.agent_id,
            claimed_at=payload.claimed_at,
        )
    except sqlite3.OperationalError as exc:
        raise _schema_unavailable(exc) from exc
    if row is None:
        return {"success": False, "wakeup_request": None}
    log_activity(
        db_path,
        action="wakeup.claimed",
        target_type="wakeup",
        target_id=row["id"],
        actor_agent_id=row.get("agent_id"),
        payload={"agent_id": row.get("agent_id")},
    )
    return {"success": True, "wakeup_request": _decode_json_columns(row)}


@router.patch("/api/wakeup-requests/{wakeup_id}")
async def patch_wakeup_request(wakeup_id: str, payload: FinishWakeupRequest, request: Request):
    _require_api_auth_request(request)
    db_path = _db_path_from_request(request)
    try:
        row = finish_wakeup(
            db_path,
            wakeup_id=wakeup_id,
            status=payload.status,
            run_id=payload.run_id,
            error=payload.error,
            finished_at=payload.finished_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid wakeup reference: {exc}") from exc
    except sqlite3.OperationalError as exc:
        raise _schema_unavailable(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Wakeup request not found or not finishable")
    log_activity(
        db_path,
        action=f"wakeup.{payload.status}",
        target_type="wakeup",
        target_id=row["id"],
        actor_agent_id=row.get("agent_id"),
        run_id=payload.run_id,
        payload={"status": payload.status, "error": payload.error},
    )
    return {"success": True, "wakeup_request": _decode_json_columns(row)}


@router.post("/api/control-plane/run-once")
async def post_control_plane_run_once(payload: RunOnceRequest, request: Request):
    """Drain queued wakeups for the request workspace.

    The background heartbeat loop is intentionally simple, but this endpoint is
    the user-facing immediate path: after creating or changing a project from
    the UI, run the queue against that project's own `.aiteam/aiteam.db`.
    """

    _require_api_auth_request(request)
    db_path = _db_path_from_request(request)
    scheduler = HeartbeatScheduler(db_path)
    executor = RunExecutor(db_path, build_default_registry())
    max_runs = max(1, min(int(payload.max_runs or 1), 50))
    dispatched: list[dict[str, Any]] = []

    try:
        reconciled = reconcile_stale_wakeups(db_path)
        repaired_agent_ids = reconcile_project_agent_policy(db_path)
        enqueued_issue_ids = [
            *reconcile_unassigned_role_issues(db_path),
            *reconcile_unqueued_assigned_issues(db_path),
        ]
        wakeup_scope = None
        if not payload.include_new_wakeups:
            wakeup_scope = _queued_wakeup_ids(db_path, agent_id=payload.agent_id, limit=max_runs)
        for _ in range(max_runs):
            result = scheduler.dispatch_next(agent_id=payload.agent_id, wakeup_ids=wakeup_scope)
            if result is None:
                break
            executor.execute(result)
            dispatched.append(
                {
                    "wakeup_request": _decode_json_columns(result.wakeup_request),
                    "run": _decode_json_columns(result.run),
                }
            )
    except sqlite3.OperationalError as exc:
        raise _schema_unavailable(exc) from exc

    log_activity(
        db_path,
        action="control_plane.run_once",
        target_type="control_plane",
        target_id=payload.agent_id or "all",
        actor_user_id="user",
        payload={
            "agent_id": payload.agent_id,
            "max_runs": max_runs,
            "include_new_wakeups": payload.include_new_wakeups,
            "reconciled_wakeup_count": len(reconciled),
            "repaired_agent_count": len(repaired_agent_ids),
            "enqueued_issue_count": len(enqueued_issue_ids),
            "dispatched_count": len(dispatched),
        },
    )

    return {
        "success": True,
        "reconciled_wakeup_ids": reconciled,
        "repaired_agent_ids": repaired_agent_ids,
        "enqueued_issue_ids": enqueued_issue_ids,
        "dispatched_count": len(dispatched),
        "dispatched": dispatched,
    }


def _db_path_from_request(request: Request) -> Path:
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
    return runtime_dir / "aiteam.db"


def _fetch_one(db_path: Path, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    with contextlib.closing(sqlite3.connect(str(db_path), timeout=20.0)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None


def _fetch_all(db_path: Path, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with contextlib.closing(sqlite3.connect(str(db_path), timeout=20.0)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


def _queued_wakeup_ids(db_path: Path, *, agent_id: str | None, limit: int) -> list[str]:
    filters = ["status = 'queued'"]
    params: list[Any] = []
    if agent_id:
        filters.append("agent_id = ?")
        params.append(agent_id)
    params.append(max(1, int(limit)))
    with contextlib.closing(sqlite3.connect(str(db_path), timeout=20.0)) as conn:
        rows = conn.execute(
            f"""
            SELECT id
            FROM wakeup_requests
            WHERE {' AND '.join(filters)}
            ORDER BY requested_at ASC, id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [str(row[0]) for row in rows]


def _decode_json_columns(row: dict[str, Any]) -> dict[str, Any]:
    output = dict(row)
    for key, value in list(output.items()):
        if not key.endswith("_json") or not isinstance(value, str):
            continue
        public_key = key[: -len("_json")]
        try:
            output[public_key] = json.loads(value)
        except Exception:
            output[public_key] = value
    return output


def _schema_unavailable(exc: sqlite3.OperationalError) -> HTTPException:
    detail = str(exc)
    if "no such table" in detail.lower():
        return HTTPException(status_code=503, detail="Control-plane v2 schema is not available")
    return HTTPException(status_code=500, detail=detail)
