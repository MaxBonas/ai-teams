"""Estado SQLite versionado del asistente guiado."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "guided_setup_v1"
SCOPES = ("machine_onboarding", "project_setup", "installation_repair")
STEP_STATUSES = frozenset(
    {"not_started", "in_progress", "blocked", "skipped", "passed"}
)
_TERMINAL_STEP_STATUSES = frozenset({"skipped", "passed"})
_FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "password",
        "secret",
        "secret_value",
        "access_token",
        "refresh_token",
        "bearer_token",
        "authorization",
    }
)
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS guided_setup_sessions (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    scope TEXT NOT NULL
        CHECK (scope IN ('machine_onboarding', 'project_setup', 'installation_repair')),
    subject_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'blocked', 'passed')),
    current_step TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE(schema_version, scope, subject_key)
);
CREATE TABLE IF NOT EXISTS guided_setup_steps (
    session_id TEXT NOT NULL REFERENCES guided_setup_sessions(id) ON DELETE CASCADE,
    step_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (status IN ('not_started', 'in_progress', 'blocked', 'skipped', 'passed')),
    response_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    blocker_code TEXT,
    skip_reason TEXT,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, step_key),
    UNIQUE(session_id, ordinal)
);
CREATE TABLE IF NOT EXISTS guided_setup_preparation_receipts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    needs_hash TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    doctor_hash TEXT NOT NULL,
    ready INTEGER NOT NULL CHECK (ready IN (0, 1)),
    blockers_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id, step_key)
        REFERENCES guided_setup_steps(session_id, step_key) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS guided_setup_project_commit_receipts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE
        REFERENCES guided_setup_sessions(id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    project_target TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS guided_setup_project_preflight_artifacts (
    session_id TEXT NOT NULL
        REFERENCES guided_setup_sessions(id) ON DELETE CASCADE,
    reference TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    kind TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_json TEXT NOT NULL,
    fixture_evidence_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, reference)
);
CREATE TABLE IF NOT EXISTS guided_setup_project_preflight_receipts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL
        REFERENCES guided_setup_sessions(id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    preflight_hash TEXT NOT NULL,
    execution_plan_hash TEXT NOT NULL,
    execution_receipt_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('go', 'no_go')),
    fixture_evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    preflight_json TEXT NOT NULL,
    execution_receipt_json TEXT NOT NULL,
    receipt_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, receipt_hash)
);
"""

_CONTRACT: dict[str, tuple[dict[str, Any], ...]] = {
    "machine_onboarding": (
        {"key": "welcome", "required": True, "depends_on": ()},
        {"key": "projects_root", "required": True, "depends_on": ("welcome",)},
        {"key": "needs_profile", "required": True, "depends_on": ("projects_root",)},
        {"key": "adapter_setup", "required": True, "depends_on": ("needs_profile",)},
        {"key": "machine_preflight", "required": True, "depends_on": ("adapter_setup",)},
        {"key": "machine_review", "required": True, "depends_on": ("machine_preflight",)},
    ),
    "project_setup": (
        {"key": "project_identity", "required": True, "depends_on": ()},
        {"key": "objective_profile", "required": True, "depends_on": ("project_identity",)},
        {"key": "ecosystem_detection", "required": False, "depends_on": ("objective_profile",)},
        {"key": "team_profile", "required": True, "depends_on": ("objective_profile",)},
        {"key": "lead_selection", "required": True, "depends_on": ("team_profile",)},
        {"key": "project_preflight", "required": True, "depends_on": ("lead_selection",)},
        {"key": "project_review", "required": True, "depends_on": ("project_preflight",)},
    ),
    "installation_repair": (
        {"key": "diagnosis", "required": True, "depends_on": ()},
        {"key": "repair_plan", "required": True, "depends_on": ("diagnosis",)},
        {"key": "repair_preflight", "required": True, "depends_on": ("repair_plan",)},
        {"key": "repair_review", "required": True, "depends_on": ("repair_preflight",)},
    ),
}


class GuidedSetupConflict(RuntimeError):
    """La revisión o transición solicitada ya no coincide con SQLite."""


def setup_contract(scope: str) -> dict[str, Any]:
    clean_scope = _scope(scope)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": clean_scope,
        "steps": [
            {
                "key": row["key"],
                "ordinal": ordinal,
                "required": row["required"],
                "depends_on": list(row["depends_on"]),
            }
            for ordinal, row in enumerate(_CONTRACT[clean_scope])
        ],
    }


def create_or_resume_setup(
    db_path: Path,
    *,
    scope: str,
    subject_key: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    clean_scope = _scope(scope)
    clean_subject = _subject(subject_key)
    clean_metadata = _payload(metadata or {})
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """
            SELECT id FROM guided_setup_sessions
            WHERE schema_version = ? AND scope = ? AND subject_key = ?
            """,
            (SCHEMA_VERSION, clean_scope, clean_subject),
        ).fetchone()
        if existing is None:
            session_id = str(uuid.uuid4())
            contract = setup_contract(clean_scope)
            conn.execute(
                """
                INSERT INTO guided_setup_sessions
                    (id, schema_version, scope, subject_key, current_step, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    SCHEMA_VERSION,
                    clean_scope,
                    clean_subject,
                    contract["steps"][0]["key"],
                    _json(clean_metadata),
                ),
            )
            conn.executemany(
                """
                INSERT INTO guided_setup_steps
                    (session_id, step_key, ordinal, required)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        session_id,
                        step["key"],
                        step["ordinal"],
                        int(step["required"]),
                    )
                    for step in contract["steps"]
                ],
            )
        else:
            session_id = str(existing["id"])
        conn.commit()
        return _read_session(conn, session_id)


def get_setup(db_path: Path, session_id: str) -> dict[str, Any]:
    with contextlib.closing(_connect(db_path)) as conn:
        return _read_session(conn, session_id)


def transition_setup_step(
    db_path: Path,
    session_id: str,
    step_key: str,
    *,
    status: str,
    expected_revision: int,
    response: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
    blocker_code: str | None = None,
    skip_reason: str | None = None,
) -> dict[str, Any]:
    clean_status = str(status or "").strip()
    if clean_status not in STEP_STATUSES - {"not_started"}:
        raise ValueError("guided_setup_status_not_allowed")
    clean_response = _payload(response or {})
    clean_evidence = _payload(evidence or {})
    clean_blocker = str(blocker_code or "").strip() or None
    clean_skip = str(skip_reason or "").strip() or None
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = _session_row(conn, session_id)
        if int(session["revision"]) != int(expected_revision):
            raise GuidedSetupConflict("guided_setup_revision_conflict")
        contract = setup_contract(str(session["scope"]))
        definitions = {row["key"]: row for row in contract["steps"]}
        if step_key not in definitions:
            raise KeyError("guided_setup_step_not_found")
        step = conn.execute(
            "SELECT * FROM guided_setup_steps WHERE session_id = ? AND step_key = ?",
            (session_id, step_key),
        ).fetchone()
        if step is None:
            raise KeyError("guided_setup_step_not_found")
        current_status = str(step["status"])
        if clean_status == current_status and clean_status in _TERMINAL_STEP_STATUSES:
            conn.rollback()
            return _read_session(conn, session_id)
        allowed = {
            "not_started": {"in_progress", "blocked", "skipped"},
            "in_progress": {"in_progress", "passed", "blocked", "skipped"},
            "blocked": {"in_progress", "blocked", "skipped"},
            "skipped": set(),
            "passed": set(),
        }[current_status]
        if clean_status not in allowed:
            raise GuidedSetupConflict("guided_setup_transition_not_allowed")
        definition = definitions[step_key]
        if clean_status == "passed" and step_key in {
            "needs_profile",
            "objective_profile",
        }:
            from aiteam.guided_setup_needs import validate_needs_submission

            clean_response = validate_needs_submission(
                clean_response,
                scope=str(session["scope"]),
            )
        if clean_status == "passed" and step_key == "project_identity":
            from aiteam.guided_setup_project_proposal import (
                normalize_project_identity_intent,
            )

            clean_response = normalize_project_identity_intent(clean_response)
        if clean_status == "passed" and step_key == "adapter_setup":
            receipt = conn.execute(
                """
                SELECT id, schema_version, needs_hash, plan_hash, doctor_hash,
                       ready, blockers_json
                FROM guided_setup_preparation_receipts
                WHERE session_id = ? AND step_key = ?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (session_id, step_key),
            ).fetchone()
            if receipt is None or not bool(receipt["ready"]):
                raise ValueError("guided_setup_preparation_ready_receipt_required")
            clean_response = {
                "preparation_receipt_ref": f"sha256:{receipt['plan_hash']}"
            }
            clean_evidence = {
                "id": str(receipt["id"]),
                "schema_version": str(receipt["schema_version"]),
                "needs_hash": str(receipt["needs_hash"]),
                "plan_hash": str(receipt["plan_hash"]),
                "doctor_hash": str(receipt["doctor_hash"]),
                "ready": True,
                "blockers": json.loads(str(receipt["blockers_json"])),
            }
        if clean_status == "skipped" and bool(definition["required"]):
            raise ValueError("guided_setup_required_step_cannot_skip")
        if clean_status == "skipped" and not clean_skip:
            raise ValueError("guided_setup_skip_reason_required")
        if clean_status == "blocked" and not clean_blocker:
            raise ValueError("guided_setup_blocker_code_required")
        _require_dependencies(conn, session_id, definition)
        conn.execute(
            """
            UPDATE guided_setup_steps SET
                status = ?,
                response_json = ?,
                evidence_json = ?,
                blocker_code = ?,
                skip_reason = ?,
                started_at = CASE
                    WHEN started_at IS NULL THEN CURRENT_TIMESTAMP ELSE started_at END,
                completed_at = CASE
                    WHEN ? IN ('passed', 'skipped') THEN CURRENT_TIMESTAMP ELSE NULL END,
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ? AND step_key = ?
            """,
            (
                clean_status,
                _json(clean_response),
                _json(clean_evidence),
                clean_blocker if clean_status == "blocked" else None,
                clean_skip if clean_status == "skipped" else None,
                clean_status,
                session_id,
                step_key,
            ),
        )
        _refresh_session(conn, session_id)
        conn.commit()
        return _read_session(conn, session_id)


def record_setup_preparation(
    db_path: Path,
    session_id: str,
    *,
    expected_revision: int,
    plan: Mapping[str, Any],
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist a redacted server receipt and link it to adapter_setup."""
    if plan.get("schema_version") != "guided_setup_preparation_v1":
        raise ValueError("guided_setup_preparation_plan_schema_mismatch")
    plan_scope = plan.get("scope")
    if not isinstance(plan_scope, Mapping) or any(
        plan_scope.get(key) is not expected
        for key, expected in (
            ("read_only", True),
            ("secrets_read", False),
            ("credentials_probed", False),
            ("installations_attempted", False),
            ("terms_accepted", False),
        )
    ):
        raise ValueError("guided_setup_preparation_plan_scope_unsafe")
    if inventory.get("schema_version") != "machine_doctor_v1":
        raise ValueError("guided_setup_preparation_inventory_schema_mismatch")
    plan_hash = hashlib.sha256(_json(plan).encode("utf-8")).hexdigest()
    doctor_hash = hashlib.sha256(_json(inventory).encode("utf-8")).hexdigest()
    blockers = list((plan.get("summary") or {}).get("blockers") or [])
    ready = (plan.get("summary") or {}).get("ready") is True
    receipt_id = str(uuid.uuid4())
    receipt = {
        "id": receipt_id,
        "schema_version": "guided_setup_preparation_receipt_v1",
        "needs_hash": str(plan.get("needs_hash") or ""),
        "plan_hash": plan_hash,
        "doctor_hash": doctor_hash,
        "ready": ready,
        "blockers": blockers,
    }
    clean_receipt = _payload(receipt)
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = _session_row(conn, session_id)
        if str(session["scope"]) != "machine_onboarding":
            raise ValueError("guided_setup_preparation_scope_not_supported")
        if int(session["revision"]) != int(expected_revision):
            raise GuidedSetupConflict("guided_setup_revision_conflict")
        definition = next(
            row
            for row in setup_contract("machine_onboarding")["steps"]
            if row["key"] == "adapter_setup"
        )
        _require_dependencies(conn, session_id, definition)
        needs_row = conn.execute(
            """
            SELECT response_json FROM guided_setup_steps
            WHERE session_id = ? AND step_key = 'needs_profile' AND status = 'passed'
            """,
            (session_id,),
        ).fetchone()
        needs_payload = (
            json.loads(str(needs_row["response_json"])) if needs_row else {}
        )
        if receipt["needs_hash"] != needs_payload.get("assessment_hash"):
            raise ValueError("guided_setup_preparation_needs_hash_mismatch")
        step = conn.execute(
            """
            SELECT status FROM guided_setup_steps
            WHERE session_id = ? AND step_key = 'adapter_setup'
            """,
            (session_id,),
        ).fetchone()
        if step is None or step["status"] in _TERMINAL_STEP_STATUSES:
            raise GuidedSetupConflict("guided_setup_transition_not_allowed")
        conn.execute(
            """
            INSERT INTO guided_setup_preparation_receipts
                (id, session_id, step_key, schema_version, needs_hash,
                 plan_hash, doctor_hash, ready, blockers_json)
            VALUES (?, ?, 'adapter_setup', ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                session_id,
                receipt["schema_version"],
                receipt["needs_hash"],
                plan_hash,
                doctor_hash,
                int(ready),
                _json(blockers),
            ),
        )
        conn.execute(
            """
            UPDATE guided_setup_steps SET
                status = 'in_progress',
                response_json = ?,
                evidence_json = ?,
                blocker_code = NULL,
                skip_reason = NULL,
                started_at = CASE
                    WHEN started_at IS NULL THEN CURRENT_TIMESTAMP ELSE started_at END,
                completed_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ? AND step_key = 'adapter_setup'
            """,
            (
                _json({"preparation_receipt_ref": f"sha256:{plan_hash}"}),
                _json(clean_receipt),
                session_id,
            ),
        )
        _refresh_session(conn, session_id)
        conn.commit()
        return {
            "receipt": clean_receipt,
            "session": _read_session(conn, session_id),
        }


def get_project_commit_receipt(
    db_path: Path,
    session_id: str,
) -> dict[str, Any] | None:
    """Return the durable project commit receipt, if this session was saved."""
    with contextlib.closing(_connect(db_path)) as conn:
        _session_row(conn, session_id)
        row = conn.execute(
            """
            SELECT id, session_id, schema_version, proposal_hash,
                   project_target, result_json, created_at
            FROM guided_setup_project_commit_receipts
            WHERE session_id = ?
            """,
            (str(session_id),),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "session_id": str(row["session_id"]),
            "schema_version": str(row["schema_version"]),
            "proposal_hash": str(row["proposal_hash"]),
            "project_target": str(row["project_target"]),
            "result": json.loads(str(row["result_json"])),
            "created_at": row["created_at"],
        }


def record_project_preflight_receipt(
    db_path: Path,
    session_id: str,
    *,
    preflight: Mapping[str, Any],
    execution_plan: Mapping[str, Any],
    execution_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist one server-produced preflight receipt and its sealed artifacts."""
    from aiteam.guided_setup_project_preflight import (
        validate_project_preflight,
    )
    from aiteam.guided_setup_project_preflight_execution import (
        validate_project_preflight_execution_plan,
    )
    from aiteam.guided_setup_project_preflight_executor import (
        validate_project_preflight_execution_receipt,
    )

    validate_project_preflight(preflight)
    validate_project_preflight_execution_plan(execution_plan)
    validate_project_preflight_execution_receipt(execution_receipt)
    proposal_hash = str(preflight["inputs"]["proposal_hash"])
    plan_hash = str(execution_plan["plan_hash"])
    execution_hash = str(execution_receipt["receipt_hash"])
    if execution_plan["inputs"]["proposal_hash"] != proposal_hash:
        raise ValueError("guided_setup_preflight_persistence_proposal_mismatch")
    if execution_receipt["inputs"]["proposal_hash"] != proposal_hash:
        raise ValueError("guided_setup_preflight_persistence_receipt_mismatch")
    if execution_receipt["inputs"]["execution_plan_hash"] != plan_hash:
        raise ValueError("guided_setup_preflight_persistence_plan_mismatch")
    fixture_evidence = [
        dict(row) for row in execution_receipt["fixture_evidence"]
    ]
    fixture_hash = hashlib.sha256(
        _json(fixture_evidence).encode("utf-8")
    ).hexdigest()
    if preflight["inputs"]["fixture_evidence_hash"] != fixture_hash:
        raise ValueError("guided_setup_preflight_persistence_fixture_mismatch")

    artifacts = [dict(row) for row in execution_receipt["artifacts"]]
    evidence_by_ref = {
        str(row["receipt_ref"]): dict(row)
        for row in execution_receipt["fixture_evidence"]
    }
    artifact_refs = {str(row.get("ref") or "") for row in artifacts}
    if any(reference not in artifact_refs for reference in evidence_by_ref):
        raise ValueError("guided_setup_preflight_fixture_artifact_missing")
    clean_artifacts: list[dict[str, Any]] = []
    for artifact in artifacts:
        reference = str(artifact.get("ref") or "")
        content = artifact.get("content")
        if not _valid_sha256_reference(reference) or not isinstance(
            content, Mapping
        ):
            raise ValueError("guided_setup_preflight_artifact_invalid")
        clean_content = _artifact_payload(content)
        content_hash = hashlib.sha256(
            _json(clean_content).encode("utf-8")
        ).hexdigest()
        if reference != f"sha256:{content_hash}":
            raise ValueError("guided_setup_preflight_artifact_hash_mismatch")
        clean_artifacts.append({
            "reference": reference,
            "kind": str(artifact.get("kind") or ""),
            "content": clean_content,
            "content_hash": content_hash,
            "fixture_evidence": evidence_by_ref.get(reference),
        })

    fixture_refs = sorted(evidence_by_ref)
    status = str(preflight["summary"]["status"])
    durable = {
        "schema_version": "guided_setup_project_preflight_receipt_v1",
        "session_id": str(session_id),
        "proposal_hash": proposal_hash,
        "preflight_hash": str(preflight["preflight_hash"]),
        "execution_plan_hash": plan_hash,
        "execution_receipt_hash": execution_hash,
        "status": status,
        "fixture_evidence_refs": fixture_refs,
    }
    receipt_hash = hashlib.sha256(_json(durable).encode("utf-8")).hexdigest()
    receipt_id = str(uuid.uuid4())
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = _session_row(conn, session_id)
        if str(session["scope"]) != "project_setup":
            raise ValueError("guided_setup_project_scope_not_supported")
        try:
            for artifact in clean_artifacts:
                existing_artifact = conn.execute(
                    """
                    SELECT content_hash, kind, fixture_evidence_json
                    FROM guided_setup_project_preflight_artifacts
                    WHERE session_id = ? AND reference = ?
                    """,
                    (str(session_id), artifact["reference"]),
                ).fetchone()
                fixture_json = (
                    _json(artifact["fixture_evidence"])
                    if artifact["fixture_evidence"] is not None
                    else None
                )
                if existing_artifact is not None:
                    if (
                        str(existing_artifact["content_hash"])
                        != artifact["content_hash"]
                        or str(existing_artifact["kind"]) != artifact["kind"]
                        or existing_artifact["fixture_evidence_json"]
                        != fixture_json
                    ):
                        raise GuidedSetupConflict(
                            "guided_setup_preflight_artifact_conflict"
                        )
                    continue
                conn.execute(
                    """
                    INSERT INTO guided_setup_project_preflight_artifacts
                        (session_id, reference, schema_version, kind,
                         content_hash, content_json, fixture_evidence_json)
                    VALUES (?, ?, 'guided_setup_project_preflight_artifact_v1',
                            ?, ?, ?, ?)
                    """,
                    (
                        str(session_id),
                        artifact["reference"],
                        artifact["kind"],
                        artifact["content_hash"],
                        _json(artifact["content"]),
                        fixture_json,
                    ),
                )
            existing = conn.execute(
                """
                SELECT id FROM guided_setup_project_preflight_receipts
                WHERE session_id = ? AND receipt_hash = ?
                """,
                (str(session_id), receipt_hash),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO guided_setup_project_preflight_receipts
                        (id, session_id, schema_version, proposal_hash,
                         preflight_hash, execution_plan_hash,
                         execution_receipt_hash, status,
                         fixture_evidence_refs_json, preflight_json,
                         execution_receipt_json, receipt_hash)
                    VALUES (?, ?, 'guided_setup_project_preflight_receipt_v1',
                            ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt_id,
                        str(session_id),
                        proposal_hash,
                        preflight["preflight_hash"],
                        plan_hash,
                        execution_hash,
                        status,
                        _json(fixture_refs),
                        _json(dict(preflight)),
                        _json(dict(execution_receipt)),
                        receipt_hash,
                    ),
                )
            else:
                receipt_id = str(existing["id"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return _read_project_preflight_receipt(conn, receipt_id)


def get_latest_project_preflight_receipt(
    db_path: Path,
    session_id: str,
) -> dict[str, Any] | None:
    """Return the newest durable authorization attempt for this session."""
    with contextlib.closing(_connect(db_path)) as conn:
        _session_row(conn, session_id)
        row = conn.execute(
            """
            SELECT id FROM guided_setup_project_preflight_receipts
            WHERE session_id = ?
            ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
            (str(session_id),),
        ).fetchone()
        if row is None:
            return None
        return _read_project_preflight_receipt(conn, str(row["id"]))


def get_project_preflight_receipt_for_plan(
    db_path: Path,
    session_id: str,
    execution_plan_hash: str,
) -> dict[str, Any] | None:
    """Return a prior attempt so an old exact plan is never executed twice."""
    clean_hash = str(execution_plan_hash or "")
    if len(clean_hash) != 64:
        raise ValueError("guided_setup_execution_plan_hash_invalid")
    with contextlib.closing(_connect(db_path)) as conn:
        _session_row(conn, session_id)
        row = conn.execute(
            """
            SELECT id FROM guided_setup_project_preflight_receipts
            WHERE session_id = ? AND execution_plan_hash = ?
            ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
            (str(session_id), clean_hash),
        ).fetchone()
        if row is None:
            return None
        return _read_project_preflight_receipt(conn, str(row["id"]))


def resolve_project_fixture_evidence(
    db_path: Path,
    session_id: str,
    references: list[str],
) -> list[dict[str, Any]]:
    """Resolve session-confined evidence and verify its content address."""
    if len(references) != len(set(references)):
        raise ValueError("guided_setup_project_fixture_evidence_duplicate")
    if any(not _valid_sha256_reference(reference) for reference in references):
        raise ValueError("guided_setup_project_fixture_evidence_ref_invalid")
    with contextlib.closing(_connect(db_path)) as conn:
        _session_row(conn, session_id)
        evidence: list[dict[str, Any]] = []
        for reference in references:
            row = conn.execute(
                """
                SELECT content_hash, content_json, fixture_evidence_json
                FROM guided_setup_project_preflight_artifacts
                WHERE session_id = ? AND reference = ?
                """,
                (str(session_id), reference),
            ).fetchone()
            if row is None or row["fixture_evidence_json"] is None:
                raise ValueError(
                    "guided_setup_project_fixture_evidence_not_persisted"
                )
            try:
                content = json.loads(str(row["content_json"]))
                normalized = json.loads(
                    str(row["fixture_evidence_json"])
                )
            except (TypeError, ValueError) as exc:
                raise GuidedSetupConflict(
                    "guided_setup_project_fixture_evidence_corrupt"
                ) from exc
            if not isinstance(content, Mapping) or not isinstance(
                normalized, Mapping
            ):
                raise GuidedSetupConflict(
                    "guided_setup_project_fixture_evidence_corrupt"
                )
            content_hash = hashlib.sha256(
                _json(content).encode("utf-8")
            ).hexdigest()
            if (
                content_hash != str(row["content_hash"])
                or reference != f"sha256:{content_hash}"
            ):
                raise GuidedSetupConflict(
                    "guided_setup_project_fixture_evidence_corrupt"
                )
            if normalized.get("receipt_ref") != reference:
                raise GuidedSetupConflict(
                    "guided_setup_project_fixture_evidence_corrupt"
                )
            evidence.append(normalized)
        return evidence


def record_project_commit_receipt(
    db_path: Path,
    session_id: str,
    *,
    proposal_hash: str,
    project_target: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal one successful project materialization per guided setup session."""
    clean_hash = str(proposal_hash or "").strip()
    clean_target = str(project_target or "").strip()
    if len(clean_hash) != 64 or not clean_target:
        raise ValueError("guided_setup_project_commit_receipt_invalid")
    clean_result = _payload(result)
    with contextlib.closing(_connect(db_path)) as conn:
        _session_row(conn, session_id)
        existing = conn.execute(
            """
            SELECT proposal_hash FROM guided_setup_project_commit_receipts
            WHERE session_id = ?
            """,
            (str(session_id),),
        ).fetchone()
        if existing is not None:
            if str(existing["proposal_hash"]) != clean_hash:
                raise GuidedSetupConflict(
                    "guided_setup_project_already_committed"
                )
            receipt = get_project_commit_receipt(db_path, session_id)
            assert receipt is not None
            return receipt
        receipt_id = str(uuid.uuid4())
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                INSERT INTO guided_setup_project_commit_receipts
                    (id, session_id, schema_version, proposal_hash,
                     project_target, result_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    str(session_id),
                    "guided_setup_project_commit_receipt_v1",
                    clean_hash,
                    clean_target,
                    _json(clean_result),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    receipt = get_project_commit_receipt(db_path, session_id)
    assert receipt is not None
    return receipt


def reset_setup(
    db_path: Path,
    session_id: str,
    *,
    expected_revision: int,
    confirm: bool,
) -> dict[str, Any]:
    if confirm is not True:
        raise ValueError("guided_setup_reset_confirmation_required")
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = _session_row(conn, session_id)
        if int(session["revision"]) != int(expected_revision):
            raise GuidedSetupConflict("guided_setup_revision_conflict")
        first = setup_contract(str(session["scope"]))["steps"][0]["key"]
        conn.execute(
            """
            UPDATE guided_setup_steps SET
                status = 'not_started', response_json = '{}', evidence_json = '{}',
                blocker_code = NULL, skip_reason = NULL, started_at = NULL,
                completed_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
            """,
            (session_id,),
        )
        conn.execute(
            """
            UPDATE guided_setup_sessions SET
                status = 'in_progress', current_step = ?, revision = revision + 1,
                completed_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (first, session_id),
        )
        conn.commit()
        return _read_session(conn, session_id)


def _refresh_session(conn: sqlite3.Connection, session_id: str) -> None:
    steps = conn.execute(
        "SELECT step_key, status FROM guided_setup_steps WHERE session_id = ? ORDER BY ordinal",
        (session_id,),
    ).fetchall()
    blocked = next((row for row in steps if row["status"] == "blocked"), None)
    open_step = next(
        (row for row in steps if row["status"] not in _TERMINAL_STEP_STATUSES),
        None,
    )
    if blocked is not None:
        status, current, completed = "blocked", blocked["step_key"], None
    elif open_step is None:
        status, current, completed = "passed", None, "CURRENT_TIMESTAMP"
    else:
        status, current, completed = "in_progress", open_step["step_key"], None
    conn.execute(
        f"""
        UPDATE guided_setup_sessions SET
            status = ?, current_step = ?, revision = revision + 1,
            completed_at = {completed or 'NULL'}, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, current, session_id),
    )


def _require_dependencies(
    conn: sqlite3.Connection,
    session_id: str,
    definition: Mapping[str, Any],
) -> None:
    for dependency in definition["depends_on"]:
        row = conn.execute(
            "SELECT status FROM guided_setup_steps WHERE session_id = ? AND step_key = ?",
            (session_id, dependency),
        ).fetchone()
        if row is None or row["status"] not in _TERMINAL_STEP_STATUSES:
            raise GuidedSetupConflict("guided_setup_dependency_not_satisfied")


def _read_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    session = _session_row(conn, session_id)
    contract = setup_contract(str(session["scope"]))
    dependencies = {row["key"]: row["depends_on"] for row in contract["steps"]}
    steps = conn.execute(
        "SELECT * FROM guided_setup_steps WHERE session_id = ? ORDER BY ordinal",
        (session_id,),
    ).fetchall()
    return {
        "id": str(session["id"]),
        "schema_version": str(session["schema_version"]),
        "scope": str(session["scope"]),
        "subject_key": str(session["subject_key"]),
        "status": str(session["status"]),
        "current_step": session["current_step"],
        "revision": int(session["revision"]),
        "metadata": json.loads(str(session["metadata_json"])),
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "completed_at": session["completed_at"],
        "steps": [
            {
                "key": str(row["step_key"]),
                "ordinal": int(row["ordinal"]),
                "required": bool(row["required"]),
                "depends_on": dependencies[str(row["step_key"])],
                "status": str(row["status"]),
                "response": json.loads(str(row["response_json"])),
                "evidence": json.loads(str(row["evidence_json"])),
                "blocker_code": row["blocker_code"],
                "skip_reason": row["skip_reason"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "updated_at": row["updated_at"],
            }
            for row in steps
        ],
    }


def _session_row(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM guided_setup_sessions WHERE id = ?",
        (str(session_id),),
    ).fetchone()
    if row is None:
        raise KeyError("guided_setup_session_not_found")
    return row


def _read_project_preflight_receipt(
    conn: sqlite3.Connection,
    receipt_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT * FROM guided_setup_project_preflight_receipts WHERE id = ?
        """,
        (receipt_id,),
    ).fetchone()
    if row is None:
        raise KeyError("guided_setup_project_preflight_receipt_not_found")
    try:
        fixture_references = json.loads(
            str(row["fixture_evidence_refs_json"])
        )
        preflight = json.loads(str(row["preflight_json"]))
        execution_receipt = json.loads(
            str(row["execution_receipt_json"])
        )
    except (TypeError, ValueError) as exc:
        raise GuidedSetupConflict(
            "guided_setup_project_preflight_receipt_corrupt"
        ) from exc
    receipt = {
        "id": str(row["id"]),
        "session_id": str(row["session_id"]),
        "schema_version": str(row["schema_version"]),
        "proposal_hash": str(row["proposal_hash"]),
        "preflight_hash": str(row["preflight_hash"]),
        "execution_plan_hash": str(row["execution_plan_hash"]),
        "execution_receipt_hash": str(row["execution_receipt_hash"]),
        "status": str(row["status"]),
        "fixture_evidence_refs": fixture_references,
        "preflight": preflight,
        "execution_receipt": execution_receipt,
        "receipt_hash": str(row["receipt_hash"]),
        "created_at": row["created_at"],
    }
    durable = {
        "schema_version": receipt["schema_version"],
        "session_id": receipt["session_id"],
        "proposal_hash": receipt["proposal_hash"],
        "preflight_hash": receipt["preflight_hash"],
        "execution_plan_hash": receipt["execution_plan_hash"],
        "execution_receipt_hash": receipt["execution_receipt_hash"],
        "status": receipt["status"],
        "fixture_evidence_refs": receipt["fixture_evidence_refs"],
    }
    if hashlib.sha256(_json(durable).encode("utf-8")).hexdigest() != receipt[
        "receipt_hash"
    ]:
        raise GuidedSetupConflict(
            "guided_setup_project_preflight_receipt_corrupt"
        )
    from aiteam.guided_setup_project_preflight import (
        validate_project_preflight,
    )
    from aiteam.guided_setup_project_preflight_executor import (
        validate_project_preflight_execution_receipt,
    )

    try:
        validate_project_preflight(receipt["preflight"])
        validate_project_preflight_execution_receipt(
            receipt["execution_receipt"]
        )
    except (TypeError, ValueError) as exc:
        raise GuidedSetupConflict(
            "guided_setup_project_preflight_receipt_corrupt"
        ) from exc
    if (
        receipt["preflight"]["preflight_hash"] != receipt["preflight_hash"]
        or receipt["execution_receipt"]["receipt_hash"]
        != receipt["execution_receipt_hash"]
    ):
        raise GuidedSetupConflict(
            "guided_setup_project_preflight_receipt_corrupt"
        )
    return receipt


def _valid_sha256_reference(value: str) -> bool:
    prefix, separator, digest = str(value or "").partition(":")
    return (
        prefix == "sha256"
        and separator == ":"
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )


def _scope(value: str) -> str:
    clean = str(value or "").strip()
    if clean not in SCOPES:
        raise ValueError("guided_setup_scope_not_allowed")
    return clean


def _subject(value: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 255 or any(ord(char) < 32 for char in clean):
        raise ValueError("guided_setup_subject_key_invalid")
    return clean


def _payload(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    _reject_secret_keys(payload)
    try:
        encoded = _json(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("guided_setup_payload_not_json") from exc
    if len(encoded.encode("utf-8")) > 64_000:
        raise ValueError("guided_setup_payload_too_large")
    return payload


def _artifact_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    _reject_secret_keys(payload)
    try:
        encoded = _json(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("guided_setup_payload_not_json") from exc
    if len(encoded.encode("utf-8")) > 1_000_000:
        raise ValueError("guided_setup_preflight_artifact_too_large")
    return payload


def _reject_secret_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            forbidden_suffix = normalized.endswith(
                ("_api_key", "_password", "_access_token", "_refresh_token")
            )
            if normalized in _FORBIDDEN_SECRET_KEYS or forbidden_suffix:
                raise ValueError("guided_setup_secret_value_forbidden")
            _reject_secret_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_secret_keys(nested)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA_SQL)
    return conn
