from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.db.activity_log import log_activity
from aiteam.db.agents import create_agent
from aiteam.db.interactions import ConflictError, create_interaction, resolve_interaction
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.project_adapters import choose_adapter_for_role, project_profiles, reconcile_project_agent_policy
from aiteam.tools.catalog import default_capabilities_for_role

_TERMINAL = {"done", "cancelled"}


def diagnose_issue(db_path: Path, *, issue_id: str) -> dict[str, Any]:
    """Return a liveness diagnosis for *issue_id*.

    Paperclip-style: productive work continues unless there is a real blocker.
    The diagnosis tells you exactly which invariant is violated, or confirms work
    is live.

    Returns a dict with:
      - ``live``: bool — True if there is an active path forward
      - ``reason``: str — human-readable explanation
      - ``paths``: list[str] — which live paths were found (active_run,
        queued_wakeup, pending_interaction, children_live, terminal)
      - ``blockers``: list[str] — which expected paths are missing
    """
    with contextlib.closing(_connect(db_path)) as conn:
        issue_row = conn.execute(
            "SELECT id, status, assignee_agent_id, parent_id FROM issues WHERE id = ?",
            (issue_id,),
        ).fetchone()
        if issue_row is None:
            return {"live": False, "reason": "issue not found", "paths": [], "blockers": ["not_found"]}

        status = str(issue_row["status"] or "")
        if status in {"done", "cancelled"}:
            return {"live": True, "reason": f"terminal status: {status}", "paths": ["terminal"], "blockers": []}

        paths: list[str] = []
        blockers: list[str] = []

        # Active run
        active_run = conn.execute(
            "SELECT id FROM runs WHERE issue_id = ? AND status IN ('queued', 'running') LIMIT 1",
            (issue_id,),
        ).fetchone()
        if active_run:
            paths.append("active_run")

        # Queued wakeup
        live_wakeup = conn.execute(
            """
            SELECT id FROM wakeup_requests
            WHERE payload_json LIKE ?
              AND status IN ('queued', 'claimed', 'running')
            LIMIT 1
            """,
            (f'%"issue_id": "{issue_id}"%',),
        ).fetchone()
        if live_wakeup:
            paths.append("queued_wakeup")

        # Pending interaction (human gate — legitimate pause)
        pending_interaction = conn.execute(
            "SELECT id, kind FROM issue_thread_interactions WHERE issue_id = ? AND status = 'pending' LIMIT 1",
            (issue_id,),
        ).fetchone()
        if pending_interaction:
            paths.append("pending_interaction")

        # Live children (parent delegates and waits)
        child_statuses = [
            str(r["status"])
            for r in conn.execute(
                "SELECT status FROM issues WHERE parent_id = ?", (issue_id,)
            ).fetchall()
        ]
        if child_statuses and any(s not in _TERMINAL for s in child_statuses):
            paths.append("children_live")

        # Explicit blocker chain — waiting on unresolved deps is a valid waiting path
        unresolved_blockers = conn.execute(
            """
            SELECT d.depends_on_issue_id, i.status AS blocker_status
            FROM issue_dependencies d
            JOIN issues i ON i.id = d.depends_on_issue_id
            WHERE d.issue_id = ? AND i.status NOT IN ('done', 'cancelled')
            """,
            (issue_id,),
        ).fetchall()
        if unresolved_blockers:
            paths.append("blocker_chain")

        assignee = str(issue_row["assignee_agent_id"] or "")

    if not paths:
        if not assignee:
            blockers.append("no_assignee")
        else:
            blockers.append("no_live_path")
        return {
            "live": False,
            "reason": "no active run, wakeup, pending interaction, or live children",
            "paths": [],
            "blockers": blockers,
        }

    return {
        "live": True,
        "reason": f"live via: {', '.join(paths)}",
        "paths": paths,
        "blockers": [],
    }


def reconcile_unqueued_assigned_issues(db_path: Path) -> list[str]:
    """Enqueue assignment wakeups for assigned work with no live path.

    Paperclip's useful invariant is simple: non-terminal assigned work should
    have one of owner wakeup, active run, explicit blocker, or terminal status.
    This first reconciler covers the common startup gap.

    ``in_review`` is included because EXECUTION_SEMANTICS.md requires it: an
    ``in_review`` issue with no pending interaction, no active run, and no
    queued wakeup is a silent stall that must be surfaced.
    """

    live_issue_ids = _live_issue_ids(db_path)
    enqueued: list[str] = []
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, assignee_agent_id, complexity, description, title
            FROM issues
            WHERE status IN ('todo', 'backlog', 'in_progress', 'in_review')
              AND assignee_agent_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM issues child WHERE child.parent_id = issues.id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM issue_dependencies d
                  JOIN issues dep ON dep.id = d.depends_on_issue_id
                  WHERE d.issue_id = issues.id
                    AND dep.status NOT IN ('done', 'cancelled')
              )
              -- Infra backoff: if the most recent run for this issue failed at
              -- the provider transport layer (api_error: 429 / timeout / 5xx),
              -- hold the requeue for 2 minutes instead of hammering every tick.
              AND NOT EXISTS (
                  SELECT 1
                  FROM runs r
                  WHERE r.issue_id = issues.id
                    AND r.status = 'failed'
                    AND r.error_code = 'api_error'
                    AND r.finished_at >= datetime('now', '-120 seconds')
                    AND r.created_at = (
                        SELECT MAX(r2.created_at) FROM runs r2 WHERE r2.issue_id = issues.id
                    )
              )
            ORDER BY priority DESC, created_at ASC, id ASC
            """
        ).fetchall()

    for row in rows:
        issue_id = row["id"]
        agent_id = row["assignee_agent_id"]
        if issue_id in live_issue_ids:
            continue
        enqueue_wakeup(
            db_path,
            agent_id=agent_id,
            source="reconcile",
            reason="assignment",
            trigger_detail="assigned_issue_without_live_wakeup",
            payload={
                "issue_id": issue_id,
                "wake_reason": "assignment",
                "delegation_reason": row["description"] or row["title"],
                "complexity": row["complexity"],
            },
            idempotency_key=f"assignment:{issue_id}:{agent_id}",
        )
        enqueued.append(issue_id)
    return enqueued


def reconcile_unassigned_role_issues(db_path: Path) -> list[str]:
    """Materialize role agents and wake leaf issues created without assignees.

    LLM adapters can create useful sub-issues without going through the
    built-in hiring proposal flow. Paperclip's worker/plugin paths keep those
    issues live by assigning an agent and requesting a wakeup immediately; this
    reconciler repairs the same invariant after startup or older runs.
    """

    live_issue_ids = _live_issue_ids(db_path)
    enqueued: list[str] = []
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, role, complexity, description, title, parent_id
            FROM issues
            WHERE status IN ('todo', 'backlog', 'in_progress', 'in_review')
              AND assignee_agent_id IS NULL
              AND role IS NOT NULL
              AND TRIM(role) <> ''
              AND NOT EXISTS (
                  SELECT 1 FROM issues child WHERE child.parent_id = issues.id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM issue_dependencies d
                  JOIN issues dep ON dep.id = d.depends_on_issue_id
                  WHERE d.issue_id = issues.id
                    AND dep.status NOT IN ('done', 'cancelled')
              )
            ORDER BY priority DESC, created_at ASC, id ASC
            """
        ).fetchall()
        lead_agent_id = _lead_agent_id(conn)

    for row in rows:
        issue_id = str(row["id"])
        if issue_id in live_issue_ids:
            continue
        agent_id = _ensure_role_agent(
            db_path,
            role=str(row["role"] or ""),
            supervisor_agent_id=lead_agent_id,
            source=f"liveness:{issue_id}",
        )
        if not agent_id:
            continue
        with contextlib.closing(_connect(db_path)) as conn:
            updated = conn.execute(
                """
                UPDATE issues
                SET assignee_agent_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND assignee_agent_id IS NULL
                RETURNING id
                """,
                (agent_id, issue_id),
            ).fetchone()
        if updated is None:
            continue
        enqueue_wakeup(
            db_path,
            agent_id=agent_id,
            source="reconcile",
            reason="assignment",
            trigger_detail="unassigned_role_issue_materialized",
            payload={
                "issue_id": issue_id,
                "parent_issue_id": row["parent_id"],
                "wake_reason": "assignment",
                "delegation_reason": row["description"] or row["title"],
                "complexity": row["complexity"],
            },
            idempotency_key=f"assignment:{issue_id}:{agent_id}",
        )
        log_activity(
            db_path,
            action="issue.assigned",
            target_type="issue",
            target_id=issue_id,
            actor_agent_id=lead_agent_id,
            payload={
                "agent_id": agent_id,
                "source": "liveness_reconcile_unassigned_role_issue",
            },
        )
        enqueued.append(issue_id)
    return enqueued


def reconcile_stalled_subtrees(db_path: Path) -> list[str]:
    """Enqueue escalation wakeups for supervisors whose subtrees are fully stalled.

    A subtree is "stalled" when a parent issue is in_progress and ALL of its
    non-terminal children are in 'blocked' status.  Without this reconciler the
    system enters a silent dead-lock:

      - Engineer blocked (api_only_engineer_no_workspace_changes)
      - Reviewer/QA waiting on the blocked engineer (dependency blocker skips them)
      - Lead in_progress but never receives a child_report wakeup → infinite stall

    This mirrors Paperclip's ``harness_liveness_escalation`` which detects stalled
    subtrees and creates escalation issues automatically.

    Idempotency: one ``subtree_stalled`` wakeup per supervisor per stall cycle
    (keyed on the set of blocked child ids so new children re-trigger it).
    """
    enqueued: list[str] = []
    live_issue_ids = _live_issue_ids(db_path)

    with contextlib.closing(_connect(db_path)) as conn:
        # Find parent issues in_progress/in_review that have at least one child
        # and ALL non-terminal children are blocked
        parent_rows = conn.execute(
            """
            SELECT DISTINCT i.id, i.assignee_agent_id, i.parent_id
            FROM issues i
            WHERE i.status IN ('in_progress', 'in_review')
              AND EXISTS (
                  SELECT 1 FROM issues c WHERE c.parent_id = i.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM issues c
                  WHERE c.parent_id = i.id
                    AND c.status NOT IN ('done', 'cancelled', 'blocked')
              )
              AND EXISTS (
                  SELECT 1 FROM issues c
                  WHERE c.parent_id = i.id
                    AND c.status = 'blocked'
              )
            ORDER BY i.created_at ASC
            """
        ).fetchall()

        lead_agent_id = _lead_agent_id(conn)

    for parent_row in parent_rows:
        parent_id = str(parent_row["id"])
        supervisor_id = str(parent_row["assignee_agent_id"] or lead_agent_id or "")
        if not supervisor_id:
            continue

        # Collect blocked child ids for idempotency key
        with contextlib.closing(_connect(db_path)) as conn:
            blocked_rows = conn.execute(
                "SELECT id FROM issues WHERE parent_id = ? AND status = 'blocked' ORDER BY id ASC",
                (parent_id,),
            ).fetchall()
        blocked_ids = sorted(str(r["id"]) for r in blocked_rows)
        if not blocked_ids:
            continue

        idempotency_key = f"subtree_stalled:{parent_id}:{','.join(blocked_ids)}"

        # Don't re-escalate if supervisor already has a live wakeup for this issue
        if parent_id in live_issue_ids:
            continue

        enqueue_wakeup(
            db_path,
            agent_id=supervisor_id,
            source="reconcile",
            reason="subtree_stalled",
            trigger_detail=f"all_children_blocked:{parent_id}",
            payload={
                "issue_id": parent_id,
                "wake_reason": "child_report",
                "blocked_child_ids": blocked_ids,
                "escalation_reason": "subtree_stalled",
            },
            idempotency_key=idempotency_key,
        )
        # Also create a durable escalation interaction so the stall persists even
        # if the supervisor run fails.  The interaction is idempotent (same key)
        # and will wake the assignee when the user accepts/rejects it.
        create_interaction(
            db_path,
            issue_id=parent_id,
            kind="request_confirmation",
            payload={
                "blocked_child_ids": blocked_ids,
                "escalation_reason": "subtree_stalled",
                "supervisor_id": supervisor_id,
            },
            continuation_policy="wake_assignee",
            idempotency_key=idempotency_key,
            title="Subtree stalled — all child issues are blocked",
            summary=(
                f"{len(blocked_ids)} child issue(s) are blocked and no work can proceed. "
                "Review the blocked children and unblock or reassign."
            ),
        )
        enqueued.append(parent_id)

    return enqueued


def reconcile_orphaned_interactions(db_path: Path) -> list[str]:
    """Cancel pending interactions whose question no longer applies.

    Two orphan cases seen in real runs (a stale "Subtree stalled" card asked
    the user to decide about a child that had been *cancelled* a day earlier):

    1. The interaction's own issue reached a terminal status — nobody is
       waiting for the answer any more.
    2. A ``subtree_stalled`` escalation whose ``blocked_child_ids`` are all
       out of 'blocked' by now (unblocked, done or cancelled) — the stall
       resolved itself.

    Cancelling (instead of accepting) never enqueues a wakeup, so this is
    pure hygiene: the card disappears from the pending list and the audit
    trail records why.
    """
    cancelled: list[str] = []
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT it.id, it.issue_id, it.kind, it.payload_json, i.status AS issue_status
            FROM issue_thread_interactions it
            LEFT JOIN issues i ON i.id = it.issue_id
            WHERE it.status = 'pending'
            ORDER BY it.created_at ASC
            """
        ).fetchall()
        blocked_now = {
            str(r["id"])
            for r in conn.execute("SELECT id FROM issues WHERE status = 'blocked'").fetchall()
        }

    for row in rows:
        issue_status = str(row["issue_status"] or "")
        payload = _decode_json(row["payload_json"])
        reason = str(payload.get("reason") or payload.get("escalation_reason") or "")
        orphan_why = ""
        if issue_status in _TERMINAL:
            orphan_why = f"issue_{issue_status}"
        elif reason == "subtree_stalled":
            child_ids = [str(c) for c in (payload.get("blocked_child_ids") or [])]
            if child_ids and not any(c in blocked_now for c in child_ids):
                orphan_why = "children_no_longer_blocked"
        if not orphan_why:
            continue
        try:
            resolve_interaction(
                db_path,
                interaction_id=str(row["id"]),
                action="cancel",
                resolution_data={"auto_cancelled": True, "orphan_reason": orphan_why},
                resolved_by_user_id="system",
            )
        except (ConflictError, LookupError, ValueError):
            continue
        log_activity(
            db_path,
            action="interaction.auto_cancelled",
            target_type="interaction",
            target_id=str(row["id"]),
            actor_user_id="system",
            payload={"issue_id": str(row["issue_id"]), "reason": reason, "orphan_reason": orphan_why},
        )
        cancelled.append(str(row["id"]))
    return cancelled


def _live_issue_ids(db_path: Path) -> set[str]:
    """Return the set of issue IDs that are currently "live".

    An issue is live when it has at least one of:
    - a queued/claimed/running wakeup pointing to it
    - an active (queued/running) run
    - a pending interaction awaiting user input

    The third condition prevents the reconciler from re-enqueuing issues that
    are correctly waiting for a user decision (e.g. a pending team proposal).
    Without it, the Lead would be woken every 30 s just to skip with
    ``no_pending_lead_work`` — the classic idle loop.
    """
    live: set[str] = set()
    with contextlib.closing(_connect(db_path)) as conn:
        wakeups = conn.execute(
            """
            SELECT payload_json
            FROM wakeup_requests
            WHERE status IN ('queued', 'claimed', 'running')
            """
        ).fetchall()
        runs = conn.execute(
            "SELECT issue_id FROM runs WHERE status IN ('queued', 'running') AND issue_id IS NOT NULL"
        ).fetchall()
        # Issues blocked on a pending user interaction are also live — they are
        # waiting for a decision, not stranded without a live path.
        pending_interactions = conn.execute(
            "SELECT issue_id FROM issue_thread_interactions WHERE status = 'pending' AND issue_id IS NOT NULL"
        ).fetchall()

    for row in wakeups:
        payload = _decode_json(row["payload_json"])
        issue_id = str(payload.get("issue_id") or payload.get("task_id") or "").strip()
        if issue_id:
            live.add(issue_id)
    for row in runs:
        live.add(row["issue_id"])
    for row in pending_interactions:
        live.add(str(row["issue_id"]))
    return live


def _ensure_role_agent(
    db_path: Path,
    *,
    role: str,
    supervisor_agent_id: str | None,
    source: str,
) -> str | None:
    role_key = _normalize_role(role)
    if not role_key:
        return None
    if role_key in {"lead", "team_lead"}:
        return supervisor_agent_id or "role:lead"
    agent_id = f"role:{role_key}"
    with contextlib.closing(_connect(db_path)) as conn:
        if conn.execute("SELECT 1 FROM agents WHERE id = ?", (agent_id,)).fetchone():
            try:
                reconcile_project_agent_policy(db_path)
            except Exception:
                pass
            return agent_id
    try:
        selection = choose_adapter_for_role(role_key, "standard", project_profiles(Path(db_path).parent))
        row = create_agent(
            db_path,
            agent_id=agent_id,
            role=role_key,
            name=role_key.replace("_", " ").title(),
            seniority="standard",
            adapter_type=str((selection or {}).get("adapter_type") or "role_builtin"),
            adapter_config=(selection or {}).get("adapter_config") or {},
            capabilities=default_capabilities_for_role(role_key),
            supervisor_agent_id=supervisor_agent_id,
            metadata={"source": "liveness_reconcile", "trigger": source},
        )
    except sqlite3.IntegrityError:
        return None
    log_activity(
        db_path,
        action="agent.created",
        target_type="agent",
        target_id=row["id"],
        actor_agent_id=supervisor_agent_id,
        payload={"role": role_key, "source": "liveness_reconcile"},
    )
    return str(row["id"])


def _lead_agent_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """
        SELECT id
        FROM agents
        WHERE id = 'role:lead' OR role IN ('lead', 'team_lead')
        ORDER BY CASE WHEN id = 'role:lead' THEN 0 ELSE 1 END, created_at ASC
        LIMIT 1
        """
    ).fetchone()
    return str(row["id"]) if row else None


def _normalize_role(role: str) -> str:
    return role.strip().lower().replace(" ", "_").replace("-", "_")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _decode_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}
