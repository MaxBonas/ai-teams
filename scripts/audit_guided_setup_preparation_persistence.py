from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from aiteam.db.guided_setup import (
    create_or_resume_setup,
    record_setup_preparation,
    transition_setup_step,
)
from aiteam.guided_setup_needs import build_needs_submission
from aiteam.guided_setup_preparation import (
    SCHEMA_VERSION,
    build_preparation_plan,
)

AUDIT_VERSION = "guided_setup_preparation_persistence_audit_v1"


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


def _advance(db: Path, subject: str) -> dict[str, Any]:
    session = create_or_resume_setup(
        db,
        scope="machine_onboarding",
        subject_key=subject,
    )
    for key, response in (
        ("welcome", {}),
        ("projects_root", {"path": "C:/fixture-only"}),
        ("needs_profile", _needs()),
    ):
        session = transition_setup_step(
            db,
            session["id"],
            key,
            status="in_progress",
            expected_revision=session["revision"],
        )
        session = transition_setup_step(
            db,
            session["id"],
            key,
            status="passed",
            expected_revision=session["revision"],
            response=response,
        )
    return session


def build_audit(repo_root: Path) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    inventory = _inventory()
    with tempfile.TemporaryDirectory(prefix="aiteam-preparation-db-audit-") as raw:
        db = Path(raw) / "guided_setup.db"
        unready_session = _advance(db, "unready")
        unready_plan = build_preparation_plan(_needs(), inventory)
        persisted = record_setup_preparation(
            db,
            unready_session["id"],
            expected_revision=unready_session["revision"],
            plan=unready_plan,
            inventory=inventory,
        )
        receipt = persisted["receipt"]
        checks["receipt_is_compact"] = (
            set(receipt)
            == {
                "id",
                "schema_version",
                "needs_hash",
                "plan_hash",
                "doctor_hash",
                "ready",
                "blockers",
            }
            and "adapters" not in receipt
            and "runtimes" not in receipt
        )
        checks["receipt_hashes_are_bound"] = (
            len(receipt["needs_hash"]) == 64
            and len(receipt["plan_hash"]) == 64
            and len(receipt["doctor_hash"]) == 64
        )
        checks["revision_advanced_once"] = (
            persisted["session"]["revision"] == unready_session["revision"] + 1
        )
        try:
            transition_setup_step(
                db,
                unready_session["id"],
                "adapter_setup",
                status="passed",
                expected_revision=persisted["session"]["revision"],
                evidence={"ready": True},
            )
        except ValueError:
            checks["unready_receipt_blocks_completion"] = True
        else:
            checks["unready_receipt_blocks_completion"] = False

        ready_session = _advance(db, "ready")
        ready_plan = build_preparation_plan(
            _needs(),
            inventory,
            provider_evidence={
                "codex_subscription": {
                    "catalog": "passed",
                    "contract": "passed",
                }
            },
        )
        ready_persisted = record_setup_preparation(
            db,
            ready_session["id"],
            expected_revision=ready_session["revision"],
            plan=ready_plan,
            inventory=inventory,
        )
        completed = transition_setup_step(
            db,
            ready_session["id"],
            "adapter_setup",
            status="passed",
            expected_revision=ready_persisted["session"]["revision"],
            evidence={"forged": True},
        )
        step = next(
            row for row in completed["steps"] if row["key"] == "adapter_setup"
        )
        checks["server_receipt_overrides_client_evidence"] = (
            step["evidence"]["ready"] is True
            and "forged" not in step["evidence"]
        )
        with contextlib.closing(sqlite3.connect(db)) as conn:
            foreign_keys = conn.execute(
                "PRAGMA foreign_key_list(guided_setup_preparation_receipts)"
            ).fetchall()
            count = conn.execute(
                "SELECT COUNT(*) FROM guided_setup_preparation_receipts"
            ).fetchone()[0]
        checks["sqlite_receipts_are_durable"] = count == 2
        checks["receipt_has_step_foreign_key"] = len(foreign_keys) >= 2

    router = (
        repo_root / "api" / "routers" / "guided_setup.py"
    ).read_text(encoding="utf-8")
    checks["api_is_authenticated_and_server_side"] = all(
        marker in router
        for marker in (
            '"/sessions/{session_id}/preparation"',
            "_require_api_auth_request(request)",
            "inventory = build_machine_inventory(",
            "adapter_profiles=profiles,",
            "record_setup_preparation(",
        )
    )
    checks["client_cannot_submit_provider_evidence"] = (
        "class PreparationRunRequest" in router
        and "provider_evidence:" not in router.split(
            "class PreparationRunRequest", 1
        )[1].split("@router", 1)[0]
    )
    serialized = json.dumps(
        {"receipt": receipt, "scope": unready_plan["scope"]},
        ensure_ascii=False,
    )
    checks["receipt_is_redacted_and_non_mutating"] = (
        "fixture-only" not in serialized
        and unready_plan["scope"]["secrets_read"] is False
        and unready_plan["scope"]["installations_attempted"] is False
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
            "persistence_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != AUDIT_VERSION:
        raise ValueError("guided setup preparation persistence schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or len(checks) != 10:
        raise ValueError("guided setup preparation persistence coverage drift")
    expected = {
        "persistence_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected:
        raise ValueError("guided setup preparation persistence summary drift")


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
    return 0 if report["summary"]["persistence_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
