"""Workflow owner-gated y reversible para cambios de proveedor."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.db.provider_changes import ensure_provider_change_schema
from aiteam.policies import CANONICAL_ROLES, canonical_role

SCHEMA_VERSION = "provider_change_workflow_v1"
CASE_STATUSES = frozenset(
    {
        "awaiting_confirmation",
        "awaiting_classification",
        "awaiting_approval",
        "approved",
        "awaiting_validation",
        "validation_failed",
        "awaiting_recalibration",
        "ready_to_accept",
        "accepted",
        "rejected",
        "reverted",
    }
)
TERMINAL_STATUSES = frozenset({"rejected"})
_ACTIVE_INVALIDATION_STATUSES = frozenset(
    {
        "approved",
        "awaiting_validation",
        "validation_failed",
        "awaiting_recalibration",
        "ready_to_accept",
        "accepted",
    }
)
_APPLICATION_KINDS = frozenset(
    {
        "no_code_change",
        "pin_updated",
        "adapter_updated",
        "catalog_updated",
        "manual_remediation",
    }
)

_WORKFLOW_SQL = """
CREATE TABLE IF NOT EXISTS provider_change_cases (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    trigger_id TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL,
    event_fingerprint TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN (
            'awaiting_confirmation', 'awaiting_classification',
            'awaiting_approval', 'approved', 'awaiting_validation',
            'validation_failed', 'awaiting_recalibration',
            'ready_to_accept', 'accepted', 'rejected', 'reverted'
        )
    ),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    owner TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    diff_json TEXT NOT NULL,
    impact_json TEXT NOT NULL,
    recommendation_json TEXT NOT NULL,
    guided_commands_json TEXT NOT NULL,
    risk_json TEXT NOT NULL,
    rollback_json TEXT NOT NULL,
    classification_json TEXT,
    approval_json TEXT,
    application_json TEXT,
    validation_json TEXT,
    outcome_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_change_cases_status
    ON provider_change_cases(status, severity, updated_at DESC, id);

CREATE TABLE IF NOT EXISTS provider_change_case_history (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(case_id) REFERENCES provider_change_cases(id),
    UNIQUE(case_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_provider_change_case_history_case
    ON provider_change_case_history(case_id, sequence);

CREATE TABLE IF NOT EXISTS provider_change_evidence_invalidations (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    canonical_role TEXT NOT NULL,
    reason TEXT NOT NULL,
    new_selection_policy TEXT NOT NULL CHECK (
        new_selection_policy IN ('preserve', 'block_affected')
    ),
    status TEXT NOT NULL CHECK (status IN ('active', 'restored')),
    created_at TEXT NOT NULL,
    restored_at TEXT,
    FOREIGN KEY(case_id) REFERENCES provider_change_cases(id),
    UNIQUE(case_id, profile_id, model_id, canonical_role)
);
CREATE INDEX IF NOT EXISTS idx_provider_change_invalidations_active
    ON provider_change_evidence_invalidations(
        status, profile_id, model_id, canonical_role
    );
"""


class ProviderChangeConflictError(Exception):
    """La revisión o transición ya no coincide con el expediente durable."""


def ensure_provider_change_workflow_schema(db_path: Path) -> None:
    ensure_provider_change_schema(db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        conn.executescript(_WORKFLOW_SQL)


def reconcile_provider_change_cases(
    db_path: Path,
    *,
    now: datetime | str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Materializa un expediente idempotente por trigger pendiente."""
    current = _coerce_datetime(now)
    ensure_provider_change_workflow_schema(db_path)
    created_ids: list[str] = []
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                """
                SELECT
                    t.id AS trigger_id,
                    t.event_fingerprint,
                    t.affected_scope_json,
                    e.id AS event_id,
                    e.owner,
                    e.severity,
                    e.kind,
                    e.dimension,
                    e.profile_id,
                    e.provider_id,
                    e.component_id,
                    e.surface,
                    e.change_json,
                    d.payload_json AS diff_json
                FROM provider_change_triggers t
                JOIN provider_change_events e
                  ON e.fingerprint = t.event_fingerprint
                JOIN provider_change_diffs d
                  ON d.diff_sha256 = e.source_diff_sha256
                LEFT JOIN provider_change_cases c
                  ON c.trigger_id = t.id
                WHERE t.status = 'pending'
                  AND c.id IS NULL
                ORDER BY t.created_at ASC, t.id ASC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
            for row in rows:
                case_id = str(uuid.uuid4())
                change = json.loads(row["change_json"])
                diff = json.loads(row["diff_json"])
                affected_scope = json.loads(row["affected_scope_json"])
                projections = _initial_case_projection(
                    row=dict(row),
                    change=change,
                    diff=diff,
                    affected_scope=affected_scope,
                )
                conn.execute(
                    """
                    INSERT INTO provider_change_cases (
                        id, schema_version, trigger_id, event_id,
                        event_fingerprint, status, revision, owner, severity,
                        title, summary, diff_json, impact_json,
                        recommendation_json, guided_commands_json, risk_json,
                        rollback_json, created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, 'awaiting_confirmation', 1, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        case_id,
                        SCHEMA_VERSION,
                        row["trigger_id"],
                        row["event_id"],
                        row["event_fingerprint"],
                        row["owner"],
                        row["severity"],
                        projections["title"],
                        projections["summary"],
                        _json(diff),
                        _json(projections["impact"]),
                        _json(projections["recommendation"]),
                        _json(projections["guided_commands"]),
                        _json(projections["risk"]),
                        _json(projections["rollback"]),
                        current,
                        current,
                    ),
                )
                _append_history(
                    conn,
                    case_id=case_id,
                    sequence=1,
                    action="observe",
                    from_status=None,
                    to_status="awaiting_confirmation",
                    actor="system",
                    payload={
                        "trigger_id": row["trigger_id"],
                        "event_fingerprint": row["event_fingerprint"],
                    },
                    now_iso=current,
                )
                created_ids.append(case_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return [get_provider_change_case(db_path, case_id) for case_id in created_ids]


def transition_provider_change_case(
    db_path: Path,
    case_id: str,
    *,
    action: str,
    expected_revision: int,
    actor: str,
    payload: Mapping[str, Any] | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    current_time = _coerce_datetime(now)
    actor_id = str(actor or "").strip()
    if not actor_id:
        raise ValueError("provider change transition requires actor")
    data = _redacted_transition_payload(dict(payload or {}))
    ensure_provider_change_workflow_schema(db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            raw = conn.execute(
                "SELECT * FROM provider_change_cases WHERE id = ?",
                (case_id,),
            ).fetchone()
            if raw is None:
                raise KeyError(case_id)
            case = _decode_case(raw)
            if int(case["revision"]) != int(expected_revision):
                raise ProviderChangeConflictError(
                    "provider change case revision is stale"
                )
            changes = _transition(
                conn,
                case=case,
                action=action,
                actor=actor_id,
                payload=data,
                now_iso=current_time,
            )
            next_status = str(changes.pop("status"))
            revision = int(case["revision"]) + 1
            assignments = ["status = ?", "revision = ?", "updated_at = ?"]
            values: list[Any] = [next_status, revision, current_time]
            for column, value in changes.items():
                assignments.append(f"{column} = ?")
                values.append(_json(value) if value is not None else None)
            values.extend([case_id, int(expected_revision)])
            updated = conn.execute(
                f"""
                UPDATE provider_change_cases
                SET {", ".join(assignments)}
                WHERE id = ? AND revision = ?
                RETURNING *
                """,
                values,
            ).fetchone()
            if updated is None:
                raise ProviderChangeConflictError(
                    "provider change case changed concurrently"
                )
            _append_history(
                conn,
                case_id=case_id,
                sequence=revision,
                action=action,
                from_status=str(case["status"]),
                to_status=next_status,
                actor=actor_id,
                payload=data,
                now_iso=current_time,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return get_provider_change_case(db_path, case_id)


def get_provider_change_case(
    db_path: Path,
    case_id: str,
) -> dict[str, Any]:
    if not Path(db_path).exists():
        raise KeyError(case_id)
    with contextlib.closing(_connect_readonly(db_path)) as conn:
        if not _table_exists(conn, "provider_change_cases"):
            raise KeyError(case_id)
        row = conn.execute(
            "SELECT * FROM provider_change_cases WHERE id = ?",
            (case_id,),
        ).fetchone()
        if row is None:
            raise KeyError(case_id)
        result = _decode_case(row)
        history = conn.execute(
            """
            SELECT * FROM provider_change_case_history
            WHERE case_id = ? ORDER BY sequence ASC
            """,
            (case_id,),
        ).fetchall()
        result["history"] = [_decode_history(item) for item in history]
        invalidations = conn.execute(
            """
            SELECT * FROM provider_change_evidence_invalidations
            WHERE case_id = ?
            ORDER BY profile_id, model_id, canonical_role
            """,
            (case_id,),
        ).fetchall()
        result["invalidations"] = [dict(item) for item in invalidations]
        return result


def list_provider_change_cases(
    db_path: Path,
    *,
    statuses: set[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    requested = statuses or set(CASE_STATUSES)
    if not requested <= CASE_STATUSES:
        raise ValueError("provider change case status filter drift")
    if not Path(db_path).exists():
        return []
    with contextlib.closing(_connect_readonly(db_path)) as conn:
        if not _table_exists(conn, "provider_change_cases"):
            return []
        placeholders = ",".join("?" for _ in sorted(requested))
        rows = conn.execute(
            f"""
            SELECT * FROM provider_change_cases
            WHERE status IN ({placeholders})
            ORDER BY
                CASE severity
                    WHEN 'critical' THEN 0
                    WHEN 'error' THEN 1
                    WHEN 'warning' THEN 2
                    ELSE 3
                END,
                updated_at DESC,
                id ASC
            LIMIT ?
            """,
            [*sorted(requested), max(1, min(int(limit), 500))],
        ).fetchall()
    return [_decode_case(row) for row in rows]


def list_active_provider_change_invalidations(
    db_path: Path,
) -> list[dict[str, Any]]:
    """Reader read-only para catálogo; una DB ausente nunca se crea."""
    if not Path(db_path).exists():
        return []
    with contextlib.closing(_connect_readonly(db_path)) as conn:
        if not _table_exists(conn, "provider_change_evidence_invalidations"):
            return []
        rows = conn.execute(
            """
            SELECT * FROM provider_change_evidence_invalidations
            WHERE status = 'active'
            ORDER BY profile_id, model_id, canonical_role, id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def evidence_is_invalidated(
    invalidations: list[Mapping[str, Any]],
    *,
    profile_id: str,
    model_id: str,
    role: str,
) -> list[dict[str, Any]]:
    normalized_role = canonical_role(role)
    return [
        dict(row)
        for row in invalidations
        if row.get("status") == "active"
        and str(row.get("profile_id")) == profile_id
        and str(row.get("model_id")) in {"*", model_id}
        and str(row.get("canonical_role")) in {"*", normalized_role}
    ]


def _transition(
    conn: sqlite3.Connection,
    *,
    case: dict[str, Any],
    action: str,
    actor: str,
    payload: dict[str, Any],
    now_iso: str,
) -> dict[str, Any]:
    status = str(case["status"])
    if status in TERMINAL_STATUSES:
        raise ValueError("terminal provider change case cannot transition")
    if action == "confirm" and status == "awaiting_confirmation":
        return {"status": "awaiting_classification"}
    if action == "reject" and status in {
        "awaiting_confirmation",
        "awaiting_classification",
        "awaiting_approval",
    }:
        reason = _required_text(payload, "reason", max_length=2000)
        _set_trigger_status(
            conn, case, status="dismissed", now_iso=now_iso
        )
        _set_event_status(conn, case, status="resolved", now_iso=now_iso)
        return {
            "status": "rejected",
            "outcome_json": {
                "decision": "rejected",
                "reason": reason,
                "actor": actor,
                "recorded_at": now_iso,
            },
        }
    if action == "classify" and status == "awaiting_classification":
        classification = _normalize_classification(case, payload)
        return {
            "status": "awaiting_approval",
            "classification_json": classification,
            "impact_json": classification["impact"],
        }
    if action == "approve" and status == "awaiting_approval":
        classification = case.get("classification")
        if not isinstance(classification, dict):
            raise ValueError("provider change case is not classified")
        note = str(payload.get("note") or "").strip()
        _activate_invalidations(
            conn,
            case=case,
            classification=classification,
            now_iso=now_iso,
        )
        return {
            "status": "approved",
            "approval_json": {
                "approved_by": actor,
                "approved_at": now_iso,
                "note": note or None,
                "automatic_execution_allowed": False,
            },
        }
    if action == "record_application" and status == "approved":
        application = _normalize_application(payload, actor=actor, now_iso=now_iso)
        return {
            "status": "awaiting_validation",
            "application_json": application,
        }
    if action == "record_validation" and status in {
        "awaiting_validation",
        "validation_failed",
    }:
        validation = _normalize_validation(payload, actor=actor, now_iso=now_iso)
        if validation["result"] == "failed":
            next_status = "validation_failed"
        elif _case_requires_recalibration(case):
            next_status = "awaiting_recalibration"
        else:
            next_status = "ready_to_accept"
        return {"status": next_status, "validation_json": validation}
    if action == "record_recalibration" and status == "awaiting_recalibration":
        evidence = _normalize_recalibration(payload, actor=actor, now_iso=now_iso)
        validation = dict(case.get("validation") or {})
        validation["recalibration"] = evidence
        return {
            "status": (
                "ready_to_accept"
                if evidence["result"] == "passed"
                else "validation_failed"
            ),
            "validation_json": validation,
        }
    if action == "accept" and status == "ready_to_accept":
        _restore_invalidations(conn, case_id=str(case["id"]), now_iso=now_iso)
        _set_trigger_status(conn, case, status="consumed", now_iso=now_iso)
        _set_event_status(conn, case, status="resolved", now_iso=now_iso)
        return {
            "status": "accepted",
            "outcome_json": {
                "decision": "accepted",
                "accepted_by": actor,
                "accepted_at": now_iso,
                "rollback_available": True,
            },
        }
    if action == "revert" and status in _ACTIVE_INVALIDATION_STATUSES:
        reason = _required_text(payload, "reason", max_length=2000)
        evidence = _receipt_refs(payload.get("evidence_receipts"))
        _restore_invalidations(conn, case_id=str(case["id"]), now_iso=now_iso)
        _set_trigger_status(conn, case, status="pending", now_iso=now_iso)
        _set_event_status(conn, case, status="acknowledged", now_iso=now_iso)
        return {
            "status": "reverted",
            "outcome_json": {
                "decision": "reverted",
                "reverted_by": actor,
                "reverted_at": now_iso,
                "reason": reason,
                "evidence_receipts": evidence,
            },
        }
    if action == "reopen" and status == "reverted":
        return {
            "status": "awaiting_classification",
            "classification_json": None,
            "approval_json": None,
            "application_json": None,
            "validation_json": None,
            "outcome_json": None,
        }
    raise ValueError(
        f"invalid provider change transition: {status}:{action}"
    )


def _normalize_classification(
    case: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    impact = payload.get("impact")
    if not isinstance(impact, Mapping):
        raise TypeError("classification requires impact")
    base_impact = dict(case["impact"])
    profile_ids = _string_list(
        impact.get("profile_ids") or base_impact.get("profile_ids"),
        field="profile_ids",
    )
    if not profile_ids:
        raise ValueError("classification requires affected profile")
    model_ids = _string_list(
        impact.get("model_ids") or base_impact.get("model_ids"),
        field="model_ids",
    )
    raw_roles = _string_list(impact.get("roles"), field="roles")
    roles = sorted({canonical_role(role) for role in raw_roles})
    unknown = set(roles) - set(CANONICAL_ROLES)
    if unknown:
        raise ValueError(f"classification roles are unknown: {sorted(unknown)}")
    all_models = impact.get("all_models") is True
    all_roles = impact.get("all_roles") is True
    if all_models and model_ids:
        raise ValueError("all_models cannot be combined with model_ids")
    if all_roles and roles:
        raise ValueError("all_roles cannot be combined with roles")
    if not all_models and not model_ids:
        raise ValueError("classification requires models or all_models")
    if not all_roles and not roles:
        raise ValueError("classification requires roles or all_roles")
    impact_level = str(payload.get("impact_level") or "").strip()
    if impact_level not in {"informational", "limited", "material", "critical"}:
        raise ValueError("classification impact_level drift")
    requires_recalibration = payload.get("requires_recalibration")
    if not isinstance(requires_recalibration, bool):
        raise TypeError("classification requires recalibration decision")
    normalized_impact = {
        **base_impact,
        "profile_ids": profile_ids,
        "model_ids": model_ids,
        "roles": roles,
        "all_models": all_models,
        "all_roles": all_roles,
        "new_selection_policy": str(
            impact.get("new_selection_policy")
            or base_impact.get("new_selection_policy")
            or "preserve"
        ),
        "existing_assignment_policy": str(
            impact.get("existing_assignment_policy")
            or base_impact.get("existing_assignment_policy")
            or "preserve_and_notify"
        ),
    }
    return {
        "impact_level": impact_level,
        "requires_recalibration": requires_recalibration,
        "impact": normalized_impact,
        "rationale": _required_text(payload, "rationale", max_length=4000),
    }


def _normalize_application(
    payload: Mapping[str, Any],
    *,
    actor: str,
    now_iso: str,
) -> dict[str, Any]:
    kind = str(payload.get("kind") or "").strip()
    if kind not in _APPLICATION_KINDS:
        raise ValueError("provider change application kind drift")
    return {
        "kind": kind,
        "summary": _required_text(payload, "summary", max_length=4000),
        "evidence_receipts": _receipt_refs(payload.get("evidence_receipts")),
        "applied_by": actor,
        "applied_at": now_iso,
        "executed_by_workflow": False,
    }


def _normalize_validation(
    payload: Mapping[str, Any],
    *,
    actor: str,
    now_iso: str,
) -> dict[str, Any]:
    result = str(payload.get("result") or "").strip()
    if result not in {"passed", "failed"}:
        raise ValueError("provider change validation result drift")
    doctor = str(payload.get("doctor") or "").strip()
    probe = str(payload.get("probe") or "").strip()
    if doctor not in {"passed", "failed", "not_applicable"}:
        raise ValueError("provider change doctor result drift")
    if probe not in {"passed", "failed", "not_applicable"}:
        raise ValueError("provider change probe result drift")
    if result == "passed" and "failed" in {doctor, probe}:
        raise ValueError("passed validation cannot contain failed gate")
    return {
        "result": result,
        "doctor": doctor,
        "probe": probe,
        "summary": _required_text(payload, "summary", max_length=4000),
        "evidence_receipts": _receipt_refs(payload.get("evidence_receipts")),
        "validated_by": actor,
        "validated_at": now_iso,
    }


def _normalize_recalibration(
    payload: Mapping[str, Any],
    *,
    actor: str,
    now_iso: str,
) -> dict[str, Any]:
    result = str(payload.get("result") or "").strip()
    if result not in {"passed", "failed"}:
        raise ValueError("provider change recalibration result drift")
    receipts = _receipt_refs(payload.get("evidence_receipts"))
    if result == "passed" and not receipts:
        raise ValueError("passed recalibration requires evidence receipts")
    return {
        "result": result,
        "summary": _required_text(payload, "summary", max_length=4000),
        "evidence_receipts": receipts,
        "recorded_by": actor,
        "recorded_at": now_iso,
    }


def _activate_invalidations(
    conn: sqlite3.Connection,
    *,
    case: Mapping[str, Any],
    classification: Mapping[str, Any],
    now_iso: str,
) -> None:
    impact = classification["impact"]
    model_ids = ["*"] if impact["all_models"] else impact["model_ids"]
    roles = ["*"] if impact["all_roles"] else impact["roles"]
    reason = f"provider_change:{case['event_fingerprint']}"
    for profile_id in impact["profile_ids"]:
        for model_id in model_ids:
            for role in roles:
                conn.execute(
                    """
                    INSERT INTO provider_change_evidence_invalidations (
                        id, case_id, profile_id, model_id, canonical_role,
                        reason, new_selection_policy, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
                    ON CONFLICT(
                        case_id, profile_id, model_id, canonical_role
                    ) DO UPDATE SET
                        reason = excluded.reason,
                        new_selection_policy = excluded.new_selection_policy,
                        status = 'active',
                        created_at = excluded.created_at,
                        restored_at = NULL
                    """,
                    (
                        str(uuid.uuid4()),
                        case["id"],
                        profile_id,
                        model_id,
                        role,
                        reason,
                        impact["new_selection_policy"],
                        now_iso,
                    ),
                )


def _restore_invalidations(
    conn: sqlite3.Connection,
    *,
    case_id: str,
    now_iso: str,
) -> None:
    conn.execute(
        """
        UPDATE provider_change_evidence_invalidations
        SET status = 'restored', restored_at = ?
        WHERE case_id = ? AND status = 'active'
        """,
        (now_iso, case_id),
    )


def _set_trigger_status(
    conn: sqlite3.Connection,
    case: Mapping[str, Any],
    *,
    status: str,
    now_iso: str,
) -> None:
    conn.execute(
        """
        UPDATE provider_change_triggers
        SET status = ?,
            consumed_at = CASE WHEN ? = 'consumed' THEN ? ELSE NULL END
        WHERE id = ?
        """,
        (status, status, now_iso, case["trigger_id"]),
    )


def _set_event_status(
    conn: sqlite3.Connection,
    case: Mapping[str, Any],
    *,
    status: str,
    now_iso: str,
) -> None:
    conn.execute(
        """
        UPDATE provider_change_events
        SET status = ?,
            acknowledged_at = CASE
                WHEN ? = 'acknowledged' THEN COALESCE(acknowledged_at, ?)
                ELSE acknowledged_at
            END,
            resolved_at = CASE WHEN ? = 'resolved' THEN ? ELSE NULL END,
            updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            status,
            now_iso,
            status,
            now_iso,
            now_iso,
            case["event_id"],
        ),
    )


def _initial_case_projection(
    *,
    row: dict[str, Any],
    change: dict[str, Any],
    diff: dict[str, Any],
    affected_scope: dict[str, Any],
) -> dict[str, Any]:
    kind = str(row["kind"])
    model_removed = kind in {
        "model_removed",
        "installed_retired",
        "installed_incompatible",
    }
    profile_id = str(row.get("profile_id") or "").strip()
    impact = {
        **affected_scope,
        "profile_ids": [profile_id] if profile_id else [],
        "roles": [],
        "all_roles": False,
        "new_selection_policy": (
            "block_affected" if model_removed else "preserve"
        ),
        "existing_assignment_policy": (
            "request_replacement" if model_removed else "preserve_and_notify"
        ),
    }
    commands = [
        {
            "id": "doctor",
            "label": "Repetir doctor",
            "command": ".\\scripts\\python_local.bat scripts\\machine_doctor.py --json",
            "execution": "manual_only",
        },
        {
            "id": "provider_probe",
            "label": "Repetir probe exacto del provider",
            "command": None,
            "execution": "guided_in_product",
        },
    ]
    return {
        "title": f"Cambio de proveedor: {kind}",
        "summary": (
            f"{row['provider_id']} / {row['component_id']} cambió "
            f"{row['dimension']}"
        ),
        "impact": impact,
        "recommendation": {
            "decision": diff["summary"]["decision"],
            "next_step": "confirm_and_classify",
            "automatic_update_allowed": False,
            "routing_change_allowed": False,
        },
        "guided_commands": commands,
        "risk": {
            "level": str(row["severity"]),
            "primary": "evidence_or_runtime_contract_may_be_stale",
            "silent_assignment_mutation_allowed": False,
        },
        "rollback": {
            "strategy": "restore_previous_pin_or_adapter_then_revalidate",
            "automatic": False,
            "preserve_existing_assignments": True,
        },
    }


def _case_requires_recalibration(case: Mapping[str, Any]) -> bool:
    classification = case.get("classification")
    return bool(
        isinstance(classification, Mapping)
        and classification.get("requires_recalibration") is True
    )


def _append_history(
    conn: sqlite3.Connection,
    *,
    case_id: str,
    sequence: int,
    action: str,
    from_status: str | None,
    to_status: str,
    actor: str,
    payload: Mapping[str, Any],
    now_iso: str,
) -> None:
    conn.execute(
        """
        INSERT INTO provider_change_case_history (
            id, case_id, sequence, action, from_status, to_status,
            actor, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            case_id,
            sequence,
            action,
            from_status,
            to_status,
            actor,
            _json(payload),
            now_iso,
        ),
    )


def _decode_case(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for column in (
        "diff_json",
        "impact_json",
        "recommendation_json",
        "guided_commands_json",
        "risk_json",
        "rollback_json",
        "classification_json",
        "approval_json",
        "application_json",
        "validation_json",
        "outcome_json",
    ):
        key = column.removesuffix("_json")
        raw = result.pop(column)
        result[key] = json.loads(raw) if raw is not None else None
    return result


def _decode_history(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    return result


def _redacted_transition_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    forbidden = {"secret", "api_key", "token", "credential", "password"}
    pending: list[Any] = [payload]
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            if any(str(key).lower() in forbidden for key in current):
                raise ValueError(
                    "provider change transition contains secret field"
                )
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return dict(payload)


def _receipt_refs(value: Any) -> list[str]:
    refs = _string_list(value, field="evidence_receipts")
    for ref in refs:
        if Path(ref).is_absolute() or ".." in Path(ref).parts:
            raise ValueError("provider change evidence receipt must be relative")
    return refs


def _string_list(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    normalized = sorted(
        {str(item).strip() for item in value if str(item).strip()}
    )
    if len(normalized) != len(value):
        raise ValueError(f"{field} contains empty or duplicate values")
    return normalized


def _required_text(
    payload: Mapping[str, Any],
    field: str,
    *,
    max_length: int,
) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ValueError(f"provider change transition requires {field}")
    if len(value) > max_length:
        raise ValueError(f"provider change {field} exceeds {max_length}")
    return value


def _coerce_datetime(value: datetime | str | None) -> str:
    parsed = (
        datetime.now(timezone.utc)
        if value is None
        else value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("provider workflow timestamp must have timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (name,),
        ).fetchone()
        is not None
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(db_path), timeout=20.0, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn
