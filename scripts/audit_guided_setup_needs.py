from __future__ import annotations

import argparse
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from aiteam.db.guided_setup import (
    create_or_resume_setup,
    transition_setup_step,
)
from aiteam.guided_setup_needs import (
    SCHEMA_VERSION,
    build_needs_submission,
    needs_questionnaire,
    validate_needs_submission,
)

AUDIT_VERSION = "guided_setup_needs_audit_v1"


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


def _pass_step(db: Path, session: dict, step_key: str, response: dict) -> dict:
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


def build_audit(repo_root: Path) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    software_contract = needs_questionnaire(
        "project_setup",
        {"objective_kind": "software"},
    )
    research_contract = needs_questionnaire(
        "project_setup",
        {"objective_kind": "research"},
    )
    software_rows = {
        row["id"]: row for row in software_contract["questions"]
    }
    research_rows = {
        row["id"]: row for row in research_contract["questions"]
    }
    checks["versioned_explained_contract"] = (
        software_contract["schema_version"] == SCHEMA_VERSION
        and all(row["help"] for row in software_contract["questions"])
        and all(
            row["recommendation_reason"]
            for row in software_contract["questions"]
        )
    )
    checks["adaptive_language_question"] = (
        software_rows["languages"]["visible"] is True
        and research_rows["languages"]["visible"] is False
    )

    sealed = build_needs_submission("project_setup", _answers())
    checks["complete_submission_sealed"] = (
        sealed["assessment"]["complete"] is True
        and len(sealed["assessment_hash"]) == 64
        and validate_needs_submission(sealed, scope="project_setup") == sealed
    )
    research = build_needs_submission(
        "project_setup",
        _answers(
            goal=(
                "Crear formularios y un estudio de necesidades "
                "para una empresa de limpieza"
            ),
            objective_kind="unknown",
            languages=["unknown"],
        ),
    )
    checks["research_fixture_needs_confirmation"] = (
        research["assessment"]["objective"]["kind"] == "research"
        and research["assessment"]["objective"]["requires_confirmation"] is True
    )
    normal_kinds = {
        row["kind"] for row in sealed["assessment"]["channel_strategy"]
    }
    local = build_needs_submission(
        "project_setup",
        _answers(local_models="willing"),
    )
    local_kinds = {
        row["kind"] for row in local["assessment"]["channel_strategy"]
    }
    checks["local_requires_owner_opt_in"] = (
        "local" not in normal_kinds and "local" in local_kinds
    )
    critical = build_needs_submission(
        "project_setup",
        _answers(criticality="critical"),
    )
    checks["criticality_recommends_quorum"] = (
        critical["assessment"]["recommended_run_profile"] == "lead_quorum"
    )
    incomplete = build_needs_submission(
        "machine_onboarding",
        {"goal": "Preparar mi máquina"},
    )
    try:
        validate_needs_submission(incomplete, scope="machine_onboarding")
    except ValueError:
        checks["incomplete_completion_rejected"] = True
    else:
        checks["incomplete_completion_rejected"] = False
    tampered = deepcopy(sealed)
    tampered["assessment"]["recommended_run_profile"] = "full_team"
    try:
        validate_needs_submission(tampered, scope="project_setup")
    except ValueError:
        checks["tampered_assessment_rejected"] = True
    else:
        checks["tampered_assessment_rejected"] = False

    with tempfile.TemporaryDirectory(prefix="aiteam-needs-audit-") as raw:
        db = Path(raw) / "guided_setup.db"
        session = create_or_resume_setup(
            db,
            scope="machine_onboarding",
            subject_key="audit-machine",
        )
        session = _pass_step(db, session, "welcome", {})
        session = _pass_step(
            db,
            session,
            "projects_root",
            {"path": "C:/fixture"},
        )
        session = transition_setup_step(
            db,
            session["id"],
            "needs_profile",
            status="in_progress",
            expected_revision=session["revision"],
        )
        try:
            transition_setup_step(
                db,
                session["id"],
                "needs_profile",
                status="passed",
                expected_revision=session["revision"],
                response={"goal": "bypass"},
            )
        except ValueError:
            checks["sqlite_completion_gate_fail_closed"] = True
        else:
            checks["sqlite_completion_gate_fail_closed"] = False

    router_source = (
        repo_root / "api" / "routers" / "guided_setup.py"
    ).read_text(encoding="utf-8")
    checks["authenticated_api_is_wired"] = all(
        marker in router_source
        for marker in (
            '"/needs-contract/{scope}"',
            '"/needs-assessment"',
            "_require_api_auth_request",
        )
    )
    report = {
        "schema_version": AUDIT_VERSION,
        "contract_version": SCHEMA_VERSION,
        "scope": {
            "fixture_database_only": True,
            "global_installations_mutated": False,
            "user_configuration_mutated": False,
            "secrets_read": False,
            "inference_attempted": False,
            "paths_emitted": False,
        },
        "checks": checks,
        "summary": {
            "needs_interview_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != AUDIT_VERSION:
        raise ValueError("guided setup needs audit schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or len(checks) != 10:
        raise ValueError("guided setup needs audit coverage drift")
    expected = {
        "needs_interview_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected:
        raise ValueError("guided setup needs audit summary drift")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_audit(Path(__file__).resolve().parents[1])
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    return 0 if report["summary"]["needs_interview_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
