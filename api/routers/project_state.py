from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from api.routers.timeline import list_timeline
from api.utils import (
    PROJECT_ROOT,
    _require_api_auth_request,
    _workspace_from_request,
    get_current_workspace,
    resolve_runtime_dir,
)
from aiteam.db.agents import list_agents
from aiteam.db.documents import get_document
from aiteam.db.issues import list_issues

router = APIRouter()


@router.get("/api/project/state")
async def get_project_state(
    request: Request,
    selected_issue_id: str | None = None,
    timeline_type: str | None = None,
    since: str | None = None,
    timeline_limit: int = 300,
    runs_limit: int = 100,
):
    """Return the cockpit snapshot in one request.

    `since` is accepted as a cursor for append-only timeline consumers. The
    current cockpit still needs full issues/agents/runs snapshots for counts
    and selection, so those remain complete and capped.
    """
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    db = resolve_runtime_dir(workspace, PROJECT_ROOT) / "aiteam.db"
    try:
        issues = list_issues(db, limit=500)
        agents = [_decode_json_fields(row) for row in list_agents(db, limit=500)]
        runs = [_decode_json_fields(row) for row in _fetch_runs(db, limit=runs_limit)]
        timeline = list_timeline(
            db,
            type=timeline_type or None,
            since=since or None,
            limit=timeline_limit,
            order="desc",
        )
        comments = _fetch_comments(db, issue_ids=[str(row["id"]) for row in issues])
        interactions = [_decode_json_fields(row) for row in _fetch_interactions(db, issue_ids=[str(row["id"]) for row in issues])]
        selected_id = _select_issue_id(issues, selected_issue_id)
        plan_document = get_document(db, issue_id=selected_id, key="plan") if selected_id else None
        if plan_document is None and selected_id and selected_id != "issue:intake":
            plan_document = get_document(db, issue_id="issue:intake", key="plan")
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)

    return {
        "success": True,
        "workspace": str(workspace.as_posix()) if workspace.resolve() != PROJECT_ROOT.resolve() else "",
        "configured": workspace.resolve() != PROJECT_ROOT.resolve(),
        "cursor": _state_cursor(issues=issues, agents=agents, runs=runs, timeline=timeline),
        "issues": issues,
        "agents": agents,
        "runs": runs,
        "timeline": timeline,
        "comments": comments,
        "interactions": interactions,
        "selected_issue_id": selected_id,
        "plan_document": plan_document,
    }


def _fetch_runs(db: Path, *, limit: int) -> list[dict[str, Any]]:
    capped = max(1, min(int(limit), 500))
    with contextlib.closing(_connect(db)) as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (capped,)).fetchall()
        return [dict(row) for row in rows]


# Payload caps: the whole point of this endpoint is bounded polling. Select
# the NEWEST rows (inner DESC + LIMIT) and present them chronologically —
# ordering ASC before the LIMIT would freeze the window on the oldest rows
# once a project outgrows the cap (the exact bug the chat feed had).
_COMMENTS_CAP = 600
_INTERACTIONS_CAP = 300


def _fetch_capped_newest(
    db: Path, *, table: str, issue_ids: list[str], cap: int
) -> list[dict[str, Any]]:
    """Newest *cap* rows for the given issues, presented chronologically.

    rowid must be aliased inside the inner select — a derived table does not
    carry the implicit rowid, so the outer ORDER BY cannot reference it.
    """
    if not issue_ids:
        return []
    placeholders = ", ".join("?" for _ in issue_ids)
    with contextlib.closing(_connect(db)) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM (
                SELECT *, rowid AS _rid
                FROM {table}
                WHERE issue_id IN ({placeholders})
                ORDER BY created_at DESC, _rid DESC
                LIMIT {int(cap)}
            )
            ORDER BY created_at ASC, _rid ASC
            """,
            issue_ids,
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("_rid", None)
        out.append(item)
    return out


def _fetch_comments(db: Path, *, issue_ids: list[str]) -> list[dict[str, Any]]:
    return _fetch_capped_newest(db, table="issue_comments", issue_ids=issue_ids, cap=_COMMENTS_CAP)


def _fetch_interactions(db: Path, *, issue_ids: list[str]) -> list[dict[str, Any]]:
    return _fetch_capped_newest(db, table="issue_thread_interactions", issue_ids=issue_ids, cap=_INTERACTIONS_CAP)


def _select_issue_id(issues: list[dict[str, Any]], selected_issue_id: str | None) -> str:
    if selected_issue_id and any(str(row.get("id")) == selected_issue_id for row in issues):
        return selected_issue_id
    return str(issues[0].get("id") or "") if issues else ""


def _state_cursor(
    *,
    issues: list[dict[str, Any]],
    agents: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
) -> str | None:
    values: list[str] = []
    for collection, keys in (
        (issues, ("updated_at", "created_at")),
        (agents, ("updated_at", "created_at")),
        (runs, ("finished_at", "started_at", "created_at")),
        (timeline, ("time",)),
    ):
        for row in collection:
            for key in keys:
                value = str(row.get(key) or "").strip()
                if value:
                    values.append(value)
                    break
    return max(values) if values else None


def _decode_json_fields(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key, value in list(out.items()):
        if key.endswith("_json") and isinstance(value, str):
            try:
                out[key[:-5]] = json.loads(value)
            except Exception:
                out[key[:-5]] = value
    return out


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db), timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _schema_err(exc: sqlite3.OperationalError) -> HTTPException:
    if "no such table" in str(exc).lower():
        return HTTPException(status_code=503, detail="Schema not available")
    return HTTPException(status_code=500, detail=str(exc))
