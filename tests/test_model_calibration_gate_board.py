from __future__ import annotations

from copy import deepcopy

import pytest

from aiteam.model_calibration_gate_board import (
    STAGE_SEQUENCE,
    attach_calibration_gates,
    build_model_calibration_gate_board,
    validate_model_calibration_gate_board,
)


def _candidate(
    *,
    green: bool = True,
    configured: bool = True,
    verified: bool = True,
    probe: bool = False,
    evaluation_status: str = "requires_canary",
    evaluation_receipts: list[str] | None = None,
    diversity: bool | None = None,
    auto_eligible: bool = False,
    preference: str = "normal",
    nominated: bool = True,
    compatible: bool = True,
    preference_source: str = "user_machine",
) -> dict:
    receipts = list(evaluation_receipts or [])
    hard_gates = {
        "configured": configured,
        "adapter_green": green,
        "model_verified": verified,
        "selectable": True,
        "compatible": compatible,
        "automatic_policy": nominated,
        "calibrated": evaluation_status == "calibrated",
        "fresh": evaluation_status == "calibrated",
        "case_diversity": diversity,
        "privacy": True if compatible else None,
        "tools": True if compatible else None,
        "workspace": True if compatible else None,
        "structured_output": True if compatible else None,
    }
    return {
        "candidate_id": "candidate-1",
        "identity": {
            "profile_id": "profile-1",
            "model_id": "model-1",
        },
        "owner_preference": {
            "state": preference,
            "reason": "fixture",
            "source": preference_source,
        },
        "states": {
            "catalogued": {"value": True},
            "configured": {"value": configured},
            "adapter_green": {"value": green},
            "model_verified": {"value": verified},
        },
        "model_metadata": {
            "probe_receipts": ["probe.json"] if probe else [],
        },
        "roles": [
            {
                "canonical_role": "reviewer",
                "compatibility": {
                    "allowed": compatible,
                    "code": "compatible" if compatible else "role_forbidden",
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


def _row(candidate: dict) -> dict:
    board = build_model_calibration_gate_board(
        {
            "schema_version": "model_catalog_read_model_v2",
            "content_hash": "a" * 64,
            "candidates": [candidate],
        }
    )
    return board["rows"][0]


def test_green_adapter_without_contract_evidence_stops_before_canary() -> None:
    row = _row(_candidate())
    by_stage = {gate["stage"]: gate for gate in row["gates"]}

    assert by_stage["adapter_health"]["status"] == "passed"
    assert by_stage["contract_probe"]["status"] == "pending"
    assert by_stage["role_canary"]["status"] == "waiting"
    assert row["blocker"]["stage"] == "contract_probe"
    assert row["next_action"] == "run_exact_contract_probe"


def test_red_adapter_keeps_calibration_historical_and_opens_remediation() -> None:
    row = _row(
        _candidate(
            green=False,
            evaluation_status="calibrated",
            evaluation_receipts=["calibration.json"],
            diversity=True,
        )
    )
    by_stage = {gate["stage"]: gate for gate in row["gates"]}

    assert row["blocker"]["stage"] == "adapter_health"
    assert row["next_action"] == "remediate_adapter_health"
    assert by_stage["role_canary"]["status"] == "historical"
    assert by_stage["multi_family_calibration"]["status"] == "historical"
    assert row["promotion_ready"] is False


def test_exact_probe_canary_diversity_and_promotion_complete_in_order() -> None:
    row = _row(
        _candidate(
            probe=True,
            evaluation_status="calibrated",
            evaluation_receipts=["calibration.json"],
            diversity=True,
            auto_eligible=True,
        )
    )

    assert [gate["stage"] for gate in row["gates"]] == list(STAGE_SEQUENCE)
    assert {gate["status"] for gate in row["gates"]} == {"passed"}
    assert row["blocker"] is None
    assert row["next_action"] == "none"
    assert row["promotion_ready"] is True


@pytest.mark.parametrize(
    ("preference", "nominated", "action"),
    [
        ("archived", True, "owner_keep_archived"),
        ("low", True, "owner_keep_low_priority"),
        ("normal", False, "owner_decide_manual_or_nominate"),
    ],
)
def test_owner_policy_prevents_proactive_spend(
    preference: str,
    nominated: bool,
    action: str,
) -> None:
    row = _row(
        _candidate(
            preference=preference,
            nominated=nominated,
        )
    )

    assert row["blocker"]["stage"] == "maintenance_policy"
    assert row["owner"] == "project_owner"
    assert row["next_action"] == action
    assert row["actionable"] is False


def test_unclassified_default_preference_never_opens_proactive_work() -> None:
    row = _row(
        _candidate(
            preference="normal",
            preference_source="default",
            nominated=True,
        )
    )

    assert row["blocker"] == {
        "stage": "maintenance_policy",
        "code": "owner_unclassified",
        "owner": "project_owner",
    }
    assert row["next_action"] == "owner_classify_before_maintenance"
    assert row["actionable"] is False


def test_api_attachment_adds_gate_without_mutating_source() -> None:
    candidate = _candidate()
    original = deepcopy(candidate)

    attached = attach_calibration_gates([candidate])

    assert candidate == original
    assert attached[0]["roles"][0]["calibration_gate"]["next_action"] == (
        "run_exact_contract_probe"
    )


def test_board_validator_rejects_sequence_tampering() -> None:
    board = build_model_calibration_gate_board(
        {
            "schema_version": "model_catalog_read_model_v2",
            "content_hash": "a" * 64,
            "candidates": [_candidate()],
        }
    )
    board["rows"][0]["gates"].reverse()

    with pytest.raises(ValueError, match="row sequence drift"):
        validate_model_calibration_gate_board(board)


def test_board_validator_rejects_promotion_and_count_tampering() -> None:
    board = build_model_calibration_gate_board(
        {
            "schema_version": "model_catalog_read_model_v2",
            "content_hash": "a" * 64,
            "candidates": [
                _candidate(
                    probe=True,
                    evaluation_status="calibrated",
                    evaluation_receipts=["calibration.json"],
                    diversity=True,
                    auto_eligible=True,
                )
            ],
        }
    )
    promotion_tampered = deepcopy(board)
    promotion_tampered["rows"][0]["gates"][-1]["status"] = "waiting"
    with pytest.raises(ValueError, match="promotion summary drift"):
        validate_model_calibration_gate_board(promotion_tampered)

    count_tampered = deepcopy(board)
    count_tampered["counts"]["complete"] = 0
    with pytest.raises(ValueError, match="counts drift"):
        validate_model_calibration_gate_board(count_tampered)
