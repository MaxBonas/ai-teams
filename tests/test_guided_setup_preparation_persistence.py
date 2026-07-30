from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aiteam.db.guided_setup import (
    create_or_resume_setup,
    record_setup_preparation,
    transition_setup_step,
)
from aiteam.guided_setup_needs import build_needs_submission
from aiteam.guided_setup_preparation import build_preparation_plan


def _needs() -> dict[str, Any]:
    return build_needs_submission(
        "machine_onboarding",
        {
            "goal": "Crear una aplicación React",
            "objective_kind": "software",
            "languages": ["React", "TypeScript"],
            "data_sensitivity": "internal",
            "budget_priority": "balanced",
            "subscriptions": ["codex"],
            "api_access": "not_willing",
            "local_models": "not_wanted",
            "autonomy": "supervised",
            "criticality": "medium",
            "team_preference": "solo_lead",
            "external_tools": "optional",
        },
    )


def _inventory() -> dict[str, Any]:
    return {
        "schema_version": "machine_doctor_v1",
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
        },
        "runtimes": [
            {
                "id": "python",
                "requirement": "required",
                "ready": True,
                "installed": True,
                "version": "3.12.10",
                "minimum_version": "3.10",
            }
        ],
        "adapters": [
            {
                "id": "codex_subscription",
                "cli": {
                    "installed": True,
                    "version": "codex-cli 0.146.0-alpha.6",
                },
                "authentication_status": "authenticated",
                "health_status": "ok",
            }
        ],
    }


def _session_at_adapter_setup(db: Path) -> dict[str, Any]:
    session = create_or_resume_setup(
        db,
        scope="machine_onboarding",
        subject_key="machine",
    )
    for step_key, response in (
        ("welcome", {}),
        ("projects_root", {"path": "C:/fixture"}),
        ("needs_profile", _needs()),
    ):
        session = transition_setup_step(
            db,
            session["id"],
            step_key,
            status="in_progress",
            expected_revision=session["revision"],
        )
        session = transition_setup_step(
            db,
            session["id"],
            step_key,
            status="passed",
            expected_revision=session["revision"],
            response=response,
        )
    return session


def test_server_receipt_is_compact_redacted_and_revision_bound(
    tmp_path: Path,
) -> None:
    db = tmp_path / "aiteam.db"
    session = _session_at_adapter_setup(db)
    inventory = _inventory()
    plan = build_preparation_plan(_needs(), inventory)

    persisted = record_setup_preparation(
        db,
        session["id"],
        expected_revision=session["revision"],
        plan=plan,
        inventory=inventory,
    )
    evidence = next(
        row
        for row in persisted["session"]["steps"]
        if row["key"] == "adapter_setup"
    )["evidence"]

    assert persisted["session"]["revision"] == session["revision"] + 1
    assert evidence["ready"] is False
    assert len(evidence["plan_hash"]) == 64
    assert "runtimes" not in evidence
    assert "adapters" not in evidence
    assert "C:/fixture" not in json.dumps(evidence)
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM guided_setup_preparation_receipts"
        ).fetchone()[0] == 1


def test_unready_receipt_cannot_complete_adapter_setup(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    session = _session_at_adapter_setup(db)
    inventory = _inventory()
    persisted = record_setup_preparation(
        db,
        session["id"],
        expected_revision=session["revision"],
        plan=build_preparation_plan(_needs(), inventory),
        inventory=inventory,
    )

    with pytest.raises(ValueError, match="ready_receipt_required"):
        transition_setup_step(
            db,
            session["id"],
            "adapter_setup",
            status="passed",
            expected_revision=persisted["session"]["revision"],
            evidence={"ready": True},
        )


def test_ready_server_receipt_is_preserved_on_completion(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    session = _session_at_adapter_setup(db)
    inventory = _inventory()
    plan = build_preparation_plan(
        _needs(),
        inventory,
        provider_evidence={
            "codex_subscription": {
                "catalog": "passed",
                "contract": "passed",
            }
        },
    )
    persisted = record_setup_preparation(
        db,
        session["id"],
        expected_revision=session["revision"],
        plan=plan,
        inventory=inventory,
    )
    completed = transition_setup_step(
        db,
        session["id"],
        "adapter_setup",
        status="passed",
        expected_revision=persisted["session"]["revision"],
        evidence={"ready": False, "forged": True},
    )
    step = next(
        row for row in completed["steps"] if row["key"] == "adapter_setup"
    )

    assert step["status"] == "passed"
    assert step["evidence"]["ready"] is True
    assert "forged" not in step["evidence"]
