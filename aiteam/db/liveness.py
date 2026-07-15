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
from aiteam.policies import EXTENSION_PROPOSAL_REASON
from aiteam.project_adapters import choose_adapter_for_role, project_profiles, reconcile_project_agent_policy
from aiteam.tools.catalog import default_capabilities_for_role

_TERMINAL = {"done", "cancelled"}

# Reasons whose interaction is deliberately attached to an issue_id that may
# be (or become) terminal — a terminal issue_status there is NOT staleness,
# so the generic "issue closed → cancel as orphan" rule below must skip them.
_TERMINAL_ISSUE_EXEMPT_REASONS = frozenset({
    "parent_closed_child_open",  # attached to the closed parent on purpose
    EXTENSION_PROPOSAL_REASON,   # a capability request outlives the issue that prompted it
})


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
              -- the infrastructure layer — provider transport (api_error: 429 /
              -- timeout / 5xx) or a missing CLI binary (subscription_cli_not_found,
              -- observed hammering 89 runs at ~1/tick until the adapter config
              -- was repaired) — hold the requeue for 2 minutes instead of
              -- retrying every tick against the same broken environment.
              AND NOT EXISTS (
                  SELECT 1
                  FROM runs r
                  WHERE r.issue_id = issues.id
                    AND r.status = 'failed'
                    AND r.error_code IN ('api_error', 'subscription_cli_not_found')
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
    (keyed on the set of blocked child ids so new children re-trigger it). The
    gate is "is an escalation for THIS exact stall already pending" — not
    "does the supervisor have any wakeup at all". A supervisor can receive
    child_report wakeups indefinitely without ever producing an
    ``lead.unblock_attempted`` for the specific blocked child (observed live:
    a root issue got a burst of wakeups, then 24h of total silence with zero
    unblock attempts) — treating "has a wakeup" as "is handling it" let that
    stall go unescalated forever.
    """
    enqueued: list[str] = []

    # A child counts toward the stall if it is EXPLICITLY blocked, or if it is
    # non-terminal and depends on something that's explicitly blocked (a
    # 'todo' reviewer waiting on 31 siblings — one of them permanently
    # blocked — is exactly as stuck as a directly-blocked child; without this
    # it never resolves because its blocker never reaches done/cancelled).
    _CHILD_STUCK_SQL = """
        c.status = 'blocked'
        OR EXISTS (
            SELECT 1 FROM issue_dependencies d
            JOIN issues dep ON dep.id = d.depends_on_issue_id
            WHERE d.issue_id = c.id AND dep.status = 'blocked'
        )
    """

    with contextlib.closing(_connect(db_path)) as conn:
        # Find parent issues in_progress/in_review that have at least one child
        # and ALL non-terminal children are stuck (blocked, directly or via a
        # dependency on something blocked).
        parent_rows = conn.execute(
            f"""
            SELECT DISTINCT i.id, i.assignee_agent_id, i.parent_id
            FROM issues i
            WHERE i.status IN ('in_progress', 'in_review')
              AND EXISTS (
                  SELECT 1 FROM issues c WHERE c.parent_id = i.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM issues c
                  WHERE c.parent_id = i.id
                    AND c.status NOT IN ('done', 'cancelled')
                    AND NOT ({_CHILD_STUCK_SQL})
              )
              AND EXISTS (
                  SELECT 1 FROM issues c
                  WHERE c.parent_id = i.id
                    AND c.status NOT IN ('done', 'cancelled')
                    AND ({_CHILD_STUCK_SQL})
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

        # Collect stuck child ids for idempotency key — same definition as above.
        with contextlib.closing(_connect(db_path)) as conn:
            blocked_rows = conn.execute(
                f"""
                SELECT c.id FROM issues c
                WHERE c.parent_id = ?
                  AND c.status NOT IN ('done', 'cancelled')
                  AND ({_CHILD_STUCK_SQL})
                ORDER BY c.id ASC
                """,
                (parent_id,),
            ).fetchall()
        blocked_ids = sorted(str(r["id"]) for r in blocked_rows)
        if not blocked_ids:
            continue

        idempotency_key = f"subtree_stalled:{parent_id}:{','.join(blocked_ids)}"

        # Two, and only two, reasons to hold off:
        #   (a) THIS exact stall (same parent + same blocked-id set) was
        #       already raised — pending or resolved, since blocked_ids only
        #       changes when something actually moved.
        #   (b) the parent already has a DIFFERENT pending interaction — the
        #       user already has a card for this issue; piling on a second
        #       one is redundant noise, not a missed escalation.
        # Do NOT skip just because the supervisor has some unrelated
        # wakeup/run in flight — that gate let a real stall (child_report
        # wakeups firing with zero unblock_attempted) go unescalated 24h+.
        with contextlib.closing(_connect(db_path)) as conn:
            already_raised = conn.execute(
                """
                SELECT 1 FROM issue_thread_interactions
                WHERE issue_id = ? AND idempotency_key = ?
                LIMIT 1
                """,
                (parent_id, idempotency_key),
            ).fetchone()
            other_pending = conn.execute(
                "SELECT 1 FROM issue_thread_interactions WHERE issue_id = ? AND status = 'pending' LIMIT 1",
                (parent_id,),
            ).fetchone()
        if already_raised is not None or other_pending is not None:
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


def reconcile_idle_parents(db_path: Path) -> list[str]:
    """Despierta a padres cuyo trabajo terminó pero a los que nadie avisó.

    Patrón visto en vivo (CLI Tareas, 2026-07-15): todos los hijos terminales,
    padre in_progress con asignado, y CERO wakeups — el último hijo cerró sin
    notify_supervisor y `reconcile_unqueued_assigned_issues` excluye a
    propósito las issues con hijos, así que ningún mecanismo volvería a
    despertar al Lead jamás. Red de seguridad determinista:

    - todos los hijos en done/cancelled
    - sin wakeup vivo del asignado para esa issue, sin run activa
    - sin interacción pendiente (una decisión humana en curso ES el motivo
      legítimo para esperar)
    - margen de 60s desde el último cambio, para no pisar el auto-report
      normal del flujo.
    """
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.assignee_agent_id, p.updated_at
            FROM issues p
            WHERE p.status IN ('todo', 'in_progress', 'in_review', 'blocked')
              AND p.assignee_agent_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM issues c WHERE c.parent_id = p.id)
              AND NOT EXISTS (
                  SELECT 1 FROM issues c
                  WHERE c.parent_id = p.id AND c.status NOT IN ('done', 'cancelled')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM wakeup_requests w
                  WHERE w.agent_id = p.assignee_agent_id
                    AND w.status IN ('queued', 'claimed', 'running')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM runs r
                  WHERE r.issue_id = p.id AND r.status = 'running'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM issue_thread_interactions x
                  WHERE x.issue_id = p.id AND x.status = 'pending'
              )
              AND p.updated_at < datetime('now', '-60 seconds')
            ORDER BY p.updated_at ASC
            """
        ).fetchall()

    woken: list[str] = []
    for row in rows:
        issue_id = str(row["id"])
        enqueue_wakeup(
            db_path,
            agent_id=str(row["assignee_agent_id"]),
            source="reconciler",
            reason="children_terminal",
            trigger_detail="idle_parent_all_children_terminal",
            payload={"issue_id": issue_id, "wake_reason": "children_terminal"},
            idempotency_key=f"idle_parent:{issue_id}:{row['updated_at']}",
        )
        woken.append(issue_id)
    return woken


def reconcile_orphaned_children_of_closed_parents(db_path: Path) -> list[str]:
    """Escalate open children left behind when their parent already closed.

    Live gap: reconcile_stalled_subtrees only watches parents that are still
    in_progress/in_review. Once a supervisor issue itself reaches done/
    cancelled — while a child is still open (a reviewer left 'blocked' with a
    genuine unresolved finding, say) — nothing escalates it any more. The
    Lead keeps answering "no blocked children" truthfully about its OWN
    issue (which really is empty) and never learns the orphaned child under
    an already-closed parent exists at all.

    Escalates once per (parent, open-child-set) to the parent's supervisor
    (falls back to the Lead) via the SAME durable-interaction pattern as
    reconcile_stalled_subtrees, attached to the parent's issue_id so
    resolving it wakes the right agent.
    """
    enqueued: list[str] = []

    with contextlib.closing(_connect(db_path)) as conn:
        parent_rows = conn.execute(
            """
            SELECT DISTINCT p.id, p.assignee_agent_id, p.status AS parent_status, p.title AS parent_title
            FROM issues p
            WHERE p.status IN ('done', 'cancelled')
              AND EXISTS (
                  SELECT 1 FROM issues c WHERE c.parent_id = p.id AND c.status NOT IN ('done', 'cancelled')
              )
            ORDER BY p.updated_at ASC
            """
        ).fetchall()
        lead_agent_id = _lead_agent_id(conn)

    for parent_row in parent_rows:
        parent_id = str(parent_row["id"])
        parent_status = str(parent_row["parent_status"])
        supervisor_id = str(parent_row["assignee_agent_id"] or lead_agent_id or "")
        if not supervisor_id:
            continue

        with contextlib.closing(_connect(db_path)) as conn:
            open_rows = conn.execute(
                "SELECT id FROM issues WHERE parent_id = ? AND status NOT IN ('done', 'cancelled') ORDER BY id ASC",
                (parent_id,),
            ).fetchall()
        open_ids = sorted(str(r["id"]) for r in open_rows)
        if not open_ids:
            continue

        idempotency_key = f"parent_closed_child_open:{parent_id}:{parent_status}:{','.join(open_ids)}"

        with contextlib.closing(_connect(db_path)) as conn:
            already_raised = conn.execute(
                "SELECT 1 FROM issue_thread_interactions WHERE issue_id = ? AND idempotency_key = ? LIMIT 1",
                (parent_id, idempotency_key),
            ).fetchone()
            other_pending = conn.execute(
                "SELECT 1 FROM issue_thread_interactions WHERE issue_id = ? AND status = 'pending' LIMIT 1",
                (parent_id,),
            ).fetchone()
        if already_raised is not None or other_pending is not None:
            continue

        enqueue_wakeup(
            db_path,
            agent_id=supervisor_id,
            source="reconcile",
            reason="parent_closed_child_open",
            trigger_detail=f"parent_{parent_status}_child_open:{parent_id}",
            payload={
                "issue_id": parent_id,
                "wake_reason": "child_report",
                "open_child_ids": open_ids,
                "escalation_reason": "parent_closed_child_open",
            },
            idempotency_key=idempotency_key,
        )
        create_interaction(
            db_path,
            issue_id=parent_id,
            kind="request_confirmation",
            payload={
                "open_child_ids": open_ids,
                "escalation_reason": "parent_closed_child_open",
                "supervisor_id": supervisor_id,
            },
            continuation_policy="wake_assignee",
            idempotency_key=idempotency_key,
            title=f"Padre {parent_status} con {len(open_ids)} hijo(s) abierto(s)",
            summary=(
                f"«{parent_row['parent_title']}» ya está {parent_status}, pero "
                f"{len(open_ids)} issue(s) hija(s) siguen abiertas. Ciérralas (con la evidencia "
                "disponible) o reabre el padre para seguir trabajando en ellas."
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
        open_now = {
            str(r["id"])
            for r in conn.execute("SELECT id FROM issues WHERE status NOT IN ('done', 'cancelled')").fetchall()
        }

    for row in rows:
        issue_status = str(row["issue_status"] or "")
        payload = _decode_json(row["payload_json"])
        reason = str(payload.get("reason") or payload.get("escalation_reason") or "")
        orphan_why = ""
        if issue_status in _TERMINAL and reason not in _TERMINAL_ISSUE_EXEMPT_REASONS:
            orphan_why = f"issue_{issue_status}"
        elif reason == "subtree_stalled":
            child_ids = [str(c) for c in (payload.get("blocked_child_ids") or [])]
            if child_ids and not any(c in blocked_now for c in child_ids):
                orphan_why = "children_no_longer_blocked"
        elif reason == "parent_closed_child_open":
            child_ids = [str(c) for c in (payload.get("open_child_ids") or [])]
            if child_ids and not any(c in open_now for c in child_ids):
                orphan_why = "children_resolved"
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
