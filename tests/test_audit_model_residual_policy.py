from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.audit_model_residual_policy import build_audit, validate_audit


def _candidate(
    profile_id: str,
    model_id: str,
    *,
    state: str,
    source: str,
) -> dict:
    nominated = state == "high"
    return {
        "candidate_id": f"{profile_id}:{model_id}",
        "identity": {
            "profile_id": profile_id,
            "model_id": model_id,
        },
        "owner_preference": {
            "state": state,
            "reason": "fixture",
            "source": source,
        },
        "states": {
            "catalogued": {"value": True},
            "configured": {"value": False},
            "adapter_green": {"value": False},
            "model_verified": {"value": False},
        },
        "model_metadata": {"probe_receipts": []},
        "roles": [
            {
                "canonical_role": "reviewer",
                "compatibility": {"allowed": False, "code": "fixture_blocked"},
                "automatic_selection": {
                    "eligible_by_policy": nominated,
                },
                "evaluation": {
                    "status": "incompatible",
                    "evidence_receipts": [],
                    "diagnostic_receipts": [],
                    "stale_reasons": [],
                },
                "score_inputs": {
                    "hard_gates": {
                        "automatic_policy": nominated,
                        "compatible": False,
                        "privacy": None,
                        "tools": None,
                        "workspace": None,
                        "structured_output": None,
                        "case_diversity": None,
                    }
                },
                "score": {
                    "auto_eligible": False,
                    "auto_ineligible_reasons": ["fixture_blocked"],
                },
            }
        ],
    }


def _fixture() -> tuple[dict, dict]:
    candidates = [
        _candidate("profile-a", "shared-model", state="high", source="user_machine"),
        _candidate("profile-b", "shared-model", state="normal", source="default"),
        _candidate("profile-b", "low-model", state="low", source="user_machine"),
    ]
    read_model = {
        "schema_version": "model_catalog_read_model_v2",
        "content_hash": "fixture-hash",
        "candidates": candidates,
    }
    preferences = {
        "schema_version": "model_owner_preferences_v1",
        "updated_at": "2026-07-30T12:00:00+00:00",
        "preferences": [
            {
                "profile_id": candidate["identity"]["profile_id"],
                "model_id": candidate["identity"]["model_id"],
                "state": candidate["owner_preference"]["state"],
                "reason": "fixture",
                "updated_at": "2026-07-30T12:00:00+00:00",
            }
            for candidate in (candidates[0], candidates[2])
        ],
    }
    return read_model, preferences


def test_audit_inventory_is_complete_without_claiming_policy_complete() -> None:
    read_model, preferences = _fixture()

    report = build_audit(read_model, preferences)

    assert report["summary"] == {
        "inventory_ready": True,
        "policy_complete": False,
        "pending_candidate_count": 1,
        "check_count": 8,
        "passed_count": 8,
        "next_action": "reconcile_pending_exact_identities_as_owner_low",
    }
    assert report["inventory"]["cross_profile_slug_collision_count"] == 1
    assert report["scope"]["model_ids_emitted"] is False
    assert report["gate_projection"]["actionable_by_classification"].get(
        "pending",
        0,
    ) == 0


def test_audit_validator_fails_closed_on_summary_tampering() -> None:
    read_model, preferences = _fixture()
    report = build_audit(read_model, preferences)
    tampered = deepcopy(report)
    tampered["summary"]["policy_complete"] = True

    with pytest.raises(ValueError, match="summary drift"):
        validate_audit(tampered)
