# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.model_automation_enforcement import (
    audit_model_automation_enforcement,
)
from aiteam.model_calibration_gate_board import attach_calibration_gates
from aiteam.model_catalog_read_model import (
    build_current_model_catalog_read_model,
)
from aiteam.model_evaluation_coverage import audit_model_evaluation_coverage
from aiteam.model_owner_preferences import (
    load_model_owner_preferences,
    model_owner_preference_from_document,
)
from aiteam.user_config import (
    DEFAULT_ADAPTER_PROFILES,
    executable_model_options,
    load_adapter_profiles,
    model_is_selectable,
    model_options,
)
from scripts.audit_model_evaluation_coverage import _versions_from_drift
from scripts.audit_model_residual_policy import build_audit as build_inventory_audit

SCHEMA_VERSION = "model_residual_policy_parity_audit_v1"


def build_audit(
    *,
    read_model: dict[str, Any],
    preferences: dict[str, Any],
    coverage: dict[str, Any],
    automation: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    read_model_before = deepcopy(read_model)
    preferences_before = deepcopy(preferences)
    inventory = build_inventory_audit(read_model, preferences)
    attached = attach_calibration_gates(read_model.get("candidates") or ())
    attached_rows = {
        (
            candidate["identity"]["profile_id"],
            candidate["identity"]["model_id"],
            role["canonical_role"],
        ): role["calibration_gate"]
        for candidate in attached
        for role in candidate.get("roles") or ()
    }
    backlog = list(coverage.get("maintenance_backlog") or ())
    api_source = (repo_root / "api" / "routers" / "model_catalog.py").read_text(
        encoding="utf-8"
    )
    ui_source = (
        repo_root
        / "ide-frontend"
        / "src"
        / "components"
        / "ModelCatalog"
        / "ModelCatalog.tsx"
    ).read_text(encoding="utf-8")
    executor_source = (
        repo_root / "aiteam" / "heartbeat" / "executor.py"
    ).read_text(encoding="utf-8")
    normal_fixture = {
        "schema_version": "model_owner_preferences_v1",
        "updated_at": "2026-07-30T12:00:00+00:00",
        "preferences": [
            {
                "profile_id": "fixture-profile",
                "model_id": "fixture-model",
                "state": "normal",
                "reason": "explicit reactivation fixture",
                "updated_at": "2026-07-30T12:00:00+00:00",
            }
        ],
    }
    reactivated = model_owner_preference_from_document(
        normal_fixture,
        "fixture-profile",
        "fixture-model",
    )
    role_count = sum(
        len(candidate.get("roles") or ())
        for candidate in read_model.get("candidates") or ()
    )
    checks = {
        "inventory_policy_complete": (
            inventory["summary"]["inventory_ready"] is True
            and inventory["summary"]["policy_complete"] is True
        ),
        "read_model_board_api_parity": len(attached_rows) == role_count,
        "low_archived_absent_from_backlog": all(
            (row.get("owner_preference") or {}).get("state")
            not in {"low", "archived"}
            for row in backlog
        ),
        "backlog_requires_explicit_owner_source": all(
            (row.get("owner_preference") or {}).get("source")
            == "user_machine"
            for row in backlog
        ),
        "hiring_defaults_fallback_enforcement": automation.get("ok") is True,
        "api_exposes_preference_and_gate_contracts": all(
            marker in api_source
            for marker in (
                '"/preferences"',
                "set_model_owner_preference",
                "attach_calibration_gates",
            )
        ),
        "ui_exposes_preference_and_gate_contracts": all(
            marker in ui_source
            for marker in (
                "OwnerPreferenceControl",
                "calibration-gate-board",
                "/api/model-catalog/preferences",
            )
        ),
        "existing_assignments_pause_without_silent_replacement": all(
            marker in executor_source
            for marker in (
                "_enforce_archived_assignment",
                "Pause an existing archived assignment without silently replacing it",
                "owner_archived_assignment",
            )
        ),
        "explicit_reactivation_is_normal_and_local": (
            reactivated["state"] == "normal"
            and reactivated["source"] == "user_machine"
        ),
        "audit_is_read_only": (
            read_model == read_model_before
            and preferences == preferences_before
        ),
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "preferences_mutated": False,
            "assignments_mutated": False,
            "secrets_read": False,
            "inference_attempted": False,
            "paths_emitted": False,
            "model_ids_emitted": False,
        },
        "inventory": {
            "candidate_count": inventory["inventory"]["candidate_count"],
            "role_row_count": role_count,
            "explicit_candidate_count": inventory["inventory"][
                "explicit_candidate_count"
            ],
            "pending_candidate_count": inventory["inventory"][
                "pending_candidate_count"
            ],
            "backlog_count": len(backlog),
        },
        "automation": {
            "candidate_checks": automation.get("candidate_checks", 0),
            "default_checks": automation.get("default_checks", 0),
            "fallback_checks": automation.get("fallback_checks", 0),
            "failure_count": automation.get("failure_count", 0),
        },
        "checks": checks,
        "summary": {
            "parity_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("model residual parity audit schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or len(checks) != 10:
        raise ValueError("model residual parity audit coverage drift")
    expected = {
        "parity_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected:
        raise ValueError("model residual parity audit summary drift")


def _coverage(preferences: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    drift = (
        repo_root
        / "benchmarks"
        / "results"
        / "model_catalog_drift"
        / "model-catalog-drift-2026-07-22.json"
    )
    executable: dict[str, set[str]] = {}
    for profile in DEFAULT_ADAPTER_PROFILES:
        profile_id = str(profile.get("id") or "")
        options, _catalog = executable_model_options(profile_id, profile=profile)
        executable[profile_id] = {
            str(option.get("value") or "")
            for option in options
            if model_is_selectable(option)
        }
    return audit_model_evaluation_coverage(
        observed_at=datetime.now().astimezone(),
        observed_versions=_versions_from_drift(drift),
        executable_models_by_profile=executable,
        owner_preferences=preferences,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    repo_root = REPO_ROOT
    preferences = load_model_owner_preferences()
    read_model = build_current_model_catalog_read_model(db_paths=())
    automation = audit_model_automation_enforcement(
        read_model,
        profiles=load_adapter_profiles(),
        options_by_profile=model_options(),
        repo_root=repo_root,
    )
    report = build_audit(
        read_model=read_model,
        preferences=preferences,
        coverage=_coverage(preferences, repo_root),
        automation=automation,
        repo_root=repo_root,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    return 0 if report["summary"]["parity_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
