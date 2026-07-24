from __future__ import annotations

import contextlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.utils import PROJECT_ROOT, _require_api_auth_request, _workspace_from_request, get_current_workspace, resolve_runtime_dir
from aiteam.db.activity_log import log_activity
from aiteam.db.dependencies import resolve_blocker_wakeups
from aiteam.db.documents import get_context_summary, get_document
from aiteam.db.interactions import list_interactions
from aiteam.db.issues import create_issue, get_issue, list_issues, update_issue
from aiteam.db.liveness import diagnose_issue
from aiteam.db.quorum_sessions import (
    evaluate_quorum_session,
    get_quorum_session_for_issue,
    list_quorum_contributions,
)
from aiteam.hiring_economics import detect_policy_deviations, provider_router_health
from aiteam.objective_classification import (
    classify_objective,
    classification_from_metadata,
)
from aiteam.project_adapters import ensure_quorum_agents, project_profiles
from aiteam.provider_governor import GOVERNOR
from aiteam.plan_contract import present_plan_document
from aiteam.run_profiles import LEAD_QUORUM, select_execution_profile
from aiteam.subscription_quota import subscription_quota_snapshot
from scripts.orchestrator_evals import evaluate_db

router = APIRouter()


class CreateIssueRequest(BaseModel):
    title: str
    status: str = "backlog"
    goal_id: str | None = None
    parent_id: str | None = None
    description: str | None = None
    role: str | None = None
    complexity: str | None = None
    criticality: Literal["low", "medium", "high", "critical"] | None = None
    ambiguity: Literal["low", "medium", "high"] | None = None
    independent_verification: bool | None = None
    parallel_workstreams: int | None = None
    reversible: bool | None = None
    run_profile: Literal["auto", "solo_lead", "lead_quorum", "full_team"] | None = None
    objective_kind: Literal[
        "auto", "software", "research", "operations", "mixed", "non_code"
    ] | None = None
    priority: int = 0
    assignee_agent_id: str | None = None
    data_class: Literal["public", "internal", "confidential", "restricted"] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateIssueRequest(BaseModel):
    status: str | None = None
    title: str | None = None
    description: str | None = None
    assignee_agent_id: str | None = None
    priority: int | None = None
    complexity: str | None = None
    criticality: str | None = None
    metadata: dict[str, Any] | None = None
    data_class: Literal["public", "internal", "confidential", "restricted"] | None = None
    objective_kind: Literal[
        "auto", "software", "research", "operations", "mixed", "non_code"
    ] | None = None


@router.post("/api/issues")
async def post_issue(body: CreateIssueRequest, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    metadata = dict(body.metadata or {})
    # The durable classification can only be selected through objective_kind;
    # arbitrary metadata must not forge its source/reasons.
    metadata.pop("objective_classification", None)
    metadata_profile = str(metadata.get("profile") or "").strip() or None
    try:
        selection = select_execution_profile(
            explicit_profile=body.run_profile or metadata_profile or "auto",
            criticality=body.criticality,
            ambiguity=body.ambiguity,
            independent_verification=body.independent_verification,
            parallel_workstreams=body.parallel_workstreams,
            reversible=body.reversible,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    metadata["profile"] = selection.profile
    metadata["profile_selection"] = selection.to_metadata()
    persisted_classification = classification_from_metadata(metadata)
    if body.objective_kind or persisted_classification is None:
        classification = classify_objective(
            body.title,
            body.description or "",
            explicit_kind=body.objective_kind,
        )
        metadata["objective_classification"] = classification.to_metadata()
    if body.data_class:
        metadata["data_class"] = body.data_class
    source_plan_revision_id = str(metadata.get("source_plan_revision_id") or "").strip()
    if source_plan_revision_id:
        try:
            accepted_plan = _accepted_plan_reference(db, revision_id=source_plan_revision_id)
        except sqlite3.OperationalError as exc:
            raise _schema_err(exc)
        if accepted_plan is None:
            raise HTTPException(
                status_code=400,
                detail="source_plan_revision_id must reference the final plan of an accepted quorum",
            )
        metadata.update(
            {
                "source_plan_revision_id": accepted_plan["revision_id"],
                "source_plan_issue_id": accepted_plan["issue_id"],
                "source_quorum_session_id": accepted_plan["session_id"],
                "source_plan_status": "accepted",
            }
        )
    try:
        row = create_issue(
            db, title=body.title, status=body.status, goal_id=body.goal_id,
            parent_id=body.parent_id, description=body.description, role=body.role,
            complexity=body.complexity, criticality=body.criticality,
            priority=body.priority, assignee_agent_id=body.assignee_agent_id,
            metadata=metadata,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)

    # If this is a lead_quorum task, ensure quorum agents exist so the Lead
    # can immediately assign sub-issues to them without FK failures.
    if selection.profile == LEAD_QUORUM:
        try:
            workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
            profiles = project_profiles(runtime_dir)
            ensure_quorum_agents(db, profiles=profiles, issue_id=str(row["id"]))
        except Exception:
            pass  # never fail issue creation because of agent bootstrap

    log_activity(
        db,
        action="issue.created",
        target_type="issue",
        target_id=row["id"],
        actor_user_id="user",
        payload={
            "title": row.get("title"),
            "status": row.get("status"),
            "assignee_agent_id": row.get("assignee_agent_id"),
            "profile_selection": selection.to_metadata(),
        },
    )
    return {"success": True, "issue": row, "profile_selection": selection.to_metadata()}


def _accepted_plan_reference(db: Path, *, revision_id: str) -> dict[str, str] | None:
    """Resolve una revisión únicamente si es el Plan B final de un quorum aceptado."""
    with contextlib.closing(sqlite3.connect(str(db), timeout=20.0)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT r.id AS revision_id, r.issue_id, q.id AS session_id
            FROM issue_document_revisions r
            JOIN quorum_sessions q
              ON q.issue_id = r.issue_id
             AND q.final_plan_revision_id = r.id
             AND q.status = 'accepted'
            WHERE r.id = ? AND r.key = 'plan'
            LIMIT 1
            """,
            (revision_id,),
        ).fetchone()
    return dict(row) if row is not None else None


@router.get("/api/issues")
async def get_issues(
    request: Request,
    goal_id: str | None = None,
    parent_id: str | None = None,
    status: str | None = None,
    assignee_agent_id: str | None = None,
    limit: int = 200,
):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        rows = list_issues(db, goal_id=goal_id, parent_id=parent_id,
                           status=status, assignee_agent_id=assignee_agent_id, limit=limit)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "issues": rows}


@router.get("/api/issues/{issue_id}")
async def get_issue_by_id(issue_id: str, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        row = get_issue(db, issue_id=issue_id)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    if row is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    # Include pending interactions inline
    try:
        interactions = list_interactions(db, issue_id=issue_id)
        pending = [i for i in interactions if i.get("status") == "pending"]
    except Exception:
        pending = []
    try:
        plan_document = get_document(db, issue_id=issue_id, key="plan")
    except Exception:
        plan_document = None
    return {
        "success": True,
        "issue": row,
        "pending_interactions": pending,
        "plan_document": present_plan_document(plan_document),
    }


@router.get("/api/issues/{issue_id}/quorum")
async def get_issue_quorum(issue_id: str, request: Request):
    """Proyección read-only del contrato durable de quorum de una issue."""
    _require_api_auth_request(request)
    db = _db(request)
    try:
        session = get_quorum_session_for_issue(db, issue_id=issue_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Quorum session not found")
        contributions = list_quorum_contributions(db, session_id=str(session["id"]))
        gate = evaluate_quorum_session(
            db, session_id=str(session["id"]), persist=False
        )
    except HTTPException:
        raise
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    session_view = {
        key: session.get(key)
        for key in (
            "id",
            "issue_id",
            "status",
            "requested_contributions",
            "min_valid_contributions",
            "skipped_reason",
            "final_plan_revision_id",
        )
    }
    contribution_views = [
        {
            "ordinal": row.get("ordinal"),
            "provider": row.get("provider"),
            "model": row.get("model"),
            "channel": row.get("channel"),
            "result": row.get("result"),
            "valid": bool(row.get("valid")),
        }
        for row in contributions
    ]
    return {
        "success": True,
        "issue_id": issue_id,
        "session": session_view,
        "contributions": contribution_views,
        "gate": gate,
    }


@router.get("/api/issues/{issue_id}/thread")
async def get_issue_thread(
    issue_id: str,
    request: Request,
    view: str = "compact",
    max_recent: int = 15,
    max_full: int = 200,
):
    """Return the thread for an issue in compact or full form.

    **compact** (default):
      - ``summary_blocks``: blocks from the context_summary document (already synthesized)
      - ``recent_comments``: up to *max_recent* comments AFTER ``synthesized_through``
        (or the last *max_recent* if no synthesis exists)
      - ``has_synthesized_history``: True when prior blocks exist

    **full**:
      - ``comments``: all comments chronological (capped at *max_full*)
    """
    _require_api_auth_request(request)
    db = _db(request)
    if view not in ("compact", "full"):
        raise HTTPException(status_code=400, detail="view must be 'compact' or 'full'")
    try:
        with sqlite3.connect(str(db), timeout=20.0) as conn:
            conn.row_factory = sqlite3.Row

            total_comments: int = conn.execute(
                "SELECT COUNT(*) FROM issue_comments WHERE issue_id = ?", (issue_id,)
            ).fetchone()[0]

            if view == "full":
                rows = conn.execute(
                    """
                    SELECT id, body, author_agent_id, author_user_id, source_run_id, created_at
                    FROM issue_comments WHERE issue_id = ?
                    ORDER BY created_at ASC, rowid ASC
                    LIMIT ?
                    """,
                    (issue_id, max_full),
                ).fetchall()
                comments = [dict(r) for r in rows]
                return {
                    "success": True,
                    "view": "full",
                    "issue_id": issue_id,
                    "total_comments": total_comments,
                    "comments": comments,
                    "truncated": total_comments > max_full,
                }

            # compact view
            summary_data = get_context_summary(db, issue_id=issue_id)
            summary_blocks: list[dict] = []
            synthesized_through: str | None = None
            if summary_data:
                summary_blocks = summary_data.get("blocks", [])
                synthesized_through = summary_data.get("synthesized_through_comment_id")

            # Fetch recent comments — only those after synthesized_through
            if synthesized_through:
                synth_row = conn.execute(
                    "SELECT rowid FROM issue_comments WHERE id = ?",
                    (synthesized_through,),
                ).fetchone()
                if synth_row:
                    recent_rows = conn.execute(
                        """
                        SELECT id, body, author_agent_id, author_user_id, source_run_id, created_at
                        FROM issue_comments WHERE issue_id = ? AND rowid > ?
                        ORDER BY created_at ASC, rowid ASC
                        LIMIT ?
                        """,
                        (issue_id, synth_row[0], max_recent),
                    ).fetchall()
                else:
                    recent_rows = conn.execute(
                        """
                        SELECT id, body, author_agent_id, author_user_id, source_run_id, created_at
                        FROM issue_comments WHERE issue_id = ?
                        ORDER BY created_at DESC, rowid DESC LIMIT ?
                        """,
                        (issue_id, max_recent),
                    ).fetchall()
                    recent_rows = list(reversed(recent_rows))
            else:
                recent_rows = conn.execute(
                    """
                    SELECT id, body, author_agent_id, author_user_id, source_run_id, created_at
                    FROM issue_comments WHERE issue_id = ?
                    ORDER BY created_at DESC, rowid DESC LIMIT ?
                    """,
                    (issue_id, max_recent),
                ).fetchall()
                recent_rows = list(reversed(recent_rows))

            recent_comments = [dict(r) for r in recent_rows]

    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)

    return {
        "success": True,
        "view": "compact",
        "issue_id": issue_id,
        "total_comments": total_comments,
        "summary_blocks": summary_blocks,
        "synthesized_through": synthesized_through,
        "recent_comments": recent_comments,
        "has_synthesized_history": len(summary_blocks) > 0,
    }


@router.get("/api/issues/{issue_id}/liveness")
async def get_issue_liveness(issue_id: str, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        diagnosis = diagnose_issue(db, issue_id=issue_id)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "issue_id": issue_id, "diagnosis": diagnosis}


@router.patch("/api/issues/{issue_id}")
async def patch_issue(issue_id: str, body: UpdateIssueRequest, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        current = get_issue(db, issue_id=issue_id)
        current_metadata: dict[str, Any] = {}
        if current:
            try:
                import json
                current_metadata = json.loads(str(current.get("metadata_json") or "{}"))
            except (TypeError, ValueError):
                current_metadata = {}
        incoming_metadata = dict(body.metadata or {})
        incoming_metadata.pop("objective_classification", None)
        metadata = (
            {**current_metadata, **incoming_metadata}
            if body.metadata is not None
            or body.data_class is not None
            or body.objective_kind is not None
            or body.title is not None
            or body.description is not None
            else None
        )
        if metadata is not None and body.data_class is not None:
            metadata["data_class"] = body.data_class
        current_classification = classification_from_metadata(current_metadata)
        should_reclassify = body.objective_kind is not None or (
            current_classification is not None
            and current_classification.source == "deterministic_signals"
            and (body.title is not None or body.description is not None)
        )
        if metadata is not None and should_reclassify:
            classification = classify_objective(
                body.title if body.title is not None else str((current or {}).get("title") or ""),
                (
                    body.description
                    if body.description is not None
                    else str((current or {}).get("description") or "")
                ),
                explicit_kind=body.objective_kind,
            )
            metadata["objective_classification"] = classification.to_metadata()
        row = update_issue(
            db, issue_id=issue_id, status=body.status, title=body.title,
            description=body.description, assignee_agent_id=body.assignee_agent_id,
            priority=body.priority, complexity=body.complexity,
            criticality=body.criticality, metadata=metadata,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    if row is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    log_activity(
        db,
        action="issue.updated",
        target_type="issue",
        target_id=row["id"],
        actor_user_id="user",
        payload={
            "status": body.status,
            "title": body.title,
            "assignee_agent_id": body.assignee_agent_id,
            "priority": body.priority,
            "complexity": body.complexity,
            "criticality": body.criticality,
            "data_class": body.data_class,
            "objective_kind": body.objective_kind,
        },
    )
    # Unblock dependent issues when this one reaches a terminal state
    if body.status in ("done", "cancelled"):
        try:
            resolve_blocker_wakeups(db, resolved_issue_id=issue_id)
        except Exception:
            pass
    return {"success": True, "issue": row}


@router.get("/api/loop-health")
async def get_loop_health(request: Request):
    """Return a summary of detected Lead-Engineer loops and stuck blocked children.

    Used by the UI to surface a warning banner when the system has detected
    issues that are looping without resolution.

    Returns:
        detected_loops: list of {child_issue_id, parent_issue_id, skip_count, loop_detected_at}
        thin_delegations: count of issues where the Lead delegated with a too-short description
        summary: {total_loops, total_unresolved_blocked, requires_attention: bool}
    """
    _require_api_auth_request(request)
    db = _db(request)
    try:
        with contextlib.closing(sqlite3.connect(str(db), timeout=20.0)) as conn:
            conn.row_factory = sqlite3.Row

            # Issues where loop.detected was fired and the child is still blocked
            loop_rows = conn.execute(
                """
                SELECT
                    a.target_id AS child_issue_id,
                    json_extract(a.payload_json, '$.parent_issue_id') AS parent_issue_id,
                    COUNT(CASE WHEN a.action = 'lead.unblock_skipped' THEN 1 END) AS skip_count,
                    MAX(CASE WHEN a.action = 'loop.detected' THEN a.created_at END) AS loop_detected_at,
                    i.status AS child_status,
                    i.title AS child_title
                FROM activity_log a
                JOIN issues i ON i.id = a.target_id
                WHERE a.action IN ('lead.unblock_skipped', 'loop.detected')
                  AND i.status = 'blocked'
                GROUP BY a.target_id
                HAVING MAX(CASE WHEN a.action = 'loop.detected' THEN 1 ELSE 0 END) = 1
                ORDER BY loop_detected_at DESC
                LIMIT 20
                """
            ).fetchall()

            # Thin delegation count (last 24h)
            thin_row = conn.execute(
                """
                SELECT COUNT(DISTINCT target_id) AS n
                FROM activity_log
                WHERE action = 'delegation.thin_description'
                  AND created_at >= datetime('now', '-1 day')
                """
            ).fetchone()
            thin_count = int(thin_row["n"]) if thin_row else 0

            # Blocked children with high skip count but not yet at loop.detected threshold
            at_risk_rows = conn.execute(
                """
                SELECT
                    a.target_id AS child_issue_id,
                    COUNT(*) AS skip_count,
                    i.status AS child_status,
                    i.title AS child_title
                FROM activity_log a
                JOIN issues i ON i.id = a.target_id
                WHERE a.action = 'lead.unblock_skipped'
                  AND i.status = 'blocked'
                GROUP BY a.target_id
                HAVING skip_count >= 2
                   AND MAX(CASE WHEN a.action = 'loop.detected' THEN 1 ELSE 0 END) = 0
                ORDER BY skip_count DESC
                LIMIT 10
                """
            ).fetchall()

        detected_loops = [
            {
                "child_issue_id": row["child_issue_id"],
                "parent_issue_id": row["parent_issue_id"],
                "child_title": row["child_title"],
                "skip_count": int(row["skip_count"] or 0),
                "loop_detected_at": row["loop_detected_at"],
            }
            for row in loop_rows
        ]
        at_risk = [
            {
                "child_issue_id": row["child_issue_id"],
                "child_title": row["child_title"],
                "skip_count": int(row["skip_count"] or 0),
            }
            for row in at_risk_rows
        ]

        providers = GOVERNOR.snapshot()
        providers_degraded = sorted(key for key, info in providers.items() if info.get("degraded"))
        try:
            policy_deviations = detect_policy_deviations(db)
        except Exception:
            policy_deviations = []
        try:
            router_health = provider_router_health(db)
        except Exception:
            router_health = []
        try:
            router_health_24h = provider_router_health(db, window_hours=24)
        except Exception:
            router_health_24h = []
        # La atención se decide con la ventana RECIENTE: un proveedor que falló
        # hace una semana y ya se arregló no debe seguir gritando en el panel.
        providers_unhealthy = sorted(
            r["provider"] for r in (router_health_24h or router_health) if r["unhealthy"]
        )

        try:
            from aiteam.db.interactions import decision_latency_stats
            decision_latency = decision_latency_stats(db)
        except Exception:
            decision_latency = {}

        # Reutiliza exactamente las mismas métricas que el harness offline para
        # que el panel operativo y los benchmarks no diverjan en sus definiciones.
        # Es aditivo: consumidores antiguos de loop-health conservan su contrato.
        try:
            _eval = evaluate_db(db)
            orchestrator_evals = {
                "economy": _eval["economy"],
                "context": _eval["context"],
                "quorum": _eval["quorum"],
                "liveness": _eval["liveness"],
            }
            eval_requires_attention = (
                not bool(_eval["liveness"].get("healthy", False))
                or (
                    bool(_eval["quorum"].get("available", False))
                    and not bool(_eval["quorum"].get("healthy", False))
                )
            )
        except (FileNotFoundError, ValueError, sqlite3.Error):
            orchestrator_evals = {"available": False}
            eval_requires_attention = False

        # Estado del cap de coste diario (real, por-token) para el dashboard.
        cost_cap: dict[str, Any] = {"enabled": False}
        try:
            from aiteam.policies import daily_cost_cap_cents
            _cap = daily_cost_cap_cents()
            if _cap > 0:
                _day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                with contextlib.closing(sqlite3.connect(str(db), timeout=20.0)) as conn:
                    _spent = int(
                        conn.execute(
                            "SELECT COALESCE(SUM(cost_cents), 0) FROM cost_events WHERE substr(created_at,1,10) = ?",
                            (_day,),
                        ).fetchone()[0]
                        or 0
                    )
                cost_cap = {
                    "enabled": True,
                    "day": _day,
                    "cap_cents": _cap,
                    "spent_cents": _spent,
                    "reached": _spent >= _cap,
                }
        except Exception:
            cost_cap = {"enabled": False}
        try:
            subscription_quota = subscription_quota_snapshot(
                db,
                profiles=project_profiles(db.parent),
            )
        except Exception:
            subscription_quota = []
        subscription_profiles_requiring_attention = [
            row["profile_id"] for row in subscription_quota if row.get("requires_attention")
        ]
        requires_attention = (
            bool(detected_loops)
            or any(r["skip_count"] >= 2 for r in at_risk)
            or bool(providers_degraded)
            or bool(providers_unhealthy)
            or bool(cost_cap.get("reached"))
            or bool(subscription_profiles_requiring_attention)
            or eval_requires_attention
        )
        return {
            "success": True,
            "detected_loops": detected_loops,
            "at_risk": at_risk,
            "thin_delegations_last_24h": thin_count,
            "providers": providers,
            "providers_degraded": providers_degraded,
            "router_health": router_health,
            "router_health_24h": router_health_24h,
            "providers_unhealthy": providers_unhealthy,
            "decision_latency": decision_latency,
            "orchestrator_evals": orchestrator_evals,
            "cost_cap": cost_cap,
            "capacity_profiles": subscription_quota,
            "capacity_profiles_requiring_attention": subscription_profiles_requiring_attention,
            # Compatibilidad transitoria con clientes anteriores al split
            # subscription_pressure/api_rate_limit.
            "subscription_quota": subscription_quota,
            "subscription_profiles_requiring_attention": subscription_profiles_requiring_attention,
            "policy_deviations": policy_deviations,
            "summary": {
                "total_loops": len(detected_loops),
                "total_at_risk": len(at_risk),
                "requires_attention": requires_attention,
            },
        }
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)


def _db(request: Request) -> Path:
    ws = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return resolve_runtime_dir(ws, PROJECT_ROOT) / "aiteam.db"

def _schema_err(exc: sqlite3.OperationalError) -> HTTPException:
    if "no such table" in str(exc).lower():
        return HTTPException(status_code=503, detail="Schema not available")
    return HTTPException(status_code=500, detail=str(exc))
