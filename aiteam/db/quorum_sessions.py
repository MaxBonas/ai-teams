"""Persistencia y gates deterministas del quorum de planificación v2."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from aiteam.policies import (
    QUORUM_ABSOLUTE_MIN_VALID_CONTRIBUTIONS,
    QUORUM_MAX_CONTRIBUTIONS,
    QUORUM_MIN_VALID_CONTRIBUTIONS,
)
from aiteam.db.activity_log import log_activity


TERMINAL_QUORUM_STATUSES = frozenset({"accepted", "degraded", "failed"})


def create_quorum_session(
    db_path: Path,
    *,
    issue_id: str,
    base_plan_revision_id: str,
    requested_contributions: int = QUORUM_MIN_VALID_CONTRIBUTIONS,
) -> dict[str, Any]:
    requested = max(
        QUORUM_ABSOLUTE_MIN_VALID_CONTRIBUTIONS,
        min(int(requested_contributions), QUORUM_MAX_CONTRIBUTIONS),
    )
    min_valid = min(QUORUM_MIN_VALID_CONTRIBUTIONS, requested)
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            INSERT INTO quorum_sessions (
                id, issue_id, base_plan_revision_id,
                requested_contributions, min_valid_contributions, next_profile
            ) VALUES (?, ?, ?, ?, ?, 'planning_complete')
            ON CONFLICT(issue_id, base_plan_revision_id) DO UPDATE SET
                requested_contributions = excluded.requested_contributions,
                min_valid_contributions = excluded.min_valid_contributions,
                updated_at = CURRENT_TIMESTAMP
            RETURNING *
            """,
            (
                str(uuid.uuid4()),
                issue_id,
                base_plan_revision_id,
                requested,
                min_valid,
            ),
        ).fetchone()
    return dict(row)


def record_quorum_contribution(
    db_path: Path,
    *,
    session_id: str,
    agent_id: str,
    ordinal: int,
    result: str,
    evidence: str,
    findings: list[dict[str, Any]],
    run_id: str | None = None,
    provider: str = "",
    model: str = "",
    channel: str | None = None,
) -> dict[str, Any]:
    normalized_result = str(result or "").strip().lower()
    clean_evidence = str(evidence or "").strip()
    valid = normalized_result in {"approved", "changes_requested", "blocked"} and bool(
        clean_evidence
    ) and bool(findings)
    with contextlib.closing(_connect(db_path)) as conn:
        session = conn.execute(
            "SELECT status FROM quorum_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise LookupError(f"quorum session not found: {session_id}")
        if str(session["status"]) in TERMINAL_QUORUM_STATUSES:
            raise ValueError(
                f"quorum session {session_id} is terminal: {session['status']}"
            )
        row = conn.execute(
            """
            INSERT INTO quorum_contributions (
                id, session_id, agent_id, run_id, ordinal, provider, model,
                channel, result, evidence, findings_json, valid
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, agent_id) DO UPDATE SET
                run_id = excluded.run_id,
                ordinal = excluded.ordinal,
                provider = excluded.provider,
                model = excluded.model,
                channel = excluded.channel,
                result = excluded.result,
                evidence = excluded.evidence,
                findings_json = excluded.findings_json,
                valid = excluded.valid
            RETURNING *
            """,
            (
                str(uuid.uuid4()),
                session_id,
                agent_id,
                run_id,
                int(ordinal),
                str(provider or "").strip() or None,
                str(model or "").strip() or None,
                channel,
                normalized_result,
                clean_evidence,
                json.dumps(findings, ensure_ascii=False, sort_keys=True),
                1 if valid else 0,
            ),
        ).fetchone()
    return dict(row)


def evaluate_quorum_session(
    db_path: Path, *, session_id: str, persist: bool = True
) -> dict[str, Any]:
    """Evalúa recibos, no prosa: cantidad válida y diversidad de provider.

    ``persist=False`` ofrece una proyección estrictamente read-only para APIs
    GET: calcula el mismo gate sin avanzar ``reviewing`` a ``ready``.
    """
    with contextlib.closing(_connect(db_path)) as conn:
        session = conn.execute(
            "SELECT * FROM quorum_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise LookupError(f"quorum session not found: {session_id}")
        rows = conn.execute(
            """
            SELECT * FROM quorum_contributions
            WHERE session_id = ? ORDER BY ordinal
            """,
            (session_id,),
        ).fetchall()
        valid_rows = [row for row in rows if int(row["valid"] or 0) == 1]
        providers = {
            str(row["provider"] or "").strip().lower()
            for row in valid_rows
            if str(row["provider"] or "").strip()
        }
        min_valid = int(session["min_valid_contributions"])
        enough = len(valid_rows) >= min_valid
        diverse = len(providers) >= min(2, min_valid)
        gate_satisfied = enough and diverse
        current_status = str(session["status"])
        if current_status in TERMINAL_QUORUM_STATUSES:
            # Terminal sessions are immutable.  Metrics remain observable, but
            # ``ready`` must be false so a late report cannot emit a new wakeup.
            ready = False
            status = current_status
        else:
            ready = gate_satisfied
            status = "ready" if ready else "reviewing"
            if persist:
                conn.execute(
                    "UPDATE quorum_sessions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (status, session_id),
                )
    return {
        "ready": ready,
        "status": status,
        "requested_contributions": int(session["requested_contributions"]),
        "min_valid_contributions": min_valid,
        "reduced_quorum": min_valid == QUORUM_ABSOLUTE_MIN_VALID_CONTRIBUTIONS,
        "valid_contributions": len(valid_rows),
        "total_contributions": len(rows),
        "distinct_providers": len(providers),
        "missing_valid": max(0, min_valid - len(valid_rows)),
        "diversity_satisfied": diverse,
    }


def get_quorum_session(db_path: Path, *, session_id: str) -> dict[str, Any] | None:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM quorum_sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row is not None else None


def get_quorum_session_for_issue(
    db_path: Path, *, issue_id: str
) -> dict[str, Any] | None:
    """Última sesión creada para una issue, si existe."""
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT * FROM quorum_sessions
            WHERE issue_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (issue_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def list_quorum_contributions(
    db_path: Path, *, session_id: str
) -> list[dict[str, Any]]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT * FROM quorum_contributions
            WHERE session_id = ?
            ORDER BY ordinal ASC, rowid ASC
            """,
            (session_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def quorum_synthesis_context(db_path: Path, *, session_id: str) -> dict[str, Any]:
    """Payload compacto y estructurado para la síntesis del Lead."""
    with contextlib.closing(_connect(db_path)) as conn:
        session = conn.execute(
            "SELECT * FROM quorum_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise LookupError(f"quorum session not found: {session_id}")
        contributions = conn.execute(
            """
            SELECT id, agent_id, run_id, ordinal, provider, model, channel,
                   result, evidence, findings_json, valid
            FROM quorum_contributions
            WHERE session_id = ? ORDER BY ordinal
            """,
            (session_id,),
        ).fetchall()
        issue = conn.execute(
            "SELECT title, description, metadata_json FROM issues WHERE id=?",
            (session["issue_id"],),
        ).fetchone()
        issue_metadata = _decode_dict(issue["metadata_json"] if issue else "{}")
    return {
        "session_id": session_id,
        "issue_id": session["issue_id"],
        "base_plan_revision_id": session["base_plan_revision_id"],
        "status": session["status"],
        "objective": issue_metadata.get("quorum_objective_snapshot") or {
            "title": issue["title"] if issue else "",
            "description": issue["description"] if issue else "",
        },
        "required_action": "update_plan_then_accept_quorum_synthesis",
        "contributions": [
            {
                **{key: row[key] for key in row.keys() if key != "findings_json"},
                "valid": bool(row["valid"]),
                "findings": _decode_list(row["findings_json"]),
                "audit": _decode_dict(row["evidence"]) or {
                    "agent_report_evidence": row["evidence"]
                },
            }
            for row in contributions
        ],
    }


def degrade_quorum_session(
    db_path: Path, *, session_id: str, skipped_reason: str
) -> dict[str, Any]:
    reason = str(skipped_reason or "quorum_gate_unsatisfied").strip()
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            UPDATE quorum_sessions
            SET status = 'degraded', skipped_reason = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('reviewing', 'ready', 'synthesizing')
            RETURNING *
            """,
            (reason, session_id),
        ).fetchone()
    if row is None:
        existing = get_quorum_session(db_path, session_id=session_id)
        if existing is None:
            raise LookupError(f"quorum session not found: {session_id}")
        return existing
    return dict(row)


def accept_quorum_synthesis(
    db_path: Path,
    *,
    session_id: str,
    synthesis_run_id: str,
    final_plan_revision_id: str,
    dispositions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Acepta la síntesis y crea la continuación durable hacia ``full_team``."""
    existing = get_quorum_session(db_path, session_id=session_id)
    if existing is None:
        raise LookupError(f"quorum session not found: {session_id}")
    if str(existing["status"]) == "accepted":
        same_acceptance = (
            str(existing.get("synthesis_run_id") or "") == str(synthesis_run_id or "")
            and str(existing.get("final_plan_revision_id") or "")
            == str(final_plan_revision_id or "")
            and _decode_list(existing.get("dispositions_json")) == dispositions
        )
        if same_acceptance:
            return existing
        raise ValueError("quorum session is already accepted with a different synthesis")
    if str(existing["status"]) in {"degraded", "failed"}:
        raise ValueError(f"quorum session is terminal: {existing['status']}")
    gate = evaluate_quorum_session(db_path, session_id=session_id)
    if not gate["ready"]:
        raise ValueError("quorum synthesis cannot be accepted before the contribution gate is ready")
    allowed_decisions = {"accept", "qualify", "discard"}
    disposition_by_finding = {
        str(item.get("finding_id") or "").strip(): str(item.get("decision") or "").strip().lower()
        for item in dispositions
        if isinstance(item, dict) and str(item.get("finding_id") or "").strip()
    }
    if any(decision not in allowed_decisions for decision in disposition_by_finding.values()):
        raise ValueError("invalid quorum disposition; expected accept, qualify or discard")
    shallow_rationales = [
        str(item.get("finding_id") or "").strip()
        for item in dispositions
        if isinstance(item, dict)
        and str(item.get("finding_id") or "").strip()
        and len(str(item.get("rationale") or "").strip()) < 20
    ]
    if shallow_rationales:
        raise ValueError(
            "quorum dispositions require substantive rationale for: "
            + ", ".join(sorted(shallow_rationales))
        )

    with contextlib.closing(_connect(db_path)) as conn:
        session = conn.execute(
            "SELECT * FROM quorum_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise LookupError(f"quorum session not found: {session_id}")
        revision = conn.execute(
            """
            SELECT id, created_by_run_id FROM issue_document_revisions
            WHERE id = ? AND issue_id = ? AND key = 'plan'
            """,
            (final_plan_revision_id, session["issue_id"]),
        ).fetchone()
        if revision is None:
            raise ValueError("final plan revision does not exist for the quorum issue")
        if str(revision["created_by_run_id"] or "") != str(synthesis_run_id or ""):
            raise ValueError("final plan revision must be created by the synthesis run")
        synthesis_owner = conn.execute(
            """
            SELECT r.agent_id, a.role
            FROM runs r JOIN agents a ON a.id = r.agent_id
            WHERE r.id = ? AND r.issue_id = ?
            """,
            (synthesis_run_id, session["issue_id"]),
        ).fetchone()
        issue_owner = conn.execute(
            "SELECT assignee_agent_id FROM issues WHERE id = ?",
            (session["issue_id"],),
        ).fetchone()
        if (
            synthesis_owner is None
            or str(synthesis_owner["role"] or "").strip().lower() not in {"lead", "team_lead"}
            or issue_owner is None
            or str(synthesis_owner["agent_id"] or "") != str(issue_owner["assignee_agent_id"] or "")
        ):
            raise ValueError("quorum synthesis must be owned by the configured Lead")
        contribution_rows = conn.execute(
            """
            SELECT findings_json FROM quorum_contributions
            WHERE session_id = ? AND valid = 1
            """,
            (session_id,),
        ).fetchall()
        finding_ids: set[str] = set()
        for row in contribution_rows:
            for finding in _decode_list(row["findings_json"]):
                if isinstance(finding, dict) and str(finding.get("id") or "").strip():
                    finding_ids.add(str(finding["id"]).strip())
        missing = sorted(finding_ids - set(disposition_by_finding))
        if missing:
            raise ValueError(f"quorum synthesis is missing dispositions for: {', '.join(missing)}")

        issue_row = conn.execute(
            "SELECT metadata_json, assignee_agent_id FROM issues WHERE id = ?",
            (session["issue_id"],),
        ).fetchone()
        if issue_row is None:
            raise LookupError(f"quorum issue not found: {session['issue_id']}")
        metadata = _decode_dict(issue_row["metadata_json"])
        metadata.update(
            {
                "profile": "lead_quorum",
                "planning_status": "accepted_plan",
                "quorum_session_id": session_id,
                "quorum_plan_revision_id": final_plan_revision_id,
            }
        )
        accepted_row = conn.execute(
            """
            UPDATE quorum_sessions
            SET status = 'accepted', synthesis_run_id = ?, final_plan_revision_id = ?,
                dispositions_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'ready'
            RETURNING *
            """,
            (
                synthesis_run_id,
                final_plan_revision_id,
                json.dumps(dispositions, ensure_ascii=False, sort_keys=True),
                session_id,
            ),
        ).fetchone()
        if accepted_row is None:
            raise ValueError("quorum session changed state before synthesis acceptance")
        conn.execute(
            "UPDATE issues SET metadata_json = ?, status = 'done', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(metadata, ensure_ascii=False, sort_keys=True), session["issue_id"]),
        )
        issue_id = str(session["issue_id"])
        assignee = str(issue_row["assignee_agent_id"] or "")

    log_activity(
        db_path,
        action="quorum.accepted",
        target_type="quorum_session",
        target_id=session_id,
        actor_agent_id=assignee or None,
        run_id=synthesis_run_id or None,
        payload={
            "issue_id": issue_id,
            "final_plan_revision_id": final_plan_revision_id,
            "completion_artifact": "accepted_plan",
            "next_profile": None,
            "finding_count": len(finding_ids),
        },
    )
    with contextlib.closing(_connect(db_path)) as conn:
        return dict(conn.execute("SELECT * FROM quorum_sessions WHERE id = ?", (session_id,)).fetchone())


def _decode_dict(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _decode_list(raw: Any) -> list[Any]:
    try:
        value = json.loads(str(raw or "[]"))
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
