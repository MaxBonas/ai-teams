from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.utils import PROJECT_ROOT, _require_api_auth_request, _workspace_from_request, get_current_workspace, resolve_runtime_dir
from aiteam.db.activity_log import log_activity
from aiteam.db.agents import create_agent, get_agent, list_agents, update_agent
from aiteam.db.finops import check_budget
from aiteam.user_config import assert_no_inline_secret

router = APIRouter()


class CreateAgentRequest(BaseModel):
    role: str
    name: str
    seniority: str = "standard"
    adapter_type: str | None = None
    adapter_config: dict[str, Any] = {}
    capabilities: list[str] = []
    budget_monthly_cents: int = 0
    heartbeat_interval_sec: int = 0
    supervisor_agent_id: str | None = None
    metadata: dict[str, Any] = {}


class UpdateAgentRequest(BaseModel):
    status: str | None = None
    name: str | None = None
    seniority: str | None = None
    heartbeat_interval_sec: int | None = None
    adapter_type: str | None = None
    adapter_config: dict[str, Any] | None = None
    capabilities: list[str] | None = None
    budget_monthly_cents: int | None = None
    supervisor_agent_id: str | None = None


@router.post("/api/agents")
async def post_agent(body: CreateAgentRequest, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        assert_no_inline_secret(body.adapter_config)
        row = create_agent(
            db, role=body.role, name=body.name, seniority=body.seniority,
            adapter_type=body.adapter_type, adapter_config=body.adapter_config,
            capabilities=body.capabilities, budget_monthly_cents=body.budget_monthly_cents,
            heartbeat_interval_sec=body.heartbeat_interval_sec,
            supervisor_agent_id=body.supervisor_agent_id, metadata=body.metadata,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    log_activity(
        db,
        action="agent.created",
        target_type="agent",
        target_id=row["id"],
        actor_user_id="user",
        payload={"role": row.get("role"), "name": row.get("name"), "adapter_type": row.get("adapter_type")},
    )
    return {"success": True, "agent": _decode(row)}


@router.get("/api/agents")
async def get_agents(request: Request, status: str | None = None, role: str | None = None, limit: int = 200):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        rows = list_agents(db, status=status, role=role, limit=limit)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "agents": [_decode(r) for r in rows]}


@router.get("/api/agents/{agent_id}")
async def get_agent_by_id(agent_id: str, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        row = get_agent(db, agent_id=agent_id)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"success": True, "agent": _decode(row)}


@router.patch("/api/agents/{agent_id}")
async def patch_agent(agent_id: str, body: UpdateAgentRequest, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        if body.adapter_config is not None:
            assert_no_inline_secret(body.adapter_config)
        row = update_agent(
            db, agent_id=agent_id,
            status=body.status,
            name=body.name,
            seniority=body.seniority,
            heartbeat_interval_sec=body.heartbeat_interval_sec,
            adapter_type=body.adapter_type,
            adapter_config=body.adapter_config,
            capabilities=body.capabilities,
            budget_monthly_cents=body.budget_monthly_cents,
            supervisor_agent_id=body.supervisor_agent_id,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    log_activity(
        db,
        action="agent.updated",
        target_type="agent",
        target_id=row["id"],
        actor_user_id="user",
        payload={
            "status": body.status,
            "heartbeat_interval_sec": body.heartbeat_interval_sec,
            "adapter_type": body.adapter_type,
            "adapter_config_updated": body.adapter_config is not None,
        },
    )
    return {"success": True, "agent": _decode(row)}


@router.post("/api/agents/reconcile")
async def reconcile_agents(request: Request):
    """Re-run reconcile_project_agent_policy and return repaired agent IDs.

    Safe to call any time — idempotent. Repairs placeholder adapters, upgrades
    API-only junior agents to CLI when a CLI profile is available, and ensures
    Tier 3 scout agents exist.
    """
    _require_api_auth_request(request)
    db = _db(request)
    try:
        from aiteam.project_adapters import reconcile_project_agent_policy
        repaired = reconcile_project_agent_policy(db)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "repaired": repaired}


@router.get("/api/agents/{agent_id}/budget")
async def get_agent_budget(agent_id: str, request: Request, period: str | None = None):
    """Return budget status for a single agent."""
    _require_api_auth_request(request)
    db = _db(request)
    try:
        status = check_budget(db, agent_id=agent_id, period=period or None)
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found")
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "budget": status.to_dict()}


@router.get("/api/budget")
async def get_all_budgets(request: Request, period: str | None = None):
    """Return budget status for every agent that has a monthly budget configured."""
    _require_api_auth_request(request)
    db = _db(request)
    try:
        all_agents = list_agents(db, limit=500)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    budgets = []
    for agent in all_agents:
        agent_id = agent.get("id") or ""
        if not agent_id:
            continue
        try:
            status = check_budget(db, agent_id=agent_id, period=period or None)
            budgets.append({
                **status.to_dict(),
                "agent_name": agent.get("name") or agent_id,
                "agent_role": agent.get("role") or "",
            })
        except Exception:
            pass
    return {"success": True, "budgets": budgets, "period": period}


@router.get("/api/costs/summary")
async def get_costs_summary(request: Request):
    """Aggregate real spend and estimated savings across the project's runs.

    ``estimated_savings_cents`` totals what the same runs would have cost on
    the premium adapter a senior assignment would use — the headline number
    for the cheap-workers hiring policy.
    """
    _require_api_auth_request(request)
    db = _db(request)
    try:
        with contextlib.closing(sqlite3.connect(str(db), timeout=20.0)) as conn:
            conn.row_factory = sqlite3.Row
            totals = conn.execute(
                """
                SELECT COUNT(*) AS runs,
                       COALESCE(SUM(actual_cost_cents), 0) AS actual_cost_cents,
                       COALESCE(SUM(estimated_savings_cents), 0) AS estimated_savings_cents
                FROM runs
                WHERE status IN ('completed', 'failed', 'skipped')
                """
            ).fetchone()
            by_role = conn.execute(
                """
                SELECT COALESCE(a.role, 'desconocido') AS role,
                       COUNT(*) AS runs,
                       COALESCE(SUM(r.actual_cost_cents), 0) AS actual_cost_cents,
                       COALESCE(SUM(r.estimated_savings_cents), 0) AS estimated_savings_cents
                FROM runs r
                LEFT JOIN agents a ON a.id = r.agent_id
                WHERE r.status IN ('completed', 'failed', 'skipped')
                GROUP BY COALESCE(a.role, 'desconocido')
                ORDER BY actual_cost_cents DESC, runs DESC
                """
            ).fetchall()
            by_channel = conn.execute(
                """
                SELECT COALESCE(channel, 'desconocido') AS channel,
                       COUNT(*) AS runs,
                       COALESCE(SUM(actual_cost_cents), 0) AS actual_cost_cents,
                       COALESCE(SUM(estimated_savings_cents), 0) AS estimated_savings_cents
                FROM runs
                WHERE status IN ('completed', 'failed', 'skipped')
                GROUP BY COALESCE(channel, 'desconocido')
                ORDER BY actual_cost_cents DESC, runs DESC
                """
            ).fetchall()
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {
        "success": True,
        "totals": dict(totals) if totals else {"runs": 0, "actual_cost_cents": 0, "estimated_savings_cents": 0},
        "by_role": [dict(row) for row in by_role],
        "by_channel": [dict(row) for row in by_channel],
    }


def _db(request: Request) -> Path:
    ws = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return resolve_runtime_dir(ws, PROJECT_ROOT) / "aiteam.db"

def _decode(row: dict) -> dict:
    import json
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
