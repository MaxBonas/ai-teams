from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aiteam.db.provider_change_workflows import (
    ProviderChangeConflictError,
    evidence_is_invalidated,
    list_active_provider_change_invalidations,
    reconcile_provider_change_cases,
    transition_provider_change_case,
)
from aiteam.db.provider_changes import (
    list_pending_provider_triggers,
    list_provider_events,
    reconcile_provider_snapshot,
)
from aiteam.model_evaluation_coverage import (
    audit_model_evaluation_coverage,
)
from aiteam.provider_change_detection import build_provider_snapshot
from aiteam.provider_change_intelligence import (
    build_provider_change_inventory,
)

NOW = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "aiteam" / "db" / "schema.sql"
)


def _component() -> dict:
    return next(
        row
        for row in build_provider_change_inventory()["components"]
        if row["surface"] == "model_catalog"
        and row["profile_id"] == "codex_subscription"
    )


def _model(context: int) -> dict:
    return {
        "id": "gpt-5.6-sol",
        "aliases": [],
        "context": context,
        "tools": True,
        "structured_output": True,
        "price": "subscription",
        "quota": "subscription",
        "lifecycle": "active",
    }


def _snapshot(context: int, observed_at: datetime) -> dict:
    return build_provider_snapshot(
        _component(),
        {
            "status": "observed",
            "installed_version": "0.146.0-alpha.6",
            "latest_known_version": "0.146.0-alpha.6",
            "compatibility": {
                "installed": "compatible",
                "latest_known": "compatible",
            },
            "dimensions": {"model_id": [_model(context)]},
        },
        observed_at=observed_at.isoformat(),
    )


def _case(db_path: Path) -> dict:
    reconcile_provider_snapshot(db_path, _snapshot(1000, NOW))
    reconcile_provider_snapshot(
        db_path,
        _snapshot(2000, NOW + timedelta(hours=1)),
    )
    created = reconcile_provider_change_cases(
        db_path,
        now=NOW + timedelta(hours=2),
    )
    assert len(created) == 1
    return created[0]


def _classify_payload(*, recalibration: bool = True) -> dict:
    return {
        "impact_level": "material",
        "requires_recalibration": recalibration,
        "rationale": "El contexto cambió y la evidencia exacta debe repetirse.",
        "impact": {
            "profile_ids": ["codex_subscription"],
            "model_ids": ["gpt-5.6-sol"],
            "roles": ["lead"],
            "all_models": False,
            "all_roles": False,
            "new_selection_policy": "block_affected",
            "existing_assignment_policy": "preserve_and_notify",
        },
    }


def _confirm_classify_approve(db_path: Path, case: dict) -> dict:
    case = transition_provider_change_case(
        db_path,
        case["id"],
        action="confirm",
        expected_revision=case["revision"],
        actor="owner",
        now=NOW + timedelta(hours=3),
    )
    case = transition_provider_change_case(
        db_path,
        case["id"],
        action="classify",
        expected_revision=case["revision"],
        actor="owner",
        payload=_classify_payload(),
        now=NOW + timedelta(hours=4),
    )
    return transition_provider_change_case(
        db_path,
        case["id"],
        action="approve",
        expected_revision=case["revision"],
        actor="owner",
        payload={"note": "Aprobado para remediación manual."},
        now=NOW + timedelta(hours=5),
    )


def test_schema_contract_and_reconcile_are_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "guided_setup.db"
    case = _case(db_path)
    assert reconcile_provider_change_cases(db_path, now=NOW) == []

    assert case["status"] == "awaiting_confirmation"
    assert case["revision"] == 1
    assert case["recommendation"] == {
        "decision": "blocked",
        "next_step": "confirm_and_classify",
        "automatic_update_allowed": False,
        "routing_change_allowed": False,
    }
    assert all(
        command["execution"] in {"manual_only", "guided_in_product"}
        for command in case["guided_commands"]
    )
    assert case["history"][0]["action"] == "observe"

    schema_db = tmp_path / "schema.db"
    with sqlite3.connect(schema_db) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "provider_change_cases",
        "provider_change_case_history",
        "provider_change_evidence_invalidations",
    } <= tables


def test_revision_conflict_and_secret_fields_fail_closed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "guided_setup.db"
    case = _case(db_path)
    updated = transition_provider_change_case(
        db_path,
        case["id"],
        action="confirm",
        expected_revision=1,
        actor="owner",
        now=NOW + timedelta(hours=3),
    )

    with pytest.raises(ProviderChangeConflictError, match="stale"):
        transition_provider_change_case(
            db_path,
            case["id"],
            action="classify",
            expected_revision=1,
            actor="owner",
            payload=_classify_payload(),
        )
    with pytest.raises(ValueError, match="secret"):
        transition_provider_change_case(
            db_path,
            case["id"],
            action="classify",
            expected_revision=updated["revision"],
            actor="owner",
            payload={
                **_classify_payload(),
                "nested": {"api_key": "never-store"},
            },
        )


def test_happy_path_invalidates_exact_evidence_then_restores(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "guided_setup.db"
    case = _confirm_classify_approve(db_path, _case(db_path))
    invalidations = list_active_provider_change_invalidations(db_path)

    assert case["status"] == "approved"
    assert len(invalidations) == 1
    assert evidence_is_invalidated(
        invalidations,
        profile_id="codex_subscription",
        model_id="gpt-5.6-sol",
        role="lead",
    )
    assert not evidence_is_invalidated(
        invalidations,
        profile_id="codex_subscription",
        model_id="gpt-5.6-sol",
        role="reviewer",
    )

    case = transition_provider_change_case(
        db_path,
        case["id"],
        action="record_application",
        expected_revision=case["revision"],
        actor="owner",
        payload={
            "kind": "catalog_updated",
            "summary": "Se actualizó el contrato de contexto.",
            "evidence_receipts": ["receipts/application.json"],
        },
        now=NOW + timedelta(hours=6),
    )
    assert case["application"]["executed_by_workflow"] is False
    case = transition_provider_change_case(
        db_path,
        case["id"],
        action="record_validation",
        expected_revision=case["revision"],
        actor="owner",
        payload={
            "result": "passed",
            "doctor": "passed",
            "probe": "passed",
            "summary": "Doctor y probe exacto verdes.",
            "evidence_receipts": ["receipts/validation.json"],
        },
        now=NOW + timedelta(hours=7),
    )
    assert case["status"] == "awaiting_recalibration"
    case = transition_provider_change_case(
        db_path,
        case["id"],
        action="record_recalibration",
        expected_revision=case["revision"],
        actor="owner",
        payload={
            "result": "passed",
            "summary": "Lead recalibrado sobre dos familias.",
            "evidence_receipts": ["receipts/calibration.json"],
        },
        now=NOW + timedelta(hours=8),
    )
    assert case["status"] == "ready_to_accept"
    case = transition_provider_change_case(
        db_path,
        case["id"],
        action="accept",
        expected_revision=case["revision"],
        actor="owner",
        now=NOW + timedelta(hours=9),
    )

    assert case["status"] == "accepted"
    assert len(case["history"]) == 8
    assert list_active_provider_change_invalidations(db_path) == []
    assert list_pending_provider_triggers(db_path) == []
    assert list_provider_events(db_path, statuses={"resolved"})


def test_failed_validation_can_retry_and_revert_is_recoverable(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "guided_setup.db"
    case = _confirm_classify_approve(db_path, _case(db_path))
    case = transition_provider_change_case(
        db_path,
        case["id"],
        action="record_application",
        expected_revision=case["revision"],
        actor="owner",
        payload={
            "kind": "adapter_updated",
            "summary": "Adapter actualizado manualmente.",
        },
    )
    case = transition_provider_change_case(
        db_path,
        case["id"],
        action="record_validation",
        expected_revision=case["revision"],
        actor="owner",
        payload={
            "result": "failed",
            "doctor": "failed",
            "probe": "not_applicable",
            "summary": "Doctor detectó incompatibilidad.",
        },
    )
    assert case["status"] == "validation_failed"
    case = transition_provider_change_case(
        db_path,
        case["id"],
        action="revert",
        expected_revision=case["revision"],
        actor="owner",
        payload={
            "reason": "Se restauró el adapter anterior.",
            "evidence_receipts": ["receipts/rollback.json"],
        },
    )

    assert case["status"] == "reverted"
    assert list_active_provider_change_invalidations(db_path) == []
    assert len(list_pending_provider_triggers(db_path)) == 1
    reopened = transition_provider_change_case(
        db_path,
        case["id"],
        action="reopen",
        expected_revision=case["revision"],
        actor="owner",
    )
    assert reopened["status"] == "awaiting_classification"
    assert reopened["classification"] is None


def test_reject_dismisses_trigger_without_applying_invalidation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "guided_setup.db"
    case = _case(db_path)

    rejected = transition_provider_change_case(
        db_path,
        case["id"],
        action="reject",
        expected_revision=case["revision"],
        actor="owner",
        payload={"reason": "Cambio esperado y sin impacto operativo."},
    )

    assert rejected["status"] == "rejected"
    assert list_pending_provider_triggers(db_path) == []
    assert list_active_provider_change_invalidations(db_path) == []


def test_coverage_overlay_only_stales_exact_role() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.146.0-alpha.6",
            "antigravity_subscription": "1.1.5",
            "gemini_api_free": "api:google:v1beta",
        },
        provider_change_invalidations=[
            {
                "id": "invalidation",
                "case_id": "case",
                "profile_id": "codex_subscription",
                "model_id": "gpt-5.6-sol",
                "canonical_role": "lead",
                "reason": "provider_change:fingerprint",
                "status": "active",
            }
        ],
    )
    sol = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "codex_subscription"
        and row["model"] == "gpt-5.6-sol"
    )
    roles = {row["role"]: row for row in sol["roles"]}

    assert roles["lead"]["status"] == "partial"
    assert "provider_change_confirmed" in roles["lead"]["stale_reasons"]
    assert roles["architect"]["status"] == "calibrated"
