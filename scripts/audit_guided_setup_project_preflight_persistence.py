"""Audita persistencia, idempotencia y aislamiento del preflight de proyecto."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.db.guided_setup import (
    GuidedSetupConflict,
    create_or_resume_setup,
    get_latest_project_preflight_receipt,
    get_project_preflight_receipt_for_plan,
    record_project_preflight_receipt,
    resolve_project_fixture_evidence,
)
from aiteam.guided_setup_project_preflight import build_project_preflight
from aiteam.guided_setup_project_preflight_execution import (
    build_project_preflight_execution_plan,
)
from aiteam.guided_setup_project_preflight_executor import (
    execute_project_preflight_plan,
)
from scripts.audit_guided_setup_project_preflight import (
    _inventory,
    _needs,
    _path,
    _preparation,
    _proposal,
)

AUDIT_VERSION = "guided_setup_project_preflight_persistence_audit_v1"
EXPECTED_CHECKS = frozenset({
    "go_receipt_is_durable",
    "same_plan_is_idempotent",
    "artifact_is_content_addressed",
    "fixture_reference_resolves",
    "evidence_is_session_confined",
    "corrupt_artifact_fails_closed",
})


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _raises(call: Any, code: str) -> bool:
    try:
        call()
    except (GuidedSetupConflict, TypeError, ValueError) as exc:
        return code in str(exc)
    return False


def build_audit(_repo_root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="aiteam-guided-preflight-persistence-"
    ) as temporary:
        db_path = Path(temporary) / "guided_setup.db"
        session = create_or_resume_setup(
            db_path,
            scope="project_setup",
            subject_key="audit:software",
        )
        foreign_session = create_or_resume_setup(
            db_path,
            scope="project_setup",
            subject_key="audit:foreign",
        )
        needs = _needs("software")
        proposal = _proposal(needs)
        preparation = _preparation()
        inventory = _inventory()
        path_observation = _path()
        initial = build_project_preflight(
            needs,
            proposal,
            preparation,
            inventory,
            path_observation,
        )
        plan = build_project_preflight_execution_plan(
            needs,
            proposal,
            initial,
        )
        execution = execute_project_preflight_plan(
            plan,
            plan_hash=plan["plan_hash"],
            confirm_local_fixture=True,
            confirm_remote_probe=False,
            acknowledge_possible_quota=False,
            fixture_runner=lambda case_id, _timeout: {
                "schema_version": "ecosystem_validation_receipt_v1",
                "cases": [{"id": case_id, "status": "passed"}],
                "summary": {"total": 1, "passed": 1},
            },
        )
        post = build_project_preflight(
            needs,
            proposal,
            preparation,
            inventory,
            path_observation,
            fixture_evidence=execution["fixture_evidence"],
        )
        first = record_project_preflight_receipt(
            db_path,
            session["id"],
            preflight=post,
            execution_plan=plan,
            execution_receipt=execution,
        )
        replay = record_project_preflight_receipt(
            db_path,
            session["id"],
            preflight=post,
            execution_plan=plan,
            execution_receipt=execution,
        )
        exact = get_project_preflight_receipt_for_plan(
            db_path,
            session["id"],
            plan["plan_hash"],
        )
        latest = get_latest_project_preflight_receipt(
            db_path,
            session["id"],
        )
        references = first["fixture_evidence_refs"]
        resolved = resolve_project_fixture_evidence(
            db_path,
            session["id"],
            references,
        )
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            counts = {
                "receipts": conn.execute(
                    "SELECT COUNT(*) FROM guided_setup_project_preflight_receipts"
                ).fetchone()[0],
                "artifacts": conn.execute(
                    "SELECT COUNT(*) FROM guided_setup_project_preflight_artifacts"
                ).fetchone()[0],
            }
            conn.execute(
                """
                UPDATE guided_setup_project_preflight_artifacts
                SET content_json = '{}'
                WHERE session_id = ? AND reference = ?
                """,
                (session["id"], references[0]),
            )
            conn.commit()
        checks = {
            "go_receipt_is_durable": (
                first["status"] == "go"
                and latest is not None
                and latest["receipt_hash"] == first["receipt_hash"]
            ),
            "same_plan_is_idempotent": (
                replay["id"] == first["id"]
                and exact is not None
                and exact["id"] == first["id"]
                and counts["receipts"] == 1
                and counts["artifacts"] == 1
            ),
            "artifact_is_content_addressed": (
                len(references) == 1
                and references[0]
                == f"sha256:{_hash(execution['artifacts'][0]['content'])}"
            ),
            "fixture_reference_resolves": (
                resolved == execution["fixture_evidence"]
            ),
            "evidence_is_session_confined": _raises(
                lambda: resolve_project_fixture_evidence(
                    db_path,
                    foreign_session["id"],
                    references,
                ),
                "not_persisted",
            ),
            "corrupt_artifact_fails_closed": _raises(
                lambda: resolve_project_fixture_evidence(
                    db_path,
                    session["id"],
                    references,
                ),
                "evidence_corrupt",
            ),
        }
        evidence = {
            "receipt_schema_version": first["schema_version"],
            "execution_schema_version": execution["schema_version"],
            "preflight_schema_version": post["schema_version"],
            "receipt_hash": first["receipt_hash"],
            "preflight_hash": first["preflight_hash"],
            "execution_plan_hash": first["execution_plan_hash"],
            "execution_receipt_hash": first["execution_receipt_hash"],
            "row_counts": counts,
        }
    report = {
        "schema_version": AUDIT_VERSION,
        "scope": {
            "temporary_database_only": True,
            "user_projects_mutated": False,
            "commands_executed": False,
            "tests_executed": False,
            "remote_calls": False,
            "inference_attempted": False,
            "quota_consumed": False,
        },
        "checks": checks,
        "evidence": evidence,
        "evidence_hash": _hash(evidence),
        "summary": {
            "persistence_contract_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != AUDIT_VERSION:
        raise ValueError("guided setup preflight persistence audit schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or set(checks) != EXPECTED_CHECKS:
        raise ValueError("guided setup preflight persistence audit matrix drift")
    evidence = report.get("evidence")
    if (
        not isinstance(evidence, dict)
        or report.get("evidence_hash") != _hash(evidence)
    ):
        raise ValueError("guided setup preflight persistence audit evidence drift")
    expected_summary = {
        "persistence_contract_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected_summary:
        raise ValueError("guided setup preflight persistence audit summary drift")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_audit(REPO_ROOT)
    payload = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    ready = report["summary"]["persistence_contract_ready"]
    return 0 if ready or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
