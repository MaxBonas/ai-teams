from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from aiteam.model_calibration_gate_board import (
    STAGE_SEQUENCE,
    attach_calibration_gates,
    build_model_calibration_gate_board,
)
from aiteam.model_catalog_read_model import (
    build_current_model_catalog_read_model,
)

SCHEMA_VERSION = "model_calibration_gate_board_audit_v1"
_ADVANCED_ACTIONS = {
    "run_exact_contract_probe",
    "run_exact_role_canary",
    "run_second_independent_family",
    "resolve_promotion_gate",
}


def build_audit(read_model: dict[str, Any]) -> dict[str, Any]:
    source = deepcopy(read_model)
    board = build_model_calibration_gate_board(read_model)
    rows = board["rows"]
    expected_rows = sum(
        len(candidate.get("roles") or ())
        for candidate in read_model.get("candidates") or ()
    )
    identities = {
        (
            row["profile_id"],
            row["model_id"],
            row["canonical_role"],
        )
        for row in rows
    }
    attached = attach_calibration_gates(read_model.get("candidates") or ())
    attached_gates = {
        (
            candidate["identity"]["profile_id"],
            candidate["identity"]["model_id"],
            role["canonical_role"],
        ): role["calibration_gate"]
        for candidate in attached
        for role in candidate.get("roles") or ()
    }
    board_gates = {
        (
            row["profile_id"],
            row["model_id"],
            row["canonical_role"],
        ): _public_gate(row)
        for row in rows
    }
    fixtures = _audit_fixtures()
    fixture_rows = {
        name: build_model_calibration_gate_board(payload)["rows"][0]
        for name, payload in fixtures.items()
    }
    red_rows = [
        row
        for row in rows
        if _gate_status(row, "adapter_health") != "passed"
    ]
    report = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "schema_version": read_model.get("schema_version"),
            "content_hash": read_model.get("content_hash"),
            "candidate_count": len(read_model.get("candidates") or ()),
            "role_row_count": expected_rows,
        },
        "scope": {
            "global_installations_mutated": False,
            "secrets_read": False,
            "inference_attempted": False,
            "paths_emitted": False,
            "live_catalog_identifiers_emitted": False,
        },
        "board": {
            "stage_sequence": list(STAGE_SEQUENCE),
            "row_count": len(rows),
            "identity_count": len(identities),
            "actionable_count": board["counts"]["actionable"],
            "complete_count": board["counts"]["complete"],
            "red_adapter_row_count": len(red_rows),
        },
        "checks": {
            "exact_role_coverage": len(rows) == expected_rows,
            "identities_unique": len(identities) == len(rows),
            "source_immutable": read_model == source,
            "api_attachment_parity": attached_gates == board_gates,
            "red_adapter_cannot_bypass": all(
                row["next_action"] not in _ADVANCED_ACTIONS for row in red_rows
            ),
            "promotion_requires_all_seven": all(
                row["promotion_ready"]
                is all(gate["status"] == "passed" for gate in row["gates"])
                for row in rows
            ),
            "green_unprobed_stops_at_contract": (
                fixture_rows["green_unprobed"]["blocker"]["stage"]
                == "contract_probe"
                and fixture_rows["green_unprobed"]["next_action"]
                == "run_exact_contract_probe"
                and _gate_status(
                    fixture_rows["green_unprobed"],
                    "role_canary",
                )
                == "waiting"
            ),
            "red_historical_remediates_health": (
                fixture_rows["red_historical"]["blocker"]["stage"]
                == "adapter_health"
                and fixture_rows["red_historical"]["next_action"]
                == "remediate_adapter_health"
                and _gate_status(
                    fixture_rows["red_historical"],
                    "role_canary",
                )
                == "historical"
            ),
            "owner_policy_prevents_spend": all(
                fixture_rows[name]["actionable"] is False
                and fixture_rows[name]["blocker"]["stage"]
                == "maintenance_policy"
                for name in ("archived", "low", "manual")
            ),
            "full_evidence_promotes": (
                fixture_rows["complete"]["promotion_ready"] is True
                and all(
                    gate["status"] == "passed"
                    for gate in fixture_rows["complete"]["gates"]
                )
            ),
        },
    }
    report["summary"] = {
        "promotion_ready": all(report["checks"].values()),
        "check_count": len(report["checks"]),
        "passed_count": sum(report["checks"].values()),
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("model calibration gate audit schema drift")
    if report.get("scope") != {
        "global_installations_mutated": False,
        "secrets_read": False,
        "inference_attempted": False,
        "paths_emitted": False,
        "live_catalog_identifiers_emitted": False,
    }:
        raise ValueError("model calibration gate audit scope drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or len(checks) != 10:
        raise ValueError("model calibration gate audit coverage drift")
    summary = report.get("summary")
    if not isinstance(summary, dict) or summary != {
        "promotion_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }:
        raise ValueError("model calibration gate audit summary drift")


def _public_gate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(row[key])
        for key in (
            "candidate_id",
            "profile_id",
            "model_id",
            "canonical_role",
            "owner_preference",
            "maintenance_policy",
            "gates",
            "blocker",
            "owner",
            "next_action",
            "actionable",
            "promotion_ready",
        )
    }


def _gate_status(row: dict[str, Any], stage: str) -> str | None:
    return next(
        (
            str(gate["status"])
            for gate in row["gates"]
            if gate["stage"] == stage
        ),
        None,
    )


def _audit_fixtures() -> dict[str, dict[str, Any]]:
    return {
        "green_unprobed": _fixture(),
        "red_historical": _fixture(
            green=False,
            calibrated=True,
            probe=True,
        ),
        "archived": _fixture(preference="archived"),
        "low": _fixture(preference="low"),
        "manual": _fixture(nominated=False),
        "complete": _fixture(
            calibrated=True,
            probe=True,
            auto_eligible=True,
        ),
    }


def _fixture(
    *,
    green: bool = True,
    calibrated: bool = False,
    probe: bool = False,
    auto_eligible: bool = False,
    preference: str = "normal",
    nominated: bool = True,
) -> dict[str, Any]:
    receipts = ["fixture-receipt"] if calibrated else []
    evaluation_status = "calibrated" if calibrated else "requires_canary"
    hard_gates = {
        "automatic_policy": nominated,
        "compatible": True,
        "privacy": True,
        "tools": True,
        "workspace": True,
        "structured_output": True,
        "case_diversity": True if calibrated else None,
    }
    candidate = {
        "candidate_id": "fixture-candidate",
        "identity": {
            "profile_id": "fixture-profile",
            "model_id": "fixture-model",
        },
        "owner_preference": {
            "state": preference,
            "reason": "audit_fixture",
        },
        "states": {
            "catalogued": {"value": True},
            "configured": {"value": True},
            "adapter_green": {"value": green},
            "model_verified": {"value": True},
        },
        "model_metadata": {
            "probe_receipts": ["fixture-probe"] if probe else [],
        },
        "roles": [
            {
                "canonical_role": "reviewer",
                "compatibility": {
                    "allowed": True,
                    "code": "compatible",
                },
                "automatic_selection": {
                    "eligible_by_policy": nominated,
                },
                "evaluation": {
                    "status": evaluation_status,
                    "evidence_receipts": receipts,
                    "diagnostic_receipts": [],
                    "stale_reasons": [],
                },
                "provenance": {
                    "evaluation_receipts": receipts,
                    "diagnostic_receipts": [],
                },
                "score_inputs": {"hard_gates": hard_gates},
                "score": {
                    "auto_eligible": auto_eligible,
                    "auto_ineligible_reasons": (
                        [] if auto_eligible else ["gate:calibrated:no"]
                    ),
                },
            }
        ],
    }
    return {
        "schema_version": "model_catalog_read_model_v2",
        "content_hash": "fixture-content-hash",
        "candidates": [candidate],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audita el gate adapter → calibración sin inferencias, secretos "
            "ni mutaciones de instalaciones."
        )
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    report = build_audit(build_current_model_catalog_read_model(db_paths=()))
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    return 0 if report["summary"]["promotion_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
