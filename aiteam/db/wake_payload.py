from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.db.agent_reports import latest_agent_report


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
                   assignee_agent_id, parent_id, priority, created_at, updated_at,
                   metadata_json
            FROM issues WHERE id = ?
            """,
            (issue_id,),
        ).fetchone()

        if issue_row is None:
            return {"issue_id": issue_id, "error": "issue_not_found", "fallback_fetch_needed": True}

        issue = dict(issue_row)
        # Surface structured acceptance criteria (set at delegation time) so
        # the assignee sees the explicit done-bar in every wake.
        try:
            _issue_meta = json.loads(issue.pop("metadata_json", None) or "{}")
        except (TypeError, ValueError):
            _issue_meta = {}
        acceptance_criteria = [
            str(item) for item in (_issue_meta.get("acceptance_criteria") or []) if str(item).strip()
        ]

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

        # Context summary document (key='context_summary') — block-based synthesis
        context_summary_row = conn.execute(
            "SELECT body, current_revision_id, updated_at"
            " FROM issue_documents WHERE issue_id = ? AND key = 'context_summary'",
            (issue_id,),
        ).fetchone()
        context_summary_doc = None
        if context_summary_row:
            try:
                summary_data = json.loads(str(context_summary_row["body"] or "{}"))
            except (ValueError, TypeError):
                summary_data = {}
            synthesized_through = summary_data.get("synthesized_through_comment_id")
            context_summary_doc = {
                "blocks": summary_data.get("blocks", []),
                "synthesized_through": synthesized_through,
                "current_revision_id": context_summary_row["current_revision_id"],
                "updated_at": context_summary_row["updated_at"],
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
            # Prefer the validated report (written by the child's assignee);
            # fall back to last-comment parsing only for pre-migration data.
            agent_report = (
                latest_agent_report(db_path, issue_id=str(r["id"]))
                or _parse_agent_report(last_body)
            )
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

    # When context_summary is active, filter comments to only those after the
    # last synthesized point — older content is captured in the summary blocks.
    if context_summary_doc and context_summary_doc.get("synthesized_through"):
        synth_id = context_summary_doc["synthesized_through"]
        synth_found = False
        filtered: list[dict[str, Any]] = []
        for c in comments:  # already chronological (reversed shown)
            if synth_found:
                filtered.append(c)
            if c["id"] == synth_id:
                synth_found = True
        if synth_found:
            comments = filtered

    with contextlib.closing(_connect(db_path)) as conn:
        user_directives = _user_directives(conn)

    return {
        "issue_id": issue_id,
        "run_id": run_id,
        "user_directives": user_directives,
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
            "acceptance_criteria": acceptance_criteria,
        },
        "parent": parent_summary,
        "comments": comments,
        "comments_shown": len(comments),
        "comments_total": total_comments,
        "fallback_fetch_needed": has_more_comments,
        "pending_interactions": interactions,
        "plan_document": plan_doc,
        "context_summary": context_summary_doc,
        "trigger_comment_id": comment_id,
        "children": children_summary,
    }


_PROJECT_OPEN_ISSUES_LIMIT = 40


def project_open_issues(db_path: Path, *, exclude_issue_id: str | None = None) -> list[dict[str, Any]]:
    """Every non-terminal issue in the project, across ALL roots, newest first.

    The wake payload is scoped to one issue's subtree — correct for workers,
    but the Lead answers project-level questions ("is there open work?") from
    it. With several root issues (each user task can start a new root), a Lead
    woken on a finished root truthfully reported "no open issues" while other
    trees had live, even failing, work. Injected for lead-tier roles so global
    claims come from global data.
    """
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, title, status, role, parent_id, assignee_agent_id, updated_at
            FROM issues
            WHERE status NOT IN ('done', 'cancelled')
            ORDER BY updated_at DESC, rowid DESC
            LIMIT ?
            """,
            (_PROJECT_OPEN_ISSUES_LIMIT,),
        ).fetchall()
    return [
        dict(row) for row in rows
        if str(row["id"]) != str(exclude_issue_id or "")
    ]


_USER_DIRECTIVES_LIMIT = 5
_DIRECTIVE_SUMMARY_MAX = 400

# Resolver ids that are NOT the human: their resolutions are plumbing, not
# product direction.
_MACHINE_RESOLVERS = {"autonomy", "system"}


def _user_directives(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Decisions the human took on this project, newest first (capped).

    A user-resolved interaction with a ``user_note`` (e.g. answering "B" to
    "Decidir cierre del prototipo") — or an outright rejection — is a BINDING
    project directive, not a one-shot message for whoever woke next. Injected
    into every wake payload so the Lead encodes it into new issues' acceptance
    criteria and reviewers review against it instead of against earlier,
    now-superseded standards.
    """
    rows = conn.execute(
        """
        SELECT title, summary, status, result_json, resolved_at, resolved_by_user_id
        FROM issue_thread_interactions
        WHERE resolved_by_user_id IS NOT NULL
          AND status IN ('accepted', 'answered', 'rejected')
        ORDER BY resolved_at DESC, rowid DESC
        LIMIT 40
        """
    ).fetchall()
    directives: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        # Machine resolutions (autonomy/system) are plumbing, not direction.
        if str(item.get("resolved_by_user_id") or "") in _MACHINE_RESOLVERS:
            continue
        try:
            result = json.loads(str(item.get("result_json") or "{}"))
        except (TypeError, ValueError):
            result = {}
        if not isinstance(result, dict):
            result = {}
        resolution = result.get("resolution_data") or {}
        note = str((resolution or {}).get("user_note") or "").strip()
        status = str(item.get("status") or "")
        if not note and status != "rejected":
            continue  # a plain accept without a note carries no direction
        summary = str(item.get("summary") or "").strip()
        directives.append({
            "resolved_at": item.get("resolved_at"),
            "title": str(item.get("title") or "").strip(),
            "decision": status,
            "user_note": note,
            "question_summary": summary[:_DIRECTIVE_SUMMARY_MAX] + ("…" if len(summary) > _DIRECTIVE_SUMMARY_MAX else ""),
        })
        if len(directives) >= _USER_DIRECTIVES_LIMIT:
            break
    return directives


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
