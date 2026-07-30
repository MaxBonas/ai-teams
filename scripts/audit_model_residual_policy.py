from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from aiteam.model_calibration_gate_board import (
    build_model_calibration_gate_board,
)
from aiteam.model_catalog_read_model import (
    build_current_model_catalog_read_model,
)
from aiteam.model_owner_preferences import (
    load_model_owner_preferences,
    normalize_model_owner_preferences,
)

SCHEMA_VERSION = "model_residual_policy_inventory_v1"


def build_audit(
    read_model: dict[str, Any],
    preference_document: dict[str, Any],
) -> dict[str, Any]:
    preferences = normalize_model_owner_preferences(preference_document)
    candidates = list(read_model.get("candidates") or ())
    board = build_model_calibration_gate_board(read_model)
    preference_identities = {
        (row["profile_id"], row["model_id"])
        for row in preferences["preferences"]
    }
    candidate_identities = [
        (
            str(candidate["identity"]["profile_id"]),
            str(candidate["identity"]["model_id"]),
        )
        for candidate in candidates
    ]
    candidate_identity_set = set(candidate_identities)
    explicit = []
    pending = []
    invalid = []
    pending_by_profile: Counter[str] = Counter()
    candidates_by_state: Counter[str] = Counter()
    for candidate in candidates:
        preference = candidate.get("owner_preference") or {}
        state = str(preference.get("state") or "missing")
        source = str(preference.get("source") or "missing")
        candidates_by_state[state] += 1
        if source == "user_machine":
            explicit.append(candidate)
        elif source == "default":
            pending.append(candidate)
            pending_by_profile[str(candidate["identity"]["profile_id"])] += 1
        else:
            invalid.append(candidate)

    identity_profiles: defaultdict[str, set[str]] = defaultdict(set)
    for profile_id, model_id in candidate_identities:
        identity_profiles[model_id].add(profile_id)
    cross_profile_collisions = sum(
        len(profile_ids) > 1 for profile_ids in identity_profiles.values()
    )

    classification_by_identity = {
        (
            str(candidate["identity"]["profile_id"]),
            str(candidate["identity"]["model_id"]),
        ): (
            "explicit"
            if str((candidate.get("owner_preference") or {}).get("source"))
            == "user_machine"
            else "pending"
        )
        for candidate in candidates
    }
    rows_by_class: Counter[str] = Counter()
    actionable_by_class: Counter[str] = Counter()
    proactive_by_class: Counter[str] = Counter()
    promotion_by_class: Counter[str] = Counter()
    for row in board["rows"]:
        classification = classification_by_identity[
            (str(row["profile_id"]), str(row["model_id"]))
        ]
        rows_by_class[classification] += 1
        actionable_by_class[classification] += row["actionable"] is True
        proactive_by_class[classification] += (
            (row.get("maintenance_policy") or {}).get("allows_proactive") is True
        )
        promotion_by_class[classification] += row["promotion_ready"] is True

    report = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "read_model_schema_version": read_model.get("schema_version"),
            "read_model_content_hash": read_model.get("content_hash"),
            "preference_schema_version": preferences["schema_version"],
        },
        "scope": {
            "global_installations_mutated": False,
            "preferences_mutated": False,
            "secrets_read": False,
            "inference_attempted": False,
            "paths_emitted": False,
            "model_ids_emitted": False,
        },
        "inventory": {
            "candidate_count": len(candidates),
            "unique_identity_count": len(candidate_identity_set),
            "preference_entry_count": len(preferences["preferences"]),
            "explicit_candidate_count": len(explicit),
            "pending_candidate_count": len(pending),
            "invalid_source_count": len(invalid),
            "orphan_preference_count": len(
                preference_identities - candidate_identity_set
            ),
            "cross_profile_slug_collision_count": cross_profile_collisions,
            "candidates_by_state": dict(sorted(candidates_by_state.items())),
            "pending_by_profile": dict(sorted(pending_by_profile.items())),
        },
        "gate_projection": {
            "role_row_count": len(board["rows"]),
            "rows_by_classification": dict(sorted(rows_by_class.items())),
            "actionable_by_classification": dict(
                sorted(actionable_by_class.items())
            ),
            "proactive_by_classification": dict(
                sorted(proactive_by_class.items())
            ),
            "promotion_ready_by_classification": dict(
                sorted(promotion_by_class.items())
            ),
        },
        "checks": {
            "candidate_identities_unique": (
                len(candidate_identities) == len(candidate_identity_set)
            ),
            "classification_total_matches_catalog": (
                len(explicit) + len(pending) + len(invalid) == len(candidates)
            ),
            "preference_entries_resolve_exactly": (
                not (preference_identities - candidate_identity_set)
                and len(explicit) == len(preference_identities)
            ),
            "classification_sources_valid": not invalid,
            "all_candidates_visible_in_gate_board": (
                len(board["rows"])
                == sum(len(candidate.get("roles") or ()) for candidate in candidates)
            ),
            "low_and_archived_not_actionable": all(
                row["actionable"] is False
                for row in board["rows"]
                if row["owner_preference"]["state"] in {"low", "archived"}
            ),
            "pending_safe_before_reconcile": (
                actionable_by_class["pending"] == 0
                and proactive_by_class["pending"] == 0
                and promotion_by_class["pending"] == 0
            ),
            "cross_profile_identity_preserved": all(
                profile_id and model_id
                for profile_id, model_id in candidate_identities
            ),
        },
    }
    report["summary"] = {
        "inventory_ready": all(report["checks"].values()),
        "policy_complete": len(pending) == 0,
        "pending_candidate_count": len(pending),
        "check_count": len(report["checks"]),
        "passed_count": sum(value is True for value in report["checks"].values()),
        "next_action": (
            "none"
            if not pending
            else "reconcile_pending_exact_identities_as_owner_low"
        ),
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("model residual policy audit schema drift")
    if report.get("scope") != {
        "global_installations_mutated": False,
        "preferences_mutated": False,
        "secrets_read": False,
        "inference_attempted": False,
        "paths_emitted": False,
        "model_ids_emitted": False,
    }:
        raise ValueError("model residual policy audit scope drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or len(checks) != 8:
        raise ValueError("model residual policy audit coverage drift")
    inventory = report.get("inventory")
    summary = report.get("summary")
    if not isinstance(inventory, dict) or not isinstance(summary, dict):
        raise ValueError("model residual policy audit summary missing")
    expected = {
        "inventory_ready": all(checks.values()),
        "policy_complete": inventory.get("pending_candidate_count") == 0,
        "pending_candidate_count": inventory.get("pending_candidate_count"),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
        "next_action": (
            "none"
            if inventory.get("pending_candidate_count") == 0
            else "reconcile_pending_exact_identities_as_owner_low"
        ),
    }
    if summary != expected:
        raise ValueError("model residual policy audit summary drift")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inventaría la política residual del catálogo sin mutar "
            "preferencias, instalaciones ni consumir inferencias."
        )
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    report = build_audit(
        build_current_model_catalog_read_model(db_paths=()),
        load_model_owner_preferences(),
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    return 0 if report["summary"]["inventory_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
