from __future__ import annotations

import pytest

from aiteam.provider_cli_update_acceptance import (
    build_provider_cli_update_acceptance,
    validate_provider_cli_update_acceptance,
)


def test_clean_clone_and_updated_checkout_resolve_identical_matrix() -> None:
    report = build_provider_cli_update_acceptance()

    assert report["equivalence"] == {
        "matrix_equal": True,
        "promotion_equal": True,
        "ok": True,
    }
    assert (
        report["sources"]["clean_clone"]["matrix_sha256"]
        == report["sources"]["existing_checkout_after_fast_forward"][
            "matrix_sha256"
        ]
    )
    assert report["sources"]["existing_checkout_after_fast_forward"][
        "pre_update"
    ] == {
        "promotion_ready": False,
        "failure_codes": ["fingerprint_matches", "minimum_version"],
        "upgrade_required": True,
        "duplicate_detected": True,
    }


def test_upgrade_edge_cases_fail_closed_and_optional_absence_passes() -> None:
    report = build_provider_cli_update_acceptance()
    cases = {row["id"]: row for row in report["negative_cases"]}

    assert all(row["blocked_as_expected"] for row in cases.values())
    assert "fingerprint_matches" in cases["duplicate_binary"]["failure_codes"]
    assert "minimum_version" in cases["upgrade_required"]["failure_codes"]
    assert (
        "prerelease_explicit"
        in cases["implicit_prerelease"]["failure_codes"]
    )
    assert report["optional_case"] == {
        "id": "optional_absent",
        "accepted": True,
        "status": "optional_absent",
        "promotion_ready": True,
    }
    assert report["summary"]["promotion_ready"] is True


def test_update_acceptance_validator_rejects_tampering() -> None:
    report = build_provider_cli_update_acceptance()
    report["scope"]["global_installations_mutated"] = True

    with pytest.raises(ValueError, match="scope drift"):
        validate_provider_cli_update_acceptance(report)

    report = build_provider_cli_update_acceptance()
    report["sources"]["clean_clone"]["matrix_sha256"] = "invalid"
    with pytest.raises(ValueError, match="digest invalid"):
        validate_provider_cli_update_acceptance(report)
