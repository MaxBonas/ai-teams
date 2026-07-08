"""Validated, provenance-carrying AGENT-REPORT records (role enforcement fase 3).

The legacy contract parsed the ``---AGENT-REPORT---`` block out of an issue's
*last comment* at read time. That was fragile and forgeable:

- if the Lead's directive was the newest comment, the child's report vanished;
- any actor's text on the thread could be read as "the report";
- truncation could corrupt it.

This module persists the report ONCE, at run finish, tied to the run and agent
that produced it. Consumers must only trust rows with ``valid=1 AND
is_assignee=1`` — i.e. a well-formed report written by the issue's own
assignee. Everything else is stored for audit but never drives gates.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

REPORT_FIELDS = ("role", "result", "issue_status", "next_owner", "tech_match", "blocker", "evidence")

# Global vocabulary of meaningful outcomes. Per-role nuance (e.g. only
# reviewers say "approved") is intentionally NOT hard-failed: an engineer
# writing "approved" is odd but harmless; gates key off reviewer rows only.
ALLOWED_RESULTS = frozenset(
    {"done", "completed", "approved", "changes_requested", "blocked", "partial", "failed", "skipped"}
)

_ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS agent_reports (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    agent_role TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    issue_status TEXT,
    next_owner TEXT,
    tech_match TEXT,
    blocker TEXT,
    evidence TEXT,
    valid INTEGER NOT NULL DEFAULT 0,
    is_assignee INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_reports_issue ON agent_reports(issue_id, created_at);
"""


def record_agent_report(
    db_path: Path,
    *,
    issue_id: str,
    agent_id: str,
    run_id: str | None,
    agent_role: str,
    parsed: dict[str, str],
) -> dict[str, Any]:
    """Persist a parsed AGENT-REPORT with validation + provenance flags.

    ``valid``      — result belongs to the known vocabulary AND the report's
                     self-declared role (if any) matches the emitting agent's
                     role (an agent cannot speak as another role).
    ``is_assignee``— the emitting agent is the issue's assignee.
    """
    result_value = str(parsed.get("result") or "").strip().lower()
    claimed_role = str(parsed.get("role") or "").strip().lower()
    role_key = str(agent_role or "").strip().lower()
    role_matches = not claimed_role or claimed_role == role_key or (
        # tolerate the common engineer/software_engineer + reviewer/code_reviewer aliases
        claimed_role.replace("software_", "").replace("code_", "")
        == role_key.replace("software_", "").replace("code_", "")
    )
    valid = result_value in ALLOWED_RESULTS and role_matches

    with contextlib.closing(_connect(db_path)) as conn:
        conn.executescript(_ENSURE_SQL)
        assignee_row = conn.execute(
            "SELECT assignee_agent_id FROM issues WHERE id = ?", (issue_id,)
        ).fetchone()
        is_assignee = bool(
            assignee_row and str(assignee_row["assignee_agent_id"] or "") == str(agent_id or "")
        )
        row = conn.execute(
            """
            INSERT INTO agent_reports (
                id, issue_id, agent_id, run_id, agent_role, result, issue_status,
                next_owner, tech_match, blocker, evidence, valid, is_assignee, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                str(uuid.uuid4()),
                issue_id,
                agent_id or None,
                run_id,
                role_key,
                result_value,
                _clean(parsed.get("issue_status")),
                _clean(parsed.get("next_owner")),
                _clean(parsed.get("tech_match")),
                _clean(parsed.get("blocker")),
                _clean(parsed.get("evidence")),
                1 if valid else 0,
                1 if is_assignee else 0,
                json.dumps(parsed, ensure_ascii=False, sort_keys=True),
            ),
        ).fetchone()
        return dict(row)


def latest_agent_report(db_path: Path, *, issue_id: str) -> dict[str, str] | None:
    """Latest trustworthy report for *issue_id* (valid + written by assignee).

    Returns the same dict shape the legacy comment parser produced, so
    consumers can switch transparently. None when no trustworthy report exists.
    """
    try:
        with contextlib.closing(_connect(db_path)) as conn:
            row = conn.execute(
                """
                SELECT agent_role, result, issue_status, next_owner, tech_match, blocker, evidence
                FROM agent_reports
                WHERE issue_id = ?
                  AND valid = 1
                  AND is_assignee = 1
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (issue_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None  # table missing (pre-migration DB) — caller falls back
    if row is None:
        return None
    report = {
        "role": row["agent_role"],
        "result": row["result"],
        "issue_status": row["issue_status"],
        "next_owner": row["next_owner"],
        "tech_match": row["tech_match"],
        "blocker": row["blocker"],
        "evidence": row["evidence"],
    }
    return {k: v for k, v in report.items() if v}


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
