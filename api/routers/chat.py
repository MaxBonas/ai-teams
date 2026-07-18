"""
Chat channel between user and Lead.

Aggregates:
  - issue_comments authored by the Lead (agent→user messages)
  - issue_comments authored by the user (user→agent messages)
  - issue_thread_interactions with status='pending' (decision requests inline)

Exposed as a single chronological feed via GET /api/chat.
POST /api/chat/message posts a user comment to issue:intake and wakes the Lead.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.utils import (
    PROJECT_ROOT,
    _require_api_auth_request,
    _workspace_from_request,
    require_configured_workspace,
    get_current_workspace,
    resolve_runtime_dir,
)
from aiteam.db.activity_log import log_activity
from aiteam.db.comments import create_comment

router = APIRouter()

INTAKE_ISSUE_ID = "issue:intake"


class ChatMessageRequest(BaseModel):
    body: str
    issue_id: str = INTAKE_ISSUE_ID


@router.get("/api/chat")
async def get_chat(request: Request, limit: int = 120):
    """Return a unified chat feed of Lead↔User messages and pending interactions."""
    _require_api_auth_request(request)
    db = _db(request)
    try:
        items = _load_chat(db, limit=limit)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return {"success": True, "messages": []}
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"success": True, "messages": items}


@router.post("/api/chat/message")
async def post_chat_message(body: ChatMessageRequest, request: Request):
    """User posts a message to the Lead.  Wakes the Lead for a response.

    If ``issue:intake`` does not yet exist (project created without an initial
    task), it is auto-bootstrapped from the first user message so the Lead has
    context to work with.
    """
    _require_api_auth_request(request)
    require_configured_workspace(request)
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="Message body is required")
    db = _db(request)

    # El objetivo de una planificación quorum se congela al crear la sesión:
    # una directiva nueva va a una Nueva tarea, nunca a mutar este objetivo.
    frozen_reason = _quorum_message_block_reason(db, issue_id=body.issue_id)
    if frozen_reason:
        raise HTTPException(status_code=409, detail=frozen_reason)

    # Auto-bootstrap: create issue:intake if it doesn't exist yet so the Lead
    # always has a rooted issue to attach comments and runs to.
    if body.issue_id == INTAKE_ISSUE_ID:
        try:
            _ensure_intake_issue(db, initial_body=body.body.strip())
        except Exception:
            pass  # best-effort; create_comment will surface the real error if needed

    try:
        comment = create_comment(
            db,
            issue_id=body.issue_id,
            body=body.body.strip(),
            author_user_id="user",
            metadata={"source": "user_chat"},
        )
    except (ValueError, sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_activity(
        db,
        action="chat.user_message",
        target_type="comment",
        target_id=comment["id"],
        actor_user_id="user",
        payload={"issue_id": body.issue_id, "body_preview": body.body[:120]},
    )

    # Wake the Lead so it picks up the user message
    try:
        _enqueue_lead_wakeup(db, issue_id=body.issue_id, comment_id=comment["id"])
    except Exception:
        pass  # Best-effort; don't fail the request if wakeup fails

    return {"success": True, "comment": comment}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _quorum_message_block_reason(db: Path, *, issue_id: str) -> str | None:
    """Impide que un prompt posterior parezca alterar un objetivo ya congelado."""
    with contextlib.closing(sqlite3.connect(str(db), timeout=20.0)) as conn:
        row = conn.execute(
            "SELECT status FROM quorum_sessions WHERE issue_id=? ORDER BY created_at DESC LIMIT 1",
            (issue_id,),
        ).fetchone()
    if row is None:
        return None
    return (
        "El objetivo de esta planificación quorum ya está congelado. "
        "Crea una Nueva tarea con perfil Lead + Quorum para el nuevo objetivo."
    )

def _load_chat(db: Path, *, limit: int = 120) -> list[dict[str, Any]]:
    # Select the NEWEST `limit` items (inner ORDER BY … DESC), then present
    # them chronologically (outer ORDER BY … ASC). Ordering ASC before the
    # LIMIT would return the OLDEST items and freeze the chat once the thread
    # grows past `limit` — the Lead's latest replies would never appear.
    sql = """
        SELECT * FROM (
            SELECT
                'comment:' || c.id          AS id,
                c.id                         AS source_id,
                'message'                    AS item_type,
                CASE
                    WHEN c.author_user_id IS NOT NULL THEN 'user'
                    ELSE 'agent'
                END                          AS sender,
                COALESCE(c.author_user_id, c.author_agent_id, 'sistema') AS author,
                c.body                       AS body,
                NULL                         AS title,
                NULL                         AS summary,
                NULL                         AS kind,
                NULL                         AS interaction_status,
                NULL                         AS payload_json,
                c.issue_id                   AS issue_id,
                c.source_run_id              AS source_run_id,
                c.created_at                 AS created_at
            FROM issue_comments c

            UNION ALL

            SELECT
                'interaction:' || i.id       AS id,
                i.id                         AS source_id,
                'interaction'                AS item_type,
                'agent'                      AS sender,
                COALESCE(i.created_by_agent_id, 'sistema') AS author,
                COALESCE(i.summary, i.kind)  AS body,
                i.title                      AS title,
                i.summary                    AS summary,
                i.kind                       AS kind,
                i.status                     AS interaction_status,
                i.payload_json               AS payload_json,
                i.issue_id                   AS issue_id,
                i.source_run_id              AS source_run_id,
                i.created_at                 AS created_at
            FROM issue_thread_interactions i

            ORDER BY created_at DESC, id DESC
            LIMIT ?
        )
        ORDER BY created_at ASC, id ASC
    """
    with contextlib.closing(sqlite3.connect(str(db), timeout=20.0)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, (limit,)).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        # Decode payload_json for interactions
        raw_payload = item.pop("payload_json", None)
        if raw_payload:
            try:
                item["payload"] = json.loads(raw_payload)
            except Exception:
                item["payload"] = {}
        else:
            item["payload"] = {}
        result.append(item)
    return result


def _enqueue_lead_wakeup(db: Path, *, issue_id: str, comment_id: str) -> None:
    """Enqueue a wakeup for the Lead agent to respond to a user chat message."""
    now = datetime.now(timezone.utc).isoformat()
    wakeup_id = str(uuid.uuid4())
    payload = json.dumps({
        "issue_id": issue_id,
        "wake_reason": "chat_message",
        "comment_id": comment_id,
    }, ensure_ascii=False)
    sql = """
        INSERT INTO wakeup_requests
            (id, agent_id, source, reason, status, payload_json, idempotency_key, coalesced_count, requested_at, created_at, updated_at)
        VALUES (?, 'role:lead', 'user_chat', 'user_chat_message', 'queued', ?, NULL, 0, ?, ?, ?)
    """
    with contextlib.closing(sqlite3.connect(str(db), timeout=20.0)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(sql, (wakeup_id, payload, now, now, now))
        conn.commit()


def _db(request: Request) -> Path:
    ws = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return resolve_runtime_dir(ws, PROJECT_ROOT) / "aiteam.db"


def _ensure_intake_issue(db: Path, *, initial_body: str) -> None:
    """Create goal:intake + issue:intake if they don't already exist.

    Called when the user sends their first chat message to a project that was
    created without an initial_task.  This gives the Lead a rooted issue so
    runs and comments can be anchored properly.
    """
    now = datetime.now(timezone.utc).isoformat()
    title = initial_body.split("\n")[0].strip()[:160] or "Tarea inicial"

    with contextlib.closing(sqlite3.connect(str(db), timeout=20.0)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        # Skip if already bootstrapped
        existing = conn.execute(
            "SELECT id FROM issues WHERE id = ?", (INTAKE_ISSUE_ID,)
        ).fetchone()
        if existing is not None:
            return

        # Create goal
        conn.execute(
            """
            INSERT OR IGNORE INTO goals (id, title, description, source, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "goal:intake",
                title,
                initial_body,
                "user_chat_bootstrap",
                json.dumps({"profile": "full_team"}, ensure_ascii=False),
                now,
                now,
            ),
        )
        # Create issue:intake assigned to the Lead
        conn.execute(
            """
            INSERT OR IGNORE INTO issues (
                id, goal_id, title, description, status, role,
                complexity, criticality, assignee_agent_id, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                INTAKE_ISSUE_ID,
                "goal:intake",
                title,
                initial_body,
                "todo",
                "lead",
                "medium",
                "medium",
                "role:lead",
                json.dumps(
                    {"source": "user_chat_bootstrap", "wake_reason": "new_project"},
                    ensure_ascii=False,
                ),
                now,
                now,
            ),
        )
        conn.commit()
