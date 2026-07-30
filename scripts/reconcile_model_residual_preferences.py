from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from aiteam.model_catalog_read_model import (
    build_current_model_catalog_read_model,
)
from aiteam.model_owner_preferences import (
    append_model_owner_preferences,
    load_model_owner_preferences,
    normalize_model_owner_preferences,
)

SCHEMA_VERSION = "model_residual_preference_reconcile_v1"
REASON = "Prioridad residual baja por directiva explícita del owner 2026-07-24"


def build_plan(
    read_model: dict[str, Any],
    preference_document: dict[str, Any],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    preferences = normalize_model_owner_preferences(preference_document)
    existing = {
        (row["profile_id"], row["model_id"])
        for row in preferences["preferences"]
    }
    additions: list[dict[str, str]] = []
    invalid_default_states = 0
    for candidate in read_model.get("candidates") or ():
        identity = candidate["identity"]
        key = (str(identity["profile_id"]), str(identity["model_id"]))
        preference = candidate.get("owner_preference") or {}
        if str(preference.get("source")) != "default":
            continue
        if str(preference.get("state")) != "normal":
            invalid_default_states += 1
            continue
        if key in existing:
            raise ValueError("default preference collides with explicit identity")
        additions.append(
            {
                "profile_id": key[0],
                "model_id": key[1],
                "state": "low",
                "reason": REASON,
            }
        )
    identities = [
        (row["profile_id"], row["model_id"]) for row in additions
    ]
    states_before = Counter(
        str((candidate.get("owner_preference") or {}).get("state") or "missing")
        for candidate in read_model.get("candidates") or ()
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "secrets_read": False,
            "inference_attempted": False,
            "paths_emitted": False,
            "model_ids_emitted": False,
            "global_installations_mutated": False,
        },
        "plan": {
            "candidate_count": len(read_model.get("candidates") or ()),
            "existing_preference_count": len(preferences["preferences"]),
            "addition_count": len(additions),
            "unique_addition_count": len(set(identities)),
            "invalid_default_state_count": invalid_default_states,
            "states_before": dict(sorted(states_before.items())),
            "target_state": "low",
            "preserve_existing": True,
            "atomic_write": True,
        },
        "checks": {
            "additions_unique": len(identities) == len(set(identities)),
            "additions_absent_from_existing": not (
                set(identities).intersection(existing)
            ),
            "default_states_valid": invalid_default_states == 0,
            "existing_entries_untouched_by_plan": True,
        },
    }
    report["summary"] = {
        "plan_ready": all(report["checks"].values()),
        "addition_count": len(additions),
        "apply_required": bool(additions),
    }
    return additions, report


def apply_plan(
    additions: list[dict[str, str]],
    before_document: dict[str, Any],
) -> dict[str, Any]:
    before = normalize_model_owner_preferences(before_document)
    before_by_identity = {
        (row["profile_id"], row["model_id"]): row
        for row in before["preferences"]
    }
    after = (
        append_model_owner_preferences(additions)
        if additions
        else before
    )
    after_by_identity = {
        (row["profile_id"], row["model_id"]): row
        for row in after["preferences"]
    }
    preserved = all(
        after_by_identity.get(identity) == row
        for identity, row in before_by_identity.items()
    )
    return {
        "applied": bool(additions),
        "added_count": len(additions),
        "before_count": len(before["preferences"]),
        "after_count": len(after["preferences"]),
        "existing_entries_preserved": preserved,
        "all_additions_low": all(
            after_by_identity[(row["profile_id"], row["model_id"])]["state"]
            == "low"
            for row in additions
        ),
        "reversible_via_explicit_owner_preference": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Previsualiza o aplica atómicamente la directiva residual low del "
            "owner sin emitir identidades ni leer secretos."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-owner-directive", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.confirm_owner_directive:
        parser.error("--apply requires --confirm-owner-directive")

    before = load_model_owner_preferences()
    additions, report = build_plan(
        build_current_model_catalog_read_model(db_paths=()),
        before,
    )
    report["application"] = (
        apply_plan(additions, before)
        if args.apply
        else {
            "applied": False,
            "added_count": 0,
            "before_count": len(before["preferences"]),
            "after_count": len(before["preferences"]),
            "existing_entries_preserved": True,
            "all_additions_low": True,
            "reversible_via_explicit_owner_preference": True,
        }
    )
    report["summary"]["application_valid"] = all(
        (
            report["application"]["existing_entries_preserved"],
            report["application"]["all_additions_low"],
            (
                not args.apply
                or report["application"]["added_count"] == len(additions)
            ),
        )
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    return 0 if (
        report["summary"]["plan_ready"]
        and report["summary"]["application_valid"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
