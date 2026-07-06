from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from api.utils import PROJECT_ROOT, _require_api_auth_request, _workspace_from_request, get_current_workspace, resolve_runtime_dir

router = APIRouter()


@router.get("/api/timeline")
async def get_timeline(
    request: Request,
    issue_id: str | None = None,
    type: str | None = None,
    actor: str | None = None,
    since: str | None = None,
    limit: int = 200,
    order: str = "asc",
):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        items = list_timeline(
            db,
            issue_id=issue_id,
            type=type,
            actor=actor,
            since=since,
            limit=limit,
            order=order,
        )
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "items": items}


def list_timeline(
    db_path: Path,
    *,
    issue_id: str | None = None,
    type: str | None = None,
    actor: str | None = None,
    since: str | None = None,
    limit: int = 200,
    order: str = "asc",
) -> list[dict[str, Any]]:
    direction = "DESC" if str(order).lower() == "desc" else "ASC"
    capped_limit = max(1, min(int(limit), 500))
    sql = f"""
        WITH timeline_items AS (
            SELECT
                'issue:' || id AS id,
                id AS issue_id,
                created_at AS time,
                'issue' AS type,
                'Issue creada' AS title,
                title AS detail,
                COALESCE(assignee_agent_id, role, 'sistema') AS actor,
                status AS status
            FROM issues

            UNION ALL

            SELECT
                'comment:' || id AS id,
                issue_id,
                created_at AS time,
                'comment' AS type,
                CASE WHEN author_user_id IS NOT NULL THEN 'Comentario usuario' ELSE 'Comentario agente' END AS title,
                body AS detail,
                COALESCE(author_user_id, author_agent_id, 'sistema') AS actor,
                CASE WHEN source_run_id IS NOT NULL THEN 'run' ELSE NULL END AS status
            FROM issue_comments

            UNION ALL

            SELECT
                'interaction-created:' || id AS id,
                issue_id,
                created_at AS time,
                'interaction' AS type,
                COALESCE(title, kind) AS title,
                COALESCE(summary, kind) AS detail,
                COALESCE(created_by_agent_id, 'sistema') AS actor,
                status AS status
            FROM issue_thread_interactions

            UNION ALL

            SELECT
                'interaction-resolved:' || id AS id,
                issue_id,
                resolved_at AS time,
                'interaction' AS type,
                COALESCE(title, kind) || ' resuelta' AS title,
                COALESCE(summary, kind) AS detail,
                COALESCE(resolved_by_user_id, resolved_by_agent_id, 'sistema') AS actor,
                status AS status
            FROM issue_thread_interactions
            WHERE resolved_at IS NOT NULL

            UNION ALL

            SELECT
                'run:' || id AS id,
                issue_id,
                COALESCE(finished_at, started_at, created_at) AS time,
                'run' AS type,
                CASE status
                    WHEN 'skipped' THEN 'Run sin trabajo'
                    WHEN 'completed' THEN 'Run completada'
                    WHEN 'failed' THEN 'Run fallida'
                    WHEN 'running' THEN 'Run ejecutando'
                    WHEN 'queued' THEN 'Run en cola'
                    ELSE 'Run ' || status
                END AS title,
                agent_id || CASE WHEN error IS NOT NULL THEN ': ' || error ELSE '' END AS detail,
                COALESCE(invocation_source, 'run') AS actor,
                status AS status
            FROM runs

            UNION ALL

            SELECT
                'activity:' || activity_log.id AS id,
                COALESCE(
                    CASE WHEN activity_log.target_type = 'issue' THEN activity_log.target_id ELSE NULL END,
                    runs.issue_id,
                    interaction_targets.issue_id,
                    comment_targets.issue_id
                ) AS issue_id,
                activity_log.created_at AS time,
                'activity' AS type,
                activity_log.action AS title,
                COALESCE(activity_log.target_type || ':' || activity_log.target_id, activity_log.action) AS detail,
                COALESCE(activity_log.actor_user_id, activity_log.actor_agent_id, 'sistema') AS actor,
                activity_log.target_type AS status
            FROM activity_log
            LEFT JOIN runs ON runs.id = activity_log.run_id
            LEFT JOIN issue_thread_interactions AS interaction_targets
                ON activity_log.target_type = 'interaction'
               AND interaction_targets.id = activity_log.target_id
            LEFT JOIN issue_comments AS comment_targets
                ON activity_log.target_type = 'comment'
               AND comment_targets.id = activity_log.target_id

            UNION ALL

            SELECT
                'cost:' || id AS id,
                issue_id,
                created_at AS time,
                'cost' AS type,
                'Coste registrado' AS title,
                agent_id || ': ' || cost_cents || ' cents' AS detail,
                COALESCE(agent_id, provider, 'finops') AS actor,
                period AS status
            FROM cost_events

            UNION ALL

            SELECT
                'tool:' || id AS id,
                issue_id,
                created_at AS time,
                'tool' AS type,
                tool_name AS title,
                COALESCE(reason, decision) AS detail,
                COALESCE(agent_id, 'tooling') AS actor,
                decision AS status
            FROM tool_access
        )
        SELECT *
        FROM timeline_items
        WHERE time IS NOT NULL
          AND (? IS NULL OR issue_id = ?)
          AND (? IS NULL OR type = ?)
          AND (? IS NULL OR actor = ?)
          AND (? IS NULL OR time >= ?)
        ORDER BY time {direction}, id {direction}
        LIMIT ?
    """
    params = [issue_id, issue_id, type, type, actor, actor, since, since, capped_limit]
    with contextlib.closing(sqlite3.connect(str(db_path), timeout=20.0)) as conn:
        conn.row_factory = sqlite3.Row
        items = [dict(row) for row in conn.execute(sql, params).fetchall()]
    return _collapse_failed_runs(items)


_RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit")


def _failed_run_cause(detail: str) -> str:
    text = str(detail or "").lower()
    if any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return "rate_limit"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    return "other"


def _failed_run_title(cause: str, count: int) -> str:
    base = {
        "rate_limit": "Run fallida — rate limit del proveedor",
        "timeout": "Run fallida — timeout del proveedor",
    }.get(cause, "Run fallida")
    return f"{base} (x{count})" if count > 1 else base


def _collapse_failed_runs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse consecutive failed runs of the same issue/cause into one item.

    A provider outage produces a burst of identical "Run fallida" cards that
    drowns the actual story of the run. The collapsed item carries ``count``
    and a cause-labelled title ("rate limit del proveedor (x15)").
    """
    out: list[dict[str, Any]] = []
    last_cause: str | None = None
    for item in items:
        if item.get("type") == "run" and item.get("status") == "failed":
            cause = _failed_run_cause(str(item.get("detail") or ""))
            prev = out[-1] if out else None
            if (
                prev is not None
                and prev.get("type") == "run"
                and prev.get("status") == "failed"
                and prev.get("issue_id") == item.get("issue_id")
                and last_cause == cause
            ):
                prev["count"] = int(prev.get("count") or 1) + 1
                prev["title"] = _failed_run_title(cause, prev["count"])
                continue
            grouped = dict(item)
            grouped["count"] = 1
            grouped["title"] = _failed_run_title(cause, 1)
            out.append(grouped)
            last_cause = cause
        else:
            out.append(item)
            last_cause = None
    return out


def _db(request: Request) -> Path:
    ws = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return resolve_runtime_dir(ws, PROJECT_ROOT) / "aiteam.db"


def _schema_err(exc: sqlite3.OperationalError) -> HTTPException:
    if "no such table" in str(exc).lower():
        return HTTPException(status_code=503, detail="Schema not available")
    return HTTPException(status_code=500, detail=str(exc))
