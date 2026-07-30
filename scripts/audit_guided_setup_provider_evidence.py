from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiteam.guided_setup_needs import build_needs_submission
from aiteam.guided_setup_preparation import build_preparation_plan
from aiteam.guided_setup_provider_evidence import (
    SCHEMA_VERSION,
    build_canonical_provider_evidence,
)
from aiteam.guided_setup_provider_guidance import build_provider_guidance

AUDIT_VERSION = "guided_setup_provider_evidence_audit_v1"
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _needs() -> dict[str, Any]:
    return build_needs_submission(
        "machine_onboarding",
        {
            "goal": "Crear una aplicación",
            "objective_kind": "software",
            "languages": ["Python"],
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


def _inventory(version: str = "codex-cli 0.146.0-alpha.6") -> dict[str, Any]:
    return {
        "schema_version": "machine_doctor_v1",
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
        },
        "runtimes": [],
        "adapters": [
            {
                "id": "codex_subscription",
                "cli": {"installed": True, "version": version},
                "authentication_status": "authenticated",
                "health_status": "ok",
            }
        ],
    }


def _profile(
    *,
    checked_at: datetime = NOW,
    catalog_checked_at: datetime | None = None,
    probe_version: str = "0.146.0-alpha.6",
    probe_evaluated_at: datetime = NOW,
    structured_output: str = "json_schema",
    receipts: list[str] | None = None,
) -> dict[str, Any]:
    catalog: dict[str, Any] = {
        "status": "current",
        "source": "fixture authenticated catalog",
        "count": 3,
    }
    if catalog_checked_at is not None:
        catalog["checked_at"] = catalog_checked_at.isoformat()
    return {
        "id": "codex_subscription",
        "health": {"status": "ok", "checked_at": checked_at.isoformat()},
        "model_catalog": catalog,
        "model_options": [
            {
                "value": "gpt-5.6-sol",
                "availability": "verified",
                "structured_output": structured_output,
                "probe_status": "completed",
                "probe_version": probe_version,
                "probe_evaluated_at": probe_evaluated_at.isoformat(),
                "probe_receipts": (
                    ["receipts/codex-sol.json"] if receipts is None else receipts
                ),
            }
        ],
    }


def _project(
    *,
    inventory: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_inventory = inventory or _inventory()
    plan = build_preparation_plan(_needs(), current_inventory)
    return build_canonical_provider_evidence(
        plan,
        current_inventory,
        [profile or _profile()],
        observed_at=NOW,
    )


def build_audit(repo_root: Path) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    exact = _project()
    stages = exact["stage_evidence"]["codex_subscription"]
    checks["exact_evidence_passes_four_independent_stages"] = stages == {
        "authentication": "passed",
        "catalog": "passed",
        "health": "passed",
        "contract": "passed",
    }
    checks["discovery_never_claims_quality"] = (
        exact["scope"]["discovery_is_quality"] is False
        and exact["scope"]["model_execution_is_structured_contract"] is False
    )
    checks["missing_receipt_fails_contract"] = (
        _project(profile=_profile(receipts=[]))["stage_evidence"][
            "codex_subscription"
        ]["contract"]
        == "not_checked"
    )
    checks["cli_version_mismatch_fails_contract"] = (
        _project(inventory=_inventory("codex-cli 0.147.0"))["stage_evidence"][
            "codex_subscription"
        ]["contract"]
        == "not_checked"
    )
    checks["structured_output_is_required"] = (
        _project(profile=_profile(structured_output="none"))["stage_evidence"][
            "codex_subscription"
        ]["contract"]
        == "not_checked"
    )
    stale_probe = _profile(probe_evaluated_at=NOW - timedelta(days=31))
    checks["stale_probe_fails_contract"] = (
        _project(profile=stale_probe)["stage_evidence"]["codex_subscription"][
            "contract"
        ]
        == "not_checked"
    )
    stale_health = _project(profile=_profile(checked_at=NOW - timedelta(days=2)))
    checks["stale_health_fails_auth_and_health"] = (
        stale_health["stage_evidence"]["codex_subscription"]["authentication"]
        == "not_checked"
        and stale_health["stage_evidence"]["codex_subscription"]["health"]
        == "not_checked"
    )
    stale_catalog = _project(
        profile=_profile(catalog_checked_at=NOW - timedelta(days=2))
    )
    checks["stale_persisted_catalog_fails"] = (
        stale_catalog["stage_evidence"]["codex_subscription"]["catalog"]
        == "not_checked"
    )
    unsafe = _project(profile=_profile(receipts=["C:/fixture/private/probe.json"]))
    checks["unsafe_receipt_reference_is_dropped"] = (
        unsafe["details"][0]["receipt_refs"] == []
        and unsafe["stage_evidence"]["codex_subscription"]["contract"]
        == "not_checked"
    )
    guidance = build_provider_guidance(
        build_preparation_plan(_needs(), _inventory())
    )
    checks["remote_probe_requires_quota_confirmation"] = any(
        action["risk"] == "remote_quota_possible"
        and action["confirmation_required"] is True
        for provider in guidance["providers"]
        for action in provider["actions"]
    )
    router = (
        repo_root / "api" / "routers" / "guided_setup.py"
    ).read_text(encoding="utf-8")
    report = {
        "schema_version": AUDIT_VERSION,
        "contract_version": SCHEMA_VERSION,
        "scope": {
            "fixture_only": True,
            "canonical_api_wired": (
                "build_canonical_provider_evidence(" in router
                and '"canonical_evidence": canonical_evidence' in router
            ),
            "remote_probes_executed": False,
            "secrets_read": False,
            "inference_attempted": False,
            "paths_emitted": False,
        },
        "checks": checks,
        "summary": {
            "provider_evidence_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != AUDIT_VERSION:
        raise ValueError("guided setup provider evidence schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or len(checks) != 10:
        raise ValueError("guided setup provider evidence coverage drift")
    expected = {
        "provider_evidence_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected:
        raise ValueError("guided setup provider evidence summary drift")


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
    return 0 if report["summary"]["provider_evidence_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
