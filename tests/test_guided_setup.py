from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aiteam.db.guided_setup import (
    GuidedSetupConflict,
    create_or_resume_setup,
    get_project_commit_receipt,
    get_setup,
    record_project_commit_receipt,
    reset_setup,
    setup_contract,
    transition_setup_step,
)
from aiteam.db.migration import SCHEMA_PATH
from aiteam.guided_setup_needs import build_needs_submission


def _database(tmp_path: Path) -> Path:
    db = tmp_path / "aiteam.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return db


def _advance(
    db: Path,
    session: dict,
    step_key: str,
    *,
    response: dict | None = None,
) -> dict:
    session = transition_setup_step(
        db,
        session["id"],
        step_key,
        status="in_progress",
        expected_revision=session["revision"],
    )
    return transition_setup_step(
        db,
        session["id"],
        step_key,
        status="passed",
        expected_revision=session["revision"],
        response=response or {},
        evidence={"validated": True},
    )


def _project_needs() -> dict:
    return build_needs_submission(
        "project_setup",
        {
            "goal": "Crear una aplicación web",
            "objective_kind": "software",
            "languages": ["TypeScript"],
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


def test_contract_is_versioned_ordered_and_scope_specific() -> None:
    machine = setup_contract("machine_onboarding")
    project = setup_contract("project_setup")

    assert machine["schema_version"] == "guided_setup_v1"
    assert [step["ordinal"] for step in machine["steps"]] == list(range(6))
    assert machine["steps"][1]["depends_on"] == ["welcome"]
    assert len(project["steps"]) == 7
    assert project["steps"][2]["required"] is False


def test_global_and_project_schema_keep_guided_tables_in_parity(
    tmp_path: Path,
) -> None:
    project_db = _database(tmp_path / "project")
    global_db = tmp_path / "user-config" / "guided_setup.db"
    create_or_resume_setup(
        global_db,
        scope="machine_onboarding",
        subject_key="machine",
    )

    def columns(db: Path, table: str) -> list[tuple]:
        with sqlite3.connect(db) as conn:
            return [
                tuple(row[1:6])
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            ]

    for table in (
        "guided_setup_sessions",
        "guided_setup_steps",
        "guided_setup_preparation_receipts",
        "guided_setup_project_commit_receipts",
        "guided_setup_project_preflight_artifacts",
        "guided_setup_project_preflight_receipts",
    ):
        assert columns(global_db, table) == columns(project_db, table)


def test_project_commit_receipt_is_durable_idempotent_and_single_use(
    tmp_path: Path,
) -> None:
    db = tmp_path / "guided.db"
    session = create_or_resume_setup(
        db,
        scope="project_setup",
        subject_key="project",
    )
    proposal_hash = "a" * 64
    result = {
        "workspace": "C:/projects/Portal",
        "database": "C:/projects/Portal/.aiteam/aiteam.db",
    }

    first = record_project_commit_receipt(
        db,
        session["id"],
        proposal_hash=proposal_hash,
        project_target=result["workspace"],
        result=result,
    )
    replay = record_project_commit_receipt(
        db,
        session["id"],
        proposal_hash=proposal_hash,
        project_target=result["workspace"],
        result=result,
    )

    assert replay == first
    assert get_project_commit_receipt(db, session["id"]) == first
    with pytest.raises(GuidedSetupConflict, match="already_committed"):
        record_project_commit_receipt(
            db,
            session["id"],
            proposal_hash="b" * 64,
            project_target="C:/projects/Other",
            result={"workspace": "C:/projects/Other"},
        )


def test_create_is_idempotent_and_resume_reads_sqlite(tmp_path: Path) -> None:
    db = _database(tmp_path)
    first = create_or_resume_setup(
        db,
        scope="machine_onboarding",
        subject_key="machine",
        metadata={"entrypoint": "first_run"},
    )
    second = create_or_resume_setup(
        db,
        scope="machine_onboarding",
        subject_key="machine",
        metadata={"entrypoint": "ignored_on_resume"},
    )

    assert first["id"] == second["id"]
    assert second["metadata"] == {"entrypoint": "first_run"}
    assert get_setup(db, first["id"]) == second
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM guided_setup_sessions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM guided_setup_steps").fetchone()[0] == 6


def test_dependencies_revision_and_required_skip_fail_closed(tmp_path: Path) -> None:
    db = _database(tmp_path)
    session = create_or_resume_setup(
        db,
        scope="machine_onboarding",
        subject_key="machine",
    )

    with pytest.raises(GuidedSetupConflict, match="dependency_not_satisfied"):
        transition_setup_step(
            db,
            session["id"],
            "projects_root",
            status="in_progress",
            expected_revision=session["revision"],
        )
    with pytest.raises(ValueError, match="required_step_cannot_skip"):
        transition_setup_step(
            db,
            session["id"],
            "welcome",
            status="skipped",
            expected_revision=session["revision"],
            skip_reason="not needed",
        )
    started = transition_setup_step(
        db,
        session["id"],
        "welcome",
        status="in_progress",
        expected_revision=session["revision"],
    )
    with pytest.raises(GuidedSetupConflict, match="revision_conflict"):
        transition_setup_step(
            db,
            session["id"],
            "welcome",
            status="passed",
            expected_revision=session["revision"],
        )
    assert started["revision"] == session["revision"] + 1


def test_optional_skip_block_resume_and_full_completion(tmp_path: Path) -> None:
    db = _database(tmp_path)
    session = create_or_resume_setup(
        db,
        scope="project_setup",
        subject_key="project:fixture",
    )
    session = _advance(
        db,
        session,
        "project_identity",
        response={"mode": "create", "name": "Fixture", "path": ""},
    )
    session = _advance(
        db,
        session,
        "objective_profile",
        response=_project_needs(),
    )
    session = transition_setup_step(
        db,
        session["id"],
        "ecosystem_detection",
        status="skipped",
        expected_revision=session["revision"],
        skip_reason="non_programmatic_project",
    )
    session = transition_setup_step(
        db,
        session["id"],
        "team_profile",
        status="blocked",
        expected_revision=session["revision"],
        blocker_code="lead_adapter_missing",
    )
    assert session["status"] == "blocked"
    assert session["current_step"] == "team_profile"
    session = transition_setup_step(
        db,
        session["id"],
        "team_profile",
        status="in_progress",
        expected_revision=session["revision"],
    )
    session = transition_setup_step(
        db,
        session["id"],
        "team_profile",
        status="passed",
        expected_revision=session["revision"],
    )
    for step in ("lead_selection", "project_preflight", "project_review"):
        session = _advance(db, session, step)

    assert session["status"] == "passed"
    assert session["current_step"] is None
    assert session["completed_at"] is not None


def test_reset_requires_confirmation_and_clears_progress(tmp_path: Path) -> None:
    db = _database(tmp_path)
    session = create_or_resume_setup(
        db,
        scope="installation_repair",
        subject_key="machine",
    )
    session = _advance(db, session, "diagnosis", response={"code": "cli_drift"})
    with pytest.raises(ValueError, match="confirmation_required"):
        reset_setup(
            db,
            session["id"],
            expected_revision=session["revision"],
            confirm=False,
        )

    reset = reset_setup(
        db,
        session["id"],
        expected_revision=session["revision"],
        confirm=True,
    )
    assert reset["current_step"] == "diagnosis"
    assert {step["status"] for step in reset["steps"]} == {"not_started"}
    assert all(step["response"] == {} for step in reset["steps"])


def test_payload_rejects_secret_like_keys(tmp_path: Path) -> None:
    db = _database(tmp_path)
    session = create_or_resume_setup(
        db,
        scope="machine_onboarding",
        subject_key="machine",
    )

    with pytest.raises(ValueError, match="secret_value_forbidden"):
        transition_setup_step(
            db,
            session["id"],
            "welcome",
            status="in_progress",
            expected_revision=session["revision"],
            response={"api_key": "must-not-persist"},
        )


def test_in_progress_draft_accepts_secret_reference_and_token_budget(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path)
    session = create_or_resume_setup(
        db,
        scope="machine_onboarding",
        subject_key="machine",
    )
    session = transition_setup_step(
        db,
        session["id"],
        "welcome",
        status="in_progress",
        expected_revision=session["revision"],
        response={"secret_ref": "secret:google-free:default", "token_budget": 5000},
    )
    updated = transition_setup_step(
        db,
        session["id"],
        "welcome",
        status="in_progress",
        expected_revision=session["revision"],
        response={"secret_ref": "secret:google-free:default", "token_budget": 9000},
    )

    assert updated["steps"][0]["response"]["token_budget"] == 9000
    assert updated["revision"] == session["revision"] + 1
