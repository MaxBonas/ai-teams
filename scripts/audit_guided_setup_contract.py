from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from aiteam.db.guided_setup import (
    SCHEMA_VERSION,
    GuidedSetupConflict,
    create_or_resume_setup,
    get_setup,
    reset_setup,
    setup_contract,
    transition_setup_step,
)

AUDIT_VERSION = "guided_setup_contract_audit_v1"


def build_audit(repo_root: Path) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    contracts = {
        scope: setup_contract(scope)
        for scope in (
            "machine_onboarding",
            "project_setup",
            "installation_repair",
        )
    }
    checks["three_scopes_versioned"] = all(
        contract["schema_version"] == SCHEMA_VERSION
        for contract in contracts.values()
    )
    checks["ordinals_and_dependencies_declared"] = all(
        [step["ordinal"] for step in contract["steps"]]
        == list(range(len(contract["steps"])))
        and all("depends_on" in step for step in contract["steps"])
        for contract in contracts.values()
    )
    with tempfile.TemporaryDirectory(prefix="aiteam-guided-setup-audit-") as raw:
        db = Path(raw) / "guided_setup.db"
        first = create_or_resume_setup(
            db,
            scope="machine_onboarding",
            subject_key="audit-machine",
            metadata={"entrypoint": "audit"},
        )
        resumed = create_or_resume_setup(
            db,
            scope="machine_onboarding",
            subject_key="audit-machine",
        )
        checks["create_resume_idempotent"] = (
            first["id"] == resumed["id"]
            and resumed["metadata"] == {"entrypoint": "audit"}
        )
        checks["persists_across_connections"] = get_setup(db, first["id"]) == resumed
        try:
            transition_setup_step(
                db,
                first["id"],
                "projects_root",
                status="in_progress",
                expected_revision=first["revision"],
            )
        except GuidedSetupConflict:
            checks["dependency_skip_rejected"] = True
        else:
            checks["dependency_skip_rejected"] = False
        try:
            transition_setup_step(
                db,
                first["id"],
                "welcome",
                status="in_progress",
                expected_revision=first["revision"],
                response={"api_key": "forbidden"},
            )
        except ValueError:
            checks["secret_value_rejected"] = True
        else:
            checks["secret_value_rejected"] = False
        draft = transition_setup_step(
            db,
            first["id"],
            "welcome",
            status="in_progress",
            expected_revision=first["revision"],
            response={"secret_ref": "secret:fixture:default", "token_budget": 1000},
        )
        draft = transition_setup_step(
            db,
            first["id"],
            "welcome",
            status="in_progress",
            expected_revision=draft["revision"],
            response={"secret_ref": "secret:fixture:default", "token_budget": 2000},
        )
        checks["incremental_draft_persisted"] = (
            draft["steps"][0]["response"]["token_budget"] == 2000
        )
        blocked = transition_setup_step(
            db,
            first["id"],
            "welcome",
            status="blocked",
            expected_revision=draft["revision"],
            blocker_code="audit_blocker",
        )
        resumed_step = transition_setup_step(
            db,
            first["id"],
            "welcome",
            status="in_progress",
            expected_revision=blocked["revision"],
        )
        checks["blocked_step_resumes"] = (
            blocked["status"] == "blocked"
            and resumed_step["status"] == "in_progress"
        )
        try:
            reset_setup(
                db,
                first["id"],
                expected_revision=resumed_step["revision"],
                confirm=False,
            )
        except ValueError:
            confirmation_rejected = True
        else:
            confirmation_rejected = False
        reset = reset_setup(
            db,
            first["id"],
            expected_revision=resumed_step["revision"],
            confirm=True,
        )
        checks["reset_is_explicit_and_complete"] = (
            confirmation_rejected
            and {step["status"] for step in reset["steps"]} == {"not_started"}
        )
    main_source = (repo_root / "api" / "main.py").read_text(encoding="utf-8")
    router_source = (
        repo_root / "api" / "routers" / "guided_setup.py"
    ).read_text(encoding="utf-8")
    checks["api_is_wired"] = (
        "guided_setup_router" in main_source
        and "/api/guided-setup" in router_source
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
        "contracts": {
            scope: {"step_count": len(contract["steps"])}
            for scope, contract in contracts.items()
        },
        "checks": checks,
        "summary": {
            "contract_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != AUDIT_VERSION:
        raise ValueError("guided setup audit schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or len(checks) != 10:
        raise ValueError("guided setup audit coverage drift")
    if report.get("summary") != {
        "contract_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }:
        raise ValueError("guided setup audit summary drift")


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
    return 0 if report["summary"]["contract_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
