import asyncio
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from api.utils import (
    _require_api_auth_request,
    _workspace_from_request,
    _build_project_continuity_context,
    _detect_notebooklm_status,
    _read_jsonl_records,
    _read_json_payload,
    _extract_user_message_from_task_description,
    _event_summary,
    PROJECT_ROOT,
    get_current_workspace,
)

from aiteam.dashboard import build_dashboard_payload
from aiteam.cli import build_default_orchestrator
from aiteam.pilot import compute_pilot_metrics

router = APIRouter()


def _latest_chat_run_summary(recent_events: list[dict]) -> dict[str, object]:
    def _safe_int(value: object, default: int = 0) -> int:
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = str(value or "").strip()
        if not text:
            return default
        try:
            return int(text)
        except Exception:
            return default

    latest_plan: dict | None = None
    latest_plan_payload: dict[str, object] = {}
    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_plan_created":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        latest_plan = record
        latest_plan_payload = payload
        break

    if latest_plan is None:
        return {}

    task_id = str(latest_plan_payload.get("task_id", "") or "")
    mode = str(latest_plan_payload.get("chat_mode", "") or "")
    round_budget = _safe_int(latest_plan_payload.get("round_budget", 0), 0)
    phase_count = _safe_int(latest_plan_payload.get("phase_count", 0), 0)
    delegated_count = _safe_int(latest_plan_payload.get("delegated_count", 0), 0)
    continuation_requested = bool(latest_plan_payload.get("continuation_requested", False))
    continuation_of = str(latest_plan_payload.get("continuation_of", "") or "")

    rounds_used = 0
    exhausted = False
    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_window_exhausted":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        exhausted = True
        rounds_used = _safe_int(payload.get("rounds_used", 0), 0)
        break

    if rounds_used <= 0 and task_id:
        task_prefix = f"{task_id}::"
        for record in recent_events:
            if str(record.get("event_type", "")) != "task_execution":
                continue
            payload = record.get("payload", {})
            if not isinstance(payload, dict):
                continue
            event_task_id = str(payload.get("task_id", "") or "")
            if not event_task_id.startswith(task_prefix):
                continue
            rounds_used = max(rounds_used, _safe_int(payload.get("execution_round", 0), 0))

    execution_mode = "unknown"
    placeholder_outputs = 0
    evidence_gate_rejected = False
    successful_checks: list[str] = []
    live_mode_required = False
    live_mode_rejected = False
    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_execution_mode_assessed":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        execution_mode = str(payload.get("execution_mode", "unknown") or "unknown")
        placeholder_outputs = _safe_int(payload.get("placeholder_outputs", 0), 0)
        live_mode_required = bool(payload.get("live_mode_required", False))
        break

    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_evidence_gate_rejected":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        evidence_gate_rejected = True
        break

    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_live_mode_required_rejected":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        live_mode_required = True
        live_mode_rejected = True
        break

    for record in reversed(recent_events):
        if str(record.get("event_type", "")) != "chat_quality_assessed":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id", "") or "") != task_id:
            continue
        raw_checks = payload.get("successful_checks", [])
        if isinstance(raw_checks, list):
            successful_checks = sorted(
                {
                    str(item or "").strip()
                    for item in raw_checks
                    if str(item or "").strip()
                }
            )
        break

    status = "window_exhausted" if exhausted else "completed_or_closed"
    return {
        "task_id": task_id,
        "mode": mode,
        "round_budget": round_budget,
        "rounds_used": rounds_used,
        "phase_count": phase_count,
        "delegated_count": delegated_count,
        "continuation_requested": continuation_requested,
        "continuation_of": continuation_of,
        "status": status,
        "execution_mode": execution_mode,
        "placeholder_outputs": placeholder_outputs,
        "successful_checks": successful_checks,
        "successful_check_count": len(successful_checks),
        "live_mode_required": live_mode_required,
        "live_mode_rejected": live_mode_rejected,
        "evidence_gate_rejected": evidence_gate_rejected,
        "ts": str(latest_plan.get("ts", "") or ""),
    }


def _latest_lead_user_summary(runtime_dir: Path, task_id: str) -> dict[str, object]:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return {}

    records = _read_jsonl_records(runtime_dir / "mailbox.jsonl")
    for record in reversed(records):
        if str(record.get("task_id", "") or "") != normalized_task_id:
            continue
        sender = str(record.get("sender", "") or "").strip().lower()
        recipient = str(record.get("recipient", "") or "").strip().lower()
        if sender != "team_lead" or recipient != "user":
            continue
        body = str(record.get("body", "") or "").strip()
        if not body:
            continue
        return {
            "task_id": normalized_task_id,
            "subject": str(record.get("subject", "") or ""),
            "body": body,
            "timestamp": str(record.get("timestamp", "") or ""),
        }
    return {}

@router.get("/api/dashboard")
async def get_dashboard(request: Request):
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        if not runtime_dir.exists():
            raise HTTPException(status_code=404, detail="No AI Team environment found (missing runtime/ folder).")

        # Run orchestrator initialization in a thread to avoid blocking the event loop
        def _load_data():
            orch = build_default_orchestrator(
                runtime_dir=runtime_dir,
                browser_mode="basic",
                environment="dev"
            )
            tasks = orch.taskboard.list_tasks()
            summary = orch.event_logger.summary()
            pilot_metrics = compute_pilot_metrics(tasks, summary)
            
            budget = orch.router.budget_manager
            budget_snapshot = budget.snapshot() if budget is not None else {}
            
            memory_counts = {}
            try:
                memory_counts = {
                    agent: orch.memory.count(agent)
                    for agent in orch.memory.list_agents()
                }
            except Exception:
                pass

            return build_dashboard_payload(
                runtime_dir=runtime_dir,
                tasks=tasks,
                summary=summary,
                pilot_metrics=pilot_metrics,
                budget_snapshot=budget_snapshot,
                memory_counts=memory_counts,
                environment="dev"
            )

        payload = await asyncio.to_thread(_load_data)
        return payload
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Unhandled error in aiteam router")
        return {"error": str(e)}


@router.get("/api/aiteam/state")
async def get_aiteam_state(request: Request, environment: str = "dev"):
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        if not runtime_dir.exists():
            raise HTTPException(status_code=404, detail="No runtime folder found in workspace.")

        def _load_state():
            orch = build_default_orchestrator(
                runtime_dir=runtime_dir,
                browser_mode="basic",
                environment=environment,
            )
            tasks = orch.taskboard.list_tasks()
            summary = orch.event_logger.summary()
            pilot_metrics = compute_pilot_metrics(tasks, summary)
            budget = orch.router.budget_manager
            budget_snapshot = budget.snapshot() if budget is not None else {}
            memory_counts = {
                agent: orch.memory.count(agent)
                for agent in orch.memory.list_agents()
            }
            payload = build_dashboard_payload(
                runtime_dir=runtime_dir,
                tasks=tasks,
                summary=summary,
                pilot_metrics=pilot_metrics,
                budget_snapshot=budget_snapshot,
                memory_counts=memory_counts,
                environment=environment,
            )
            continuity = _build_project_continuity_context(runtime_dir)
            recent = payload.get("recent_events", [])
            latest_chat_run = _latest_chat_run_summary(recent if isinstance(recent, list) else [])
            latest_lead_summary = _latest_lead_user_summary(runtime_dir, str(latest_chat_run.get("task_id", "") or ""))
            return {
                "task_total": payload.get("task_total", 0),
                "task_state_counts": payload.get("task_state_counts", {}),
                "summary": payload.get("summary", {}),
                "agent_latency_percentiles": payload.get("agent_latency_percentiles", {}),
                "agent_latency_trends": payload.get("agent_latency_trends", {}),
                "tuning_recommendations": payload.get("tuning_recommendations", []),
                "tasks": payload.get("tasks", [])[:80],
                "recent_events": recent[-40:],
                "last_chat_run": latest_chat_run,
                "last_lead_user_summary": latest_lead_summary,
                "notebooklm_status": _detect_notebooklm_status(runtime_dir, PROJECT_ROOT),
                "project_continuity": continuity,
            }

        return await asyncio.to_thread(_load_state)
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Unhandled error in aiteam router")
        return {"error": str(e)}


@router.get("/api/aiteam/conversations")
async def get_aiteam_conversations(request: Request, limit: int = 80):
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        mailbox_path = runtime_dir / "mailbox.jsonl"
        tasks_path = runtime_dir / "tasks.json"
        events_path = runtime_dir / "events.jsonl"
        records = _read_jsonl_records(mailbox_path)
        items: list[dict[str, object]] = []
        for record in records:
            ts = str(record.get("timestamp", ""))
            items.append(
                {
                    "timestamp": ts,
                    "sender": str(record.get("sender", "")),
                    "recipient": str(record.get("recipient", "")),
                    "subject": str(record.get("subject", "")),
                    "body": str(record.get("body", "")),
                    "task_id": str(record.get("task_id", "") or ""),
                }
            )

        existing_user_task_ids = {
            str(item.get("task_id", ""))
            for item in items
            if str(item.get("sender", "")).strip().lower() == "user"
        }

        events = _read_jsonl_records(events_path)
        started_ts: dict[str, str] = {}
        for event in events:
            if str(event.get("event_type", "")) != "task_started":
                continue
            payload = event.get("payload", {})
            if not isinstance(payload, dict):
                continue
            task_id = str(payload.get("task_id", "") or "")
            if task_id and task_id not in started_ts:
                started_ts[task_id] = str(event.get("ts", ""))

        tasks_payload = _read_json_payload(tasks_path, fallback=[])
        if isinstance(tasks_payload, list):
            for item in tasks_payload:
                if not isinstance(item, dict):
                    continue
                task_id = str(item.get("task_id", "") or "")
                if not task_id.endswith("::lead_intake"):
                    continue
                root_id = task_id.split("::", 1)[0]
                if task_id in existing_user_task_ids or root_id in existing_user_task_ids:
                    continue
                description = str(item.get("description", "") or "")
                extracted = _extract_user_message_from_task_description(description)
                if not extracted:
                    continue
                items.append(
                    {
                        "timestamp": started_ts.get(task_id, ""),
                        "sender": "user",
                        "recipient": "team_lead",
                        "subject": f"User input: {root_id}",
                        "body": extracted,
                        "task_id": root_id,
                    }
                )

        sorted_items = sorted(items, key=lambda item: str(item.get("timestamp", "")), reverse=True)
        top = sorted_items[: max(1, min(limit, 300))]
        latest_chat_run = _latest_chat_run_summary(events if isinstance(events, list) else [])
        return {
            "total": len(items),
            "items": top,
            "last_chat_run": latest_chat_run,
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Unhandled error in aiteam router")
        return {"error": str(e)}


@router.get("/api/aiteam/logs")
async def get_aiteam_logs(request: Request, limit: int = 100):
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        events_path = runtime_dir / "events.jsonl"
        tasks_path = runtime_dir / "tasks.json"

        event_records = _read_jsonl_records(events_path)
        top_records = sorted(event_records, key=lambda item: str(item.get("ts", "")), reverse=True)[
            : max(1, min(limit, 400))
        ]

        event_logs: list[dict[str, object]] = []
        task_last_ts: dict[str, str] = {}
        task_started_ts: dict[str, str] = {}
        user_output_candidates: list[dict[str, object]] = []
        for record in event_records:
            event_type = str(record.get("event_type", ""))
            payload = record.get("payload", {})
            if event_type == "task_execution" and isinstance(payload, dict):
                task_id = str(payload.get("task_id", "") or "")
                if task_id:
                    task_last_ts[task_id] = str(record.get("ts", ""))
            if event_type == "task_started" and isinstance(payload, dict):
                task_id = str(payload.get("task_id", "") or "")
                if task_id and task_id not in task_started_ts:
                    task_started_ts[task_id] = str(record.get("ts", ""))
            if event_type == "user_input" and isinstance(payload, dict):
                user_output_candidates.append(
                    {
                        "task_id": str(payload.get("task_id", "") or ""),
                        "role": "user",
                        "state": "submitted",
                        "ts": str(record.get("ts", "")),
                        "output": str(payload.get("message", "") or ""),
                    }
                )

        for record in top_records:
            event_type = str(record.get("event_type", "unknown"))
            payload = record.get("payload", {})
            payload_dict = payload if isinstance(payload, dict) else {}
            event_logs.append(
                {
                    "ts": str(record.get("ts", "")),
                    "event_type": event_type,
                    "task_id": str(payload_dict.get("task_id", "") or ""),
                    "summary": _event_summary(event_type, payload_dict),
                }
            )

        tasks_payload = _read_json_payload(tasks_path, fallback=[])
        synthetic_user_events: list[dict[str, object]] = []
        task_outputs: list[dict[str, object]] = []
        if isinstance(tasks_payload, list):
            for item in tasks_payload:
                if not isinstance(item, dict):
                    continue
                metadata = item.get("metadata", {})
                metadata_dict = metadata if isinstance(metadata, dict) else {}
                raw_output = metadata_dict.get("result") or metadata_dict.get("error") or metadata_dict.get("execution_plan_result")
                if not raw_output:
                    continue
                task_id = str(item.get("task_id", ""))
                task_outputs.append(
                    {
                        "task_id": task_id,
                        "role": str(item.get("role", "")),
                        "state": str(item.get("state", "")),
                        "ts": task_last_ts.get(task_id, ""),
                        "output": str(raw_output),
                    }
                )

            existing_user_task_ids = {
                str(item.get("task_id", ""))
                for item in user_output_candidates
            }
            existing_user_event_task_ids = {
                str(item.get("task_id", ""))
                for item in event_logs
                if str(item.get("event_type", "")) == "user_input"
            }
            for item in tasks_payload:
                if not isinstance(item, dict):
                    continue
                task_id = str(item.get("task_id", "") or "")
                if not task_id.endswith("::lead_intake"):
                    continue
                root_id = task_id.split("::", 1)[0]
                if root_id in existing_user_task_ids:
                    continue
                message = _extract_user_message_from_task_description(str(item.get("description", "") or ""))
                if not message:
                    continue
                ts_value = task_started_ts.get(task_id, task_last_ts.get(task_id, ""))
                user_output_candidates.append(
                    {
                        "task_id": root_id,
                        "role": "user",
                        "state": "submitted",
                        "ts": ts_value,
                        "output": message,
                    }
                )
                if root_id not in existing_user_event_task_ids:
                    synthetic_user_events.append(
                        {
                            "ts": ts_value,
                            "event_type": "user_input",
                            "task_id": root_id,
                            "summary": _event_summary(
                                "user_input",
                                {
                                    "task_id": root_id,
                                    "message": message,
                                },
                            ),
                        }
                    )

        task_outputs.extend(user_output_candidates)
        event_logs.extend(synthetic_user_events)
        event_logs.sort(key=lambda item: str(item.get("ts", "")), reverse=True)
        event_limit = max(1, min(limit, 400))
        
        return {
            "total": len(event_logs),
            "event_logs": event_logs[:event_limit],
            "task_outputs": task_outputs,
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Unhandled error in aiteam router")
        return {"error": str(e)}


# ── Session Audit Endpoints ─────────────────────────────────────────


@router.get("/api/aiteam/sessions")
async def list_sessions(
    request: Request,
    agent_id: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
):
    """Lista sesiones de agentes con filtros opcionales."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.agent_session import SessionStore
        store = SessionStore(runtime_dir)
        sessions = store.list_sessions(agent_id=agent_id, task_id=task_id, limit=limit)
        active = [s.to_summary_dict() for s in store.get_active_sessions()]
        return {"sessions": sessions, "active_sessions": active, "total": len(sessions)}
    except Exception as exc:
        return {"error": str(exc), "sessions": [], "active_sessions": []}


@router.get("/api/aiteam/sessions/{session_id}")
async def get_session_detail(request: Request, session_id: str):
    """Detalle completo de una sesion incluyendo todas las acciones."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.agent_session import SessionStore
        store = SessionStore(runtime_dir)
        session = store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        return session.to_dict()
    except HTTPException:
        raise
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/api/aiteam/agents/{agent_id}/activity")
async def agent_activity(request: Request, agent_id: str, limit: int = 20):
    """Timeline de actividad de un agente especifico."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.agent_session import SessionStore
        store = SessionStore(runtime_dir)
        activity = store.agent_activity(agent_id, limit=limit)
        return {"agent_id": agent_id, "activity": activity, "total": len(activity)}
    except Exception as exc:
        return {"error": str(exc), "activity": []}


@router.get("/api/aiteam/tools")
async def list_available_tools(request: Request, role: str | None = None):
    """Lista herramientas disponibles del catalogo."""
    _require_api_auth_request(request)
    try:
        catalog_path = PROJECT_ROOT / "config" / "tool_sources.catalog.json"
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.tool_dispatch import ToolDispatcher
        dispatcher = ToolDispatcher(catalog_path=catalog_path, runtime_dir=runtime_dir)
        all_tools = dispatcher.available_tools(role=role)
        enabled = [t for t in all_tools if t.enabled]
        return {
            "total": len(all_tools),
            "enabled": len(enabled),
            "tools": [
                {
                    "name": t.name,
                    "category": t.category,
                    "capabilities": t.capabilities,
                    "role_targets": t.role_targets,
                    "enabled": t.enabled,
                    "requires_approval": t.requires_approval,
                    "description": t.description,
                }
                for t in all_tools
            ],
        }
    except Exception as exc:
        return {"error": str(exc), "tools": []}


@router.get("/api/aiteam/tools/access-log")
async def tool_access_log(
    request: Request,
    agent_id: str | None = None,
    tool_name: str | None = None,
    limit: int = 50,
):
    """Historial de acceso a herramientas."""
    _require_api_auth_request(request)
    try:
        catalog_path = PROJECT_ROOT / "config" / "tool_sources.catalog.json"
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.tool_dispatch import ToolDispatcher
        dispatcher = ToolDispatcher(catalog_path=catalog_path, runtime_dir=runtime_dir)
        history = dispatcher.tool_access_history(agent_id=agent_id, tool_name=tool_name, limit=limit)
        return {"total": len(history), "access_log": history}
    except Exception as exc:
        return {"error": str(exc), "access_log": []}


@router.get("/api/aiteam/skills/usage")
async def skill_usage_stats(request: Request, limit: int = 20):
    """Estadisticas de uso de skills: veces usado, tasa de exito, ranking."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.autotools import AutoToolIntegrator
        integrator = AutoToolIntegrator(
            runtime_dir=runtime_dir,
            project_root=PROJECT_ROOT,
        )
        stats = integrator.skill_usage_stats(limit=limit)
        return {"total": len(stats), "skills": stats}
    except Exception as exc:
        return {"error": str(exc), "skills": []}


@router.get("/api/aiteam/skills/ranking/{role}")
async def skill_ranking_for_role(role: str, request: Request, limit: int = 10):
    """Ranking de skills por tasa de exito para un rol."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        from aiteam.autotools import AutoToolIntegrator
        integrator = AutoToolIntegrator(
            runtime_dir=runtime_dir,
            project_root=PROJECT_ROOT,
        )
        ranking = integrator.skill_ranking_for_role(role=role, limit=limit)
        return {"role": role, "total": len(ranking), "ranking": ranking}
    except Exception as exc:
        return {"error": str(exc), "ranking": []}


@router.get("/api/aiteam/workflow-state")
async def get_workflow_state(request: Request):
    """Estado compartido del workflow (blackboard)."""
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
        runtime_dir = workspace / "runtime"
        ws_path = runtime_dir / "workflow_state.json"
        if not ws_path.exists():
            return {"workflows": {}}
        import json
        raw = ws_path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        return {"workflows": data}
    except Exception as exc:
        return {"error": str(exc), "workflows": {}}


# ── MCP Server Management ─────────────────────────────────────

def _get_mcp_manager(request: Request):
    """Helper para obtener el MCPServerManager."""
    from aiteam.mcp_manager import MCPServerManager
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = workspace / "runtime"
    catalog_path = workspace / "config" / "tool_sources.catalog.json"
    return MCPServerManager(
        runtime_dir=runtime_dir,
        catalog_path=catalog_path,
    )


@router.get("/api/aiteam/mcp/servers")
async def list_mcp_servers(request: Request):
    """Estado de todos los servidores MCP configurados."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        return {"servers": mgr.server_status()}
    except Exception as exc:
        return {"error": str(exc), "servers": []}


@router.post("/api/aiteam/mcp/sync-catalog")
async def sync_mcp_catalog(request: Request):
    """Sincroniza MCPs del catalogo a mcp_servers.json."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        new_count = mgr.sync_from_catalog()
        return {"synced": new_count, "total": len(mgr._configs)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/aiteam/mcp/servers/{server_name}/start")
async def start_mcp_server(server_name: str, request: Request):
    """Inicia un servidor MCP."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        ok, reason = await asyncio.to_thread(mgr.start_server, server_name, 30)
        if not ok:
            raise HTTPException(status_code=400, detail=reason)
        tools = [t.name for t in mgr.list_tools(server_name)]
        return {"status": "running", "server": server_name, "tools": tools}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/aiteam/mcp/servers/{server_name}/stop")
async def stop_mcp_server(server_name: str, request: Request):
    """Detiene un servidor MCP."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        mgr.stop_server(server_name)
        return {"status": "stopped", "server": server_name}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/aiteam/mcp/servers/{server_name}/enable")
async def enable_mcp_server(server_name: str, request: Request):
    """Habilita un servidor MCP."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        enabled = mgr.enable_servers([server_name])
        if not enabled:
            raise HTTPException(status_code=404, detail=f"server '{server_name}' not found")
        return {"enabled": enabled}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/aiteam/mcp/servers/{server_name}/disable")
async def disable_mcp_server(server_name: str, request: Request):
    """Deshabilita un servidor MCP."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        disabled = mgr.disable_servers([server_name])
        if not disabled:
            raise HTTPException(status_code=404, detail=f"server '{server_name}' not found")
        return {"disabled": disabled}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/aiteam/mcp/tools")
async def list_mcp_tools(request: Request, server: str | None = None):
    """Lista herramientas disponibles en servidores MCP activos."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        tools = mgr.list_tools(server)
        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "server": t.server_name,
                    "input_schema": t.input_schema,
                }
                for t in tools
            ]
        }
    except Exception as exc:
        return {"error": str(exc), "tools": []}


@router.post("/api/aiteam/mcp/invoke")
async def invoke_mcp_tool(request: Request):
    """Invoca una herramienta MCP. Body: {server, tool, arguments?}"""
    _require_api_auth_request(request)
    try:
        body = await request.json()
        server_name = str(body.get("server", "")).strip()
        tool_name = str(body.get("tool", "")).strip()
        arguments = body.get("arguments", {})

        if not server_name or not tool_name:
            raise HTTPException(status_code=400, detail="server and tool are required")

        mgr = _get_mcp_manager(request)
        result = await asyncio.to_thread(
            mgr.invoke_tool, server_name, tool_name, arguments, None, 120
        )
        return {
            "success": result.success,
            "server": result.server_name,
            "tool": result.tool_name,
            "content": result.content,
            "text": result.text,
            "error": result.error,
            "duration_ms": result.duration_ms,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/aiteam/mcp/events")
async def mcp_event_history(request: Request, server: str | None = None, limit: int = 50):
    """Historial de eventos MCP."""
    _require_api_auth_request(request)
    try:
        mgr = _get_mcp_manager(request)
        events = mgr.event_history(server_name=server, limit=limit)
        return {"events": events}
    except Exception as exc:
        return {"error": str(exc), "events": []}

