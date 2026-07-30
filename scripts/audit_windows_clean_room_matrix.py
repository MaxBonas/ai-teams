"""Sella los receipts Windows de clone limpio e instalación actualizada."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "windows_clean_room_matrix_acceptance_v1"


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_matrix(clean_path: Path, updated_path: Path) -> dict[str, Any]:
    clean = json.loads(clean_path.read_text(encoding="utf-8"))
    updated = json.loads(updated_path.read_text(encoding="utf-8"))
    by_kind = {
        clean.get("installation_state", {}).get("kind"): clean,
        updated.get("installation_state", {}).get("kind"): updated,
    }
    if set(by_kind) != {"clean_clone", "existing_checkout_updated"}:
        raise ValueError("windows clean-room matrix scenarios drift")

    clean = by_kind["clean_clone"]
    updated = by_kind["existing_checkout_updated"]
    revision = clean.get("source", {}).get("revision")
    checks = {
        "both_receipts_green": all(
            receipt.get("ok") is True for receipt in (clean, updated)
        ),
        "both_independent_and_promotable": all(
            receipt.get("independent_machine") is True
            and receipt.get("promotion_allowed") is True
            for receipt in (clean, updated)
        ),
        "both_checkouts_clean": all(
            receipt.get("source", {}).get("working_tree_dirty") is False
            for receipt in (clean, updated)
        ),
        "same_exact_revision": (
            isinstance(revision, str)
            and revision == updated.get("source", {}).get("revision")
            and bool(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision))
        ),
        "same_exact_harness": (
            clean.get("source", {}).get("harness_sha256")
            == updated.get("source", {}).get("harness_sha256")
            and bool(
                re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(clean.get("source", {}).get("harness_sha256") or ""),
                )
            )
        ),
        "update_started_from_distinct_revision": (
            updated["installation_state"].get("pre_update_revision")
            not in {None, revision}
        ),
        "guided_commit_and_retry_green": all(
            receipt.get("fixture", {}).get("commit_schema_version")
            == "guided_setup_project_commit_v1"
            and receipt.get("fixture", {}).get("footprint_verified") is True
            and receipt.get("fixture", {}).get("retry_collision_blocked") is True
            for receipt in (clean, updated)
        ),
        "restart_update_and_rollback_green": all(
            receipt.get("update_acceptance", {}).get("project_unchanged") is True
            and receipt.get("database_rollback", {}).get("footprint_restored") is True
            for receipt in (clean, updated)
        ),
        "no_persistent_cleanup_lifecycle": all(
            not any(
                receipt.get("installation_lifecycle", {}).get(key) is True
                for key in (
                    "scheduled_tasks_installed",
                    "services_installed",
                    "startup_entries_installed",
                )
            )
            for receipt in (clean, updated)
        ),
    }
    sources = {
        kind: {
            "receipt_sha256": _hash(receipt),
            "revision": receipt["source"]["revision"],
            "harness_sha256": receipt["source"]["harness_sha256"],
            "step_count": len(receipt.get("steps") or ()),
        }
        for kind, receipt in sorted(by_kind.items())
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "receipts_only": True,
            "paths_emitted": False,
            "secrets_read": False,
            "installations_mutated": False,
            "inference_attempted": False,
            "remote_quota_consumed": False,
        },
        "checks": checks,
        "sources": sources,
        "summary": {
            "matrix_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    report["evidence_hash"] = _hash(
        {
            "schema_version": report["schema_version"],
            "scope": report["scope"],
            "checks": report["checks"],
            "sources": report["sources"],
        }
    )
    validate_matrix(report)
    return report


def validate_matrix(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("windows clean-room matrix schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or len(checks) != 9:
        raise ValueError("windows clean-room matrix coverage drift")
    expected = {
        "matrix_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected:
        raise ValueError("windows clean-room matrix summary drift")
    expected_hash = _hash(
        {
            "schema_version": report["schema_version"],
            "scope": report.get("scope"),
            "checks": checks,
            "sources": report.get("sources"),
        }
    )
    if report.get("evidence_hash") != expected_hash:
        raise ValueError("windows clean-room matrix evidence drift")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", type=Path, required=True)
    parser.add_argument("--updated", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_matrix(args.clean, args.updated)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if report["summary"]["matrix_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
