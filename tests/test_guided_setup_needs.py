from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aiteam.db.guided_setup import (
    create_or_resume_setup,
    transition_setup_step,
)
from aiteam.db.migration import SCHEMA_PATH
from aiteam.guided_setup_needs import (
    build_needs_submission,
    needs_questionnaire,
    validate_needs_submission,
)


def _answers(**overrides: object) -> dict[str, object]:
    answers: dict[str, object] = {
        "goal": "Crear una aplicación React para gestionar clientes",
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
    }
    answers.update(overrides)
    return answers


def _database(tmp_path: Path) -> Path:
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return db


def _advance(db: Path, session: dict, step_key: str, response: dict) -> dict:
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
        response=response,
    )


def test_questionnaire_is_adaptive_and_explains_recommendations() -> None:
    research = needs_questionnaire(
        "project_setup",
        {"objective_kind": "research"},
    )
    unknown = needs_questionnaire(
        "project_setup",
        {"objective_kind": "unknown"},
    )

    research_by_id = {row["id"]: row for row in research["questions"]}
    unknown_by_id = {row["id"]: row for row in unknown["questions"]}
    assert research_by_id["languages"]["visible"] is False
    assert unknown_by_id["languages"]["visible"] is True
    assert research_by_id["local_models"]["recommended"] == "not_wanted"
    assert all(row["help"] for row in research["questions"])
    assert all(row["recommendation_reason"] for row in research["questions"])


def test_complete_submission_is_deterministic_and_validates() -> None:
    first = build_needs_submission("project_setup", _answers())
    second = build_needs_submission("project_setup", _answers())

    assert first == second
    assert first["assessment"]["complete"] is True
    assert len(first["assessment_hash"]) == 64
    assert validate_needs_submission(first, scope="project_setup") == first


def test_unknown_objective_is_suggested_but_requires_confirmation() -> None:
    answers = _answers(
        goal="Preparar formularios y un estudio de necesidades para una empresa",
        objective_kind="unknown",
        languages=["unknown"],
    )
    submission = build_needs_submission("project_setup", answers)

    objective = submission["assessment"]["objective"]
    assert objective["kind"] == "research"
    assert objective["source"] == "deterministic_suggestion"
    assert objective["requires_confirmation"] is True
    assert "objective_kind" in submission["assessment"]["unknown_answers"]


def test_profile_and_channels_respect_risk_and_explicit_local_opt_in() -> None:
    normal = build_needs_submission("project_setup", _answers())
    critical = build_needs_submission(
        "project_setup",
        _answers(criticality="critical"),
    )
    local = build_needs_submission(
        "project_setup",
        _answers(local_models="willing"),
    )

    assert normal["assessment"]["recommended_run_profile"] == "solo_lead"
    assert critical["assessment"]["recommended_run_profile"] == "lead_quorum"
    assert "local" not in {
        row["kind"] for row in normal["assessment"]["channel_strategy"]
    }
    assert "local" in {
        row["kind"] for row in local["assessment"]["channel_strategy"]
    }


def test_incomplete_invalid_and_conflicting_answers_fail_closed() -> None:
    partial = build_needs_submission(
        "machine_onboarding",
        {"goal": "Preparar mi máquina"},
    )
    assert partial["assessment"]["complete"] is False
    with pytest.raises(ValueError, match="guided_setup_needs_incomplete"):
        validate_needs_submission(partial, scope="machine_onboarding")
    with pytest.raises(ValueError, match="subscriptions_conflict"):
        build_needs_submission(
            "machine_onboarding",
            _answers(subscriptions=["none", "codex"]),
        )
    with pytest.raises(ValueError, match="languages_invalid"):
        build_needs_submission(
            "machine_onboarding",
            _answers(languages=["Type Script"]),
        )


def test_tampered_assessment_or_scope_is_rejected() -> None:
    submission = build_needs_submission("project_setup", _answers())
    submission["assessment"]["recommended_run_profile"] = "full_team"
    with pytest.raises(ValueError, match="assessment_mismatch"):
        validate_needs_submission(submission, scope="project_setup")

    fresh = build_needs_submission("project_setup", _answers())
    with pytest.raises(ValueError, match="scope_mismatch"):
        validate_needs_submission(fresh, scope="machine_onboarding")


def test_sqlite_transition_requires_complete_sealed_needs_profile(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path)
    session = create_or_resume_setup(
        db,
        scope="machine_onboarding",
        subject_key="machine",
    )
    session = _advance(db, session, "welcome", {})
    session = _advance(db, session, "projects_root", {"path": "C:/projects"})
    session = transition_setup_step(
        db,
        session["id"],
        "needs_profile",
        status="in_progress",
        expected_revision=session["revision"],
        response={"goal": "borrador parcial"},
    )

    with pytest.raises(ValueError, match="schema_mismatch"):
        transition_setup_step(
            db,
            session["id"],
            "needs_profile",
            status="passed",
            expected_revision=session["revision"],
            response={"goal": "intento de bypass"},
        )

    sealed = build_needs_submission("machine_onboarding", _answers())
    completed = transition_setup_step(
        db,
        session["id"],
        "needs_profile",
        status="passed",
        expected_revision=session["revision"],
        response=sealed,
    )
    row = next(
        step for step in completed["steps"] if step["key"] == "needs_profile"
    )
    assert row["status"] == "passed"
    assert row["response"]["assessment_hash"] == sealed["assessment_hash"]
