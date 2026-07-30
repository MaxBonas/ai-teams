from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import aiteam.guided_setup_project_commit as project_commit
from aiteam.db.guided_setup import (
    GuidedSetupConflict,
    create_or_resume_setup,
    record_project_commit_receipt,
    transition_setup_step,
)
from aiteam.db.migration import SCHEMA_PATH
from aiteam.guided_setup_coverage import build_guided_setup_coverage
from aiteam.guided_setup_needs import build_needs_submission
from aiteam.guided_setup_project_proposal import build_project_team_proposal
from api.routers.guided_setup import _resolve_project_identity

AUDIT_VERSION = "guided_setup_project_acceptance_v1"
EXPECTED_CHECKS = frozenset(
    {
        "new_research_project_is_lead_only_without_test_loop",
        "import_preserves_foreign_workspace",
        "unsafe_path_is_rejected",
        "create_collision_is_rejected",
        "truncated_detection_requires_confirmation",
        "profile_without_coverage_blocks_save",
        "non_diverse_quorum_blocks_save",
        "valid_override_stays_explicit_and_non_automatic",
        "invalid_override_is_rejected",
        "stale_revision_is_rejected",
        "intermediate_failure_rolls_back_create_and_import",
        "commit_receipt_is_idempotent_and_conflict_safe",
        "session_resume_keeps_identity_and_revision",
    }
)


def _raw_candidate(
    role: str,
    name: str,
    *,
    profile: str,
    perspective: str,
    pool: str,
    automatic: bool = True,
    owner_selectable: bool = True,
) -> dict[str, Any]:
    return {
        "candidate_id": name,
        "identity": {
            "profile_id": profile,
            "model_id": f"model-{name}",
            "provider_org": perspective,
            "channel": "subscription",
            "perspective_key": perspective,
            "capacity_pool": pool,
        },
        "model_metadata": {
            "tier": "premium",
            "caps": ["reasoning", "structured_output"],
        },
        "owner_selectable": owner_selectable,
        "rank": 1,
        "selection_reason": f"acceptance:{role}",
        "contextual_compatibility": {
            "allowed": True,
            "code": "compatible",
        },
        "selection_score": {
            "score": 90,
            "auto_eligible": automatic,
            "auto_ineligible_reasons": (
                [] if automatic else ["calibration_missing"]
            ),
            "hard_gates": {
                "adapter_green": {"passed": True},
                "calibrated": {"passed": automatic},
            },
        },
    }


def _selection(role: str, *candidates: dict[str, Any]) -> dict[str, Any]:
    return {
        "selection_version": "model_selection_v1",
        "canonical_role": role,
        "candidates": list(candidates),
    }


def _coverage(
    *,
    include_reviewer: bool = True,
    manual_lead: bool = False,
    diverse_quorum: bool = True,
    recommended_profile: str = "solo_lead",
) -> dict[str, Any]:
    second_perspective = "google" if diverse_quorum else "openai"
    second_pool = "antigravity" if diverse_quorum else "codex"
    selections = {
        "team_lead": _selection(
            "team_lead",
            _raw_candidate(
                "team_lead",
                "lead-openai",
                profile="codex",
                perspective="openai",
                pool="codex",
            ),
            *(
                [
                    _raw_candidate(
                        "team_lead",
                        "lead-manual",
                        profile="manual",
                        perspective="owner",
                        pool="manual",
                        automatic=False,
                    )
                ]
                if manual_lead
                else []
            ),
        ),
        "quorum_auditor": _selection(
            "quorum_auditor",
            _raw_candidate(
                "quorum_auditor",
                "audit-openai",
                profile="codex",
                perspective="openai",
                pool="codex",
            ),
            _raw_candidate(
                "quorum_auditor",
                "audit-second",
                profile="antigravity",
                perspective=second_perspective,
                pool=second_pool,
            ),
        ),
        "engineer": _selection(
            "engineer",
            _raw_candidate(
                "engineer",
                "engineer-openai",
                profile="codex",
                perspective="openai",
                pool="codex",
            ),
        ),
        "worker": _selection("worker"),
    }
    if include_reviewer:
        selections["reviewer"] = _selection(
            "reviewer",
            _raw_candidate(
                "reviewer",
                "reviewer-google",
                profile="antigravity",
                perspective="google",
                pool="antigravity",
            ),
        )
    return build_guided_setup_coverage(
        selections,
        ready_profile_ids={"codex", "antigravity", "manual"},
        recommended_profile=recommended_profile,
    )


def _needs(
    *,
    kind: str = "research",
    team: str = "solo_lead",
) -> dict[str, Any]:
    goal = (
        "Analizar necesidades de una empresa de limpieza y preparar formularios"
        if kind == "research"
        else "Construir un portal React accesible"
    )
    return build_needs_submission(
        "project_setup",
        {
            "goal": goal,
            "objective_kind": kind,
            "languages": ["unknown"],
            "data_sensitivity": "internal",
            "budget_priority": "balanced",
            "subscriptions": ["codex", "antigravity"],
            "api_access": "not_willing",
            "local_models": "not_wanted",
            "autonomy": "supervised",
            "criticality": "medium",
            "team_preference": team,
            "external_tools": "optional",
        },
    )


def _identity(target: Path, *, mode: str = "create") -> dict[str, Any]:
    return {
        "mode": mode,
        "name": target.name,
        "target": str(target),
        "target_exists": mode == "import",
        "target_is_dir": mode == "import",
    }


def _ecosystems(*, truncated: bool = False) -> dict[str, Any]:
    return {
        "schema_version": "ecosystem_registry_v1",
        "workspace_observed": True,
        "scan_truncated": truncated,
        "files_observed": 1,
        "ecosystems": [],
        "detected_ids": [],
        "support_claims": [],
        "commands_executed": False,
        "installation_performed": False,
        "mutated": False,
    }


def _profiles_for(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    by_profile: dict[str, set[str]] = {}
    for assignment in proposal["team"]["assignments"]:
        candidate = assignment["candidate"]
        by_profile.setdefault(candidate["profile_id"], set()).add(
            candidate["model_id"]
        )
    return [
        {
            "id": profile_id,
            "adapter_type": f"{profile_id}_fixture",
            "channel": "subscription",
            "supported_roles": [],
            "config": {},
            "model_options": [
                {"value": model_id} for model_id in sorted(model_ids)
            ],
        }
        for profile_id, model_ids in sorted(by_profile.items())
    ]


def _proposal(
    target: Path,
    *,
    mode: str = "create",
    kind: str = "research",
    team: str = "solo_lead",
    coverage: dict[str, Any] | None = None,
    truncated: bool = False,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    return build_project_team_proposal(
        _needs(kind=kind, team=team),
        _identity(target, mode=mode),
        _ecosystems(truncated=truncated),
        coverage or _coverage(recommended_profile=team),
        requested_profile=team,
        instructions="Conservar evidencia y pedir desbloqueo si falta contexto.",
        overrides_by_agent_id=overrides,
    )


def _raises(
    exception: type[BaseException],
    code: str,
    call: Callable[[], Any],
) -> bool:
    try:
        call()
    except exception as exc:
        return code in str(exc)
    return False


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_audit(repo_root: Path) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    evidence: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(
        prefix="aiteam-guided-project-acceptance-"
    ) as temporary:
        root = Path(temporary).resolve()

        new_target = root / "research-project"
        research = _proposal(new_target)
        new_result = project_commit.materialize_project_proposal(
            research,
            profiles=_profiles_for(research),
            schema_path=SCHEMA_PATH,
        )
        with closing(sqlite3.connect(new_result["database"])) as conn:
            roles = [
                str(row[0])
                for row in conn.execute(
                    "SELECT role FROM agents ORDER BY rowid"
                ).fetchall()
            ]
            wakeups = conn.execute(
                "SELECT COUNT(*) FROM wakeup_requests WHERE status = 'queued'"
            ).fetchone()[0]
            goal_metadata = json.loads(
                conn.execute(
                    "SELECT metadata_json FROM goals WHERE id = 'goal:intake'"
                ).fetchone()[0]
            )
        classification = goal_metadata["objective_classification"]
        programming_roles = {
            "engineer",
            "software_engineer",
            "qa",
            "test_designer",
            "test_runner",
        }
        checks["new_research_project_is_lead_only_without_test_loop"] = (
            roles == ["lead"]
            and wakeups == 1
            and classification["kind"] == "research"
            and not programming_roles.intersection(roles)
            and research["team"]["creation_order"] == ["role:team_lead"]
        )
        evidence["new_research_project"] = {
            "roles": roles,
            "queued_wakeups": wakeups,
            "objective_kind": classification["kind"],
            "programming_roles_present": sorted(
                programming_roles.intersection(roles)
            ),
        }

        import_target = root / "existing-project"
        import_target.mkdir()
        marker = import_target / "business-notes.md"
        marker.write_text("contenido ajeno", encoding="utf-8")
        imported = _proposal(import_target, mode="import")
        import_result = project_commit.materialize_project_proposal(
            imported,
            profiles=_profiles_for(imported),
            schema_path=SCHEMA_PATH,
        )
        checks["import_preserves_foreign_workspace"] = (
            marker.read_text(encoding="utf-8") == "contenido ajeno"
            and Path(import_result["database"]).is_file()
            and not list(import_target.glob(".aiteam-staging-*"))
        )
        evidence["import"] = {
            "foreign_file_preserved": True,
            "runtime_created": Path(import_result["runtime"]).is_dir(),
            "staging_leftovers": len(
                list(import_target.glob(".aiteam-staging-*"))
            ),
        }

        projects_root = root / "confined-projects"
        previous_projects_root = os.environ.get("AITEAM_PROJECTS_ROOT")
        os.environ["AITEAM_PROJECTS_ROOT"] = str(projects_root)
        try:
            checks["unsafe_path_is_rejected"] = _raises(
                ValueError,
                "outside_projects_root",
                lambda: _resolve_project_identity(
                    {
                        "mode": "import",
                        "name": "outside",
                        "path": str(root.parent / "outside"),
                    }
                ),
            )
        finally:
            if previous_projects_root is None:
                os.environ.pop("AITEAM_PROJECTS_ROOT", None)
            else:
                os.environ["AITEAM_PROJECTS_ROOT"] = previous_projects_root

        collision_target = root / "collision"
        collision_target.mkdir()
        collision_identity = _identity(collision_target)
        collision_identity["target_exists"] = True
        checks["create_collision_is_rejected"] = _raises(
            ValueError,
            "target_collision",
            lambda: build_project_team_proposal(
                _needs(),
                collision_identity,
                _ecosystems(),
                _coverage(),
            ),
        )

        truncated = _proposal(root / "truncated", truncated=True)
        checks["truncated_detection_requires_confirmation"] = (
            "ecosystem_scan_truncated" in truncated["degradations"]
            and truncated["save_gate"]["requires_owner_confirmation"] is True
        )

        incomplete = _proposal(
            root / "incomplete",
            kind="software",
            team="full_team",
            coverage=_coverage(
                include_reviewer=False,
                recommended_profile="full_team",
            ),
        )
        checks["profile_without_coverage_blocks_save"] = (
            incomplete["save_gate"]["allowed"] is False
            and incomplete["save_gate"]["blockers"]
            == ["assignment:role:reviewer"]
        )

        non_diverse = _proposal(
            root / "quorum",
            kind="software",
            team="lead_quorum",
            coverage=_coverage(
                diverse_quorum=False,
                recommended_profile="lead_quorum",
            ),
        )
        checks["non_diverse_quorum_blocks_save"] = (
            non_diverse["save_gate"]["allowed"] is False
            and "quorum_diversity" in non_diverse["save_gate"]["blockers"]
            and non_diverse["team"]["quorum_diversity"]["ready"] is False
        )

        manual_coverage = _coverage(
            manual_lead=True,
            recommended_profile="solo_lead",
        )
        valid_override = _proposal(
            root / "override",
            coverage=manual_coverage,
            overrides={"role:team_lead": "lead-manual"},
        )
        override_assignment = valid_override["team"]["assignments"][0]
        checks["valid_override_stays_explicit_and_non_automatic"] = (
            override_assignment["selection_mode"] == "owner_explicit"
            and override_assignment["candidate"]["coverage_eligible"] is False
            and valid_override["save_gate"]["allowed"] is True
            and valid_override["save_gate"]["requires_owner_confirmation"] is True
        )

        invalid_coverage = deepcopy(manual_coverage)
        invalid_manual = invalid_coverage["roles"]["team_lead"][
            "excluded_candidates"
        ][0]
        invalid_manual["exclusion_reasons"].append(
            "adapter_not_prepared_in_setup"
        )
        checks["invalid_override_is_rejected"] = _raises(
            ValueError,
            "override_not_selectable",
            lambda: _proposal(
                root / "invalid-override",
                coverage=invalid_coverage,
                overrides={"role:team_lead": "lead-manual"},
            ),
        )

        setup_db = root / "guided-setup.db"
        session = create_or_resume_setup(
            setup_db,
            scope="project_setup",
            subject_key="acceptance-project",
        )
        advanced = transition_setup_step(
            setup_db,
            session["id"],
            "project_identity",
            status="in_progress",
            expected_revision=session["revision"],
        )
        checks["stale_revision_is_rejected"] = _raises(
            GuidedSetupConflict,
            "revision_conflict",
            lambda: transition_setup_step(
                setup_db,
                session["id"],
                "project_identity",
                status="in_progress",
                expected_revision=session["revision"],
            ),
        )

        original_insert = project_commit._insert_project_state

        def fail_insert(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("acceptance_injected_failure")

        rollback_create = root / "rollback-create"
        rollback_import = root / "rollback-import"
        rollback_import.mkdir()
        rollback_marker = rollback_import / "keep.txt"
        rollback_marker.write_text("keep", encoding="utf-8")
        project_commit._insert_project_state = fail_insert
        try:
            create_failed = _raises(
                RuntimeError,
                "acceptance_injected_failure",
                lambda: project_commit.materialize_project_proposal(
                    _proposal(rollback_create),
                    profiles=_profiles_for(_proposal(rollback_create)),
                    schema_path=SCHEMA_PATH,
                ),
            )
            import_proposal = _proposal(rollback_import, mode="import")
            import_failed = _raises(
                RuntimeError,
                "acceptance_injected_failure",
                lambda: project_commit.materialize_project_proposal(
                    import_proposal,
                    profiles=_profiles_for(import_proposal),
                    schema_path=SCHEMA_PATH,
                ),
            )
        finally:
            project_commit._insert_project_state = original_insert
        checks["intermediate_failure_rolls_back_create_and_import"] = (
            create_failed
            and import_failed
            and not rollback_create.exists()
            and rollback_marker.read_text(encoding="utf-8") == "keep"
            and not (rollback_import / ".aiteam").exists()
            and not list(root.glob(".aiteam-project-staging-*"))
            and not list(rollback_import.glob(".aiteam-staging-*"))
        )

        receipt_result = {
            "schema_version": "guided_setup_project_commit_v1",
            "workspace": "fixture/project",
        }
        proposal_hash = "a" * 64
        first_receipt = record_project_commit_receipt(
            setup_db,
            session["id"],
            proposal_hash=proposal_hash,
            project_target="fixture/project",
            result=receipt_result,
        )
        replay_receipt = record_project_commit_receipt(
            setup_db,
            session["id"],
            proposal_hash=proposal_hash,
            project_target="fixture/project",
            result=receipt_result,
        )
        conflicting_receipt_rejected = _raises(
            GuidedSetupConflict,
            "already_committed",
            lambda: record_project_commit_receipt(
                setup_db,
                session["id"],
                proposal_hash="b" * 64,
                project_target="fixture/other",
                result=receipt_result,
            ),
        )
        checks["commit_receipt_is_idempotent_and_conflict_safe"] = (
            first_receipt["id"] == replay_receipt["id"]
            and first_receipt["result"] == replay_receipt["result"]
            and conflicting_receipt_rejected
        )
        resumed = create_or_resume_setup(
            setup_db,
            scope="project_setup",
            subject_key="acceptance-project",
        )
        checks["session_resume_keeps_identity_and_revision"] = (
            resumed["id"] == advanced["id"]
            and resumed["revision"] == advanced["revision"]
            and resumed["current_step"] == advanced["current_step"]
        )
        evidence["durability"] = {
            "session_id_stable": resumed["id"] == session["id"],
            "revision_after_transition": advanced["revision"],
            "receipt_id_stable": first_receipt["id"] == replay_receipt["id"],
            "conflicting_hash_rejected": conflicting_receipt_rejected,
        }

    report = {
        "schema_version": AUDIT_VERSION,
        "scope": {
            "temporary_projects_only": True,
            "user_projects_mutated": False,
            "user_configuration_mutated": False,
            "defaults_mutated": False,
            "installations_attempted": False,
            "secrets_read": False,
            "inference_attempted": False,
            "remote_quota_consumed": False,
        },
        "checks": checks,
        "evidence": evidence,
        "summary": {
            "project_setup_acceptance_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    report["evidence_hash"] = _hash(
        {
            "schema_version": report["schema_version"],
            "scope": report["scope"],
            "checks": report["checks"],
            "evidence": report["evidence"],
        }
    )
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != AUDIT_VERSION:
        raise ValueError("guided setup project acceptance schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or set(checks) != EXPECTED_CHECKS:
        raise ValueError("guided setup project acceptance matrix drift")
    if any(not isinstance(value, bool) for value in checks.values()):
        raise ValueError("guided setup project acceptance check type drift")
    expected_summary = {
        "project_setup_acceptance_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected_summary:
        raise ValueError("guided setup project acceptance summary drift")
    expected_hash = _hash(
        {
            "schema_version": report["schema_version"],
            "scope": report.get("scope"),
            "checks": checks,
            "evidence": report.get("evidence"),
        }
    )
    if report.get("evidence_hash") != expected_hash:
        raise ValueError("guided setup project acceptance evidence drift")


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
    return (
        0
        if report["summary"]["project_setup_acceptance_ready"]
        or not args.strict
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
