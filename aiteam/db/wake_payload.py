from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


def _parse_agent_report(body: str) -> dict[str, str] | None:
    """Extract the ---AGENT-REPORT--- structured block from a comment body.

    Returns a dict of key→value strings, or None if the marker is absent.
    Example block::

        ---AGENT-REPORT---
        role: qa
        result: blocked
        issue_status: blocked
        next_owner: engineer
        tech_match: no
        blocker: engineer used Python/pygame instead of HTML/JS
        evidence: leaderboard.py:1
    """
    marker = "---AGENT-REPORT---"
    idx = body.find(marker)
    if idx == -1:
        return None
    block = body[idx + len(marker):]
    report: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("---"):
            break  # stop at the next section delimiter
        if not line or line.startswith("#"):
            continue  # skip blank lines and comments within the block
        m = re.match(r"^([a-z_]+)\s*:\s*(.*)$", line)
        if m:
            report[m.group(1)] = m.group(2).strip()
    return report or None


def build_wake_payload(
    db_path: Path,
    *,
    issue_id: str,
    comment_id: str | None = None,
    run_id: str | None = None,
    max_comments: int = 10,
) -> dict[str, Any]:
    """Return a compact context dict suitable for AITEAM_WAKE_PAYLOAD_JSON.

    Paperclip-style: give the agent enough to start without extra API calls.
    - Issue summary (title, description, status, assignee, role, complexity)
    - Latest N comments ordered desc (most recent context first)
    - Triggering comment highlighted if comment_id is set
    - Pending interactions
    - Plan document body if it exists (key='plan')
    - Fallback_fetch_needed flag when thread is long

    The payload is intentionally compact: full body only for the trigger comment
    and the last few; older comments are truncated.
    """
    with contextlib.closing(_connect(db_path)) as conn:
        issue_row = conn.execute(
            """
            SELECT id, title, description, status, role, complexity, criticality,
                   assignee_agent_id, parent_id, priority, created_at, updated_at
            FROM issues WHERE id = ?
            """,
            (issue_id,),
        ).fetchone()

        if issue_row is None:
            return {"issue_id": issue_id, "error": "issue_not_found", "fallback_fetch_needed": True}

        issue = dict(issue_row)

        # Comments: most recent first, capped
        comment_rows = conn.execute(
            """
            SELECT id, body, author_agent_id, author_user_id, source_run_id, created_at
            FROM issue_comments
            WHERE issue_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (issue_id, max_comments + 1),
        ).fetchall()

        total_comments = conn.execute(
            "SELECT COUNT(*) FROM issue_comments WHERE issue_id = ?", (issue_id,)
        ).fetchone()[0]

        has_more_comments = total_comments > max_comments
        shown = list(comment_rows)[:max_comments]

        comments = []
        for row in reversed(shown):  # chronological order for context
            c = dict(row)
            body = str(c.get("body") or "")
            is_trigger = comment_id and c["id"] == comment_id
            comments.append({
                "id": c["id"],
                "author": c.get("author_user_id") or c.get("author_agent_id") or "system",
                "created_at": c.get("created_at"),
                "body": body if (is_trigger or len(body) <= 800) else body[:800] + "…",
                "is_trigger": bool(is_trigger),
            })

        # Pending interactions
        interaction_rows = conn.execute(
            """
            SELECT id, kind, status, title, summary, created_at
            FROM issue_thread_interactions
            WHERE issue_id = ? AND status = 'pending'
            ORDER BY created_at ASC
            """,
            (issue_id,),
        ).fetchall()
        interactions = [dict(r) for r in interaction_rows]

        # Plan document (key='plan')
        plan_row = conn.execute(
            "SELECT title, body, revision_number, current_revision_id, updated_at FROM issue_documents WHERE issue_id = ? AND key = 'plan'",
            (issue_id,),
        ).fetchone()
        plan_doc = None
        if plan_row:
            body = str(plan_row["body"] or "")
            plan_doc = {
                "title": plan_row["title"],
                "body": body if len(body) <= 2000 else body[:2000] + "…",
                "revision_number": plan_row["revision_number"],
                "current_revision_id": plan_row["current_revision_id"],
                "updated_at": plan_row["updated_at"],
                "truncated": len(body) > 2000,
            }

        # Parent summary
        parent_summary = None
        if issue.get("parent_id"):
            parent_row = conn.execute(
                "SELECT id, title, status FROM issues WHERE id = ?",
                (issue["parent_id"],),
            ).fetchone()
            if parent_row:
                parent_summary = {"id": parent_row["id"], "title": parent_row["title"], "status": parent_row["status"]}

        # Children summary — status of direct child issues (for supervisors/leads)
        children_rows = conn.execute(
            """
            SELECT i.id, i.title, i.status, i.role, i.assignee_agent_id,
                   r.liveness_state, r.liveness_reason,
                   (SELECT COUNT(*) FROM runs
                    WHERE issue_id = i.id AND status = 'completed') AS completed_run_count,
                   (SELECT body FROM issue_comments
                    WHERE issue_id = i.id
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 1) AS last_comment_body
            FROM issues i
            LEFT JOIN runs r ON r.id = (
                SELECT id FROM runs
                WHERE issue_id = i.id
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
            )
            WHERE i.parent_id = ?
            ORDER BY i.priority DESC, i.created_at ASC
            """,
            (issue_id,),
        ).fetchall()

        children_summary = []
        for r in children_rows:
            last_body = r["last_comment_body"] or ""
            agent_report = _parse_agent_report(last_body)
            children_summary.append({
                "id": r["id"],
                "title": r["title"],
                "status": r["status"],
                "role": r["role"],
                "assignee_agent_id": r["assignee_agent_id"],
                "liveness_state": r["liveness_state"],
                "liveness_reason": r["liveness_reason"],
                "completed_run_count": r["completed_run_count"] or 0,
                "last_agent_report": agent_report,
            })

    return {
        "issue_id": issue_id,
        "run_id": run_id,
        "issue": {
            "id": issue["id"],
            "title": issue["title"],
            "description": issue.get("description"),
            "status": issue["status"],
            "role": issue.get("role"),
            "complexity": issue.get("complexity"),
            "criticality": issue.get("criticality"),
            "assignee_agent_id": issue.get("assignee_agent_id"),
            "priority": issue.get("priority"),
            "parent_id": issue.get("parent_id"),
            "created_at": issue.get("created_at"),
            "updated_at": issue.get("updated_at"),
        },
        "parent": parent_summary,
        "comments": comments,
        "comments_shown": len(comments),
        "comments_total": total_comments,
        "fallback_fetch_needed": has_more_comments,
        "pending_interactions": interactions,
        "plan_document": plan_doc,
        "trigger_comment_id": comment_id,
        "children": children_summary,
    }


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
