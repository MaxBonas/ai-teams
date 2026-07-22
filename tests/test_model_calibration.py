from __future__ import annotations

from datetime import datetime, timezone

from aiteam.model_calibration import (
    PROMOTED_MODEL_CALIBRATIONS,
    audit_promoted_model_calibrations,
    model_promotion_allowed,
)
from aiteam.user_config import MODEL_OPTIONS_BY_PROFILE


def _versions() -> dict[str, str]:
    return {
        "codex_subscription": "0.128.0",
        "antigravity_subscription": "1.1.5",
    }


def test_promoted_pairs_are_exact_catalog_roles_with_existing_receipts() -> None:
    report = audit_promoted_model_calibrations(
        observed_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        observed_versions=_versions(),
    )

    assert report["registry_valid"] is True
    assert report["all_fresh"] is True
    assert len(report["entries"]) == len(PROMOTED_MODEL_CALIBRATIONS) == 3
    for entry in report["entries"]:
        option = next(
            item
            for item in MODEL_OPTIONS_BY_PROFILE[entry["profile_id"]]
            if item["value"] == entry["model"]
        )
        assert entry["role"] in option["best_for"]
        assert entry["missing_evidence_receipts"] == []
        assert entry["existing_default_action"] == "unchanged"


def test_age_marks_calibration_stale_without_changing_existing_default() -> None:
    report = audit_promoted_model_calibrations(
        observed_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        observed_versions=_versions(),
    )

    sonnet = next(
        entry for entry in report["entries"] if entry["role"] == "engineer"
    )
    assert sonnet["status"] == "stale"
    assert sonnet["stale_reasons"] == ["calibration_age_exceeded"]
    assert sonnet["new_promotion_allowed"] is False
    assert sonnet["existing_default_action"] == "unchanged"


def test_calibration_age_boundary_is_fresh_through_day_thirty() -> None:
    day_thirty = audit_promoted_model_calibrations(
        observed_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        observed_versions=_versions(),
    )
    day_thirty_one = audit_promoted_model_calibrations(
        observed_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        observed_versions=_versions(),
    )

    gpt_day_thirty = next(
        entry for entry in day_thirty["entries"] if entry["model"] == "gpt-5.5"
    )
    gpt_day_thirty_one = next(
        entry for entry in day_thirty_one["entries"] if entry["model"] == "gpt-5.5"
    )
    assert gpt_day_thirty["age_days"] == 30
    assert gpt_day_thirty["status"] == "fresh"
    assert gpt_day_thirty_one["age_days"] == 31
    assert gpt_day_thirty_one["stale_reasons"] == ["calibration_age_exceeded"]


def test_provider_version_change_marks_only_matching_profile_stale() -> None:
    versions = _versions()
    versions["antigravity_subscription"] = "1.2.0"
    report = audit_promoted_model_calibrations(
        observed_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        observed_versions=versions,
    )

    by_profile = {
        profile: [entry for entry in report["entries"] if entry["profile_id"] == profile]
        for profile in versions
    }
    assert all(entry["status"] == "stale" for entry in by_profile["antigravity_subscription"])
    assert all(
        entry["stale_reasons"] == ["provider_version_changed"]
        for entry in by_profile["antigravity_subscription"]
    )
    assert all(entry["status"] == "fresh" for entry in by_profile["codex_subscription"])


def test_new_or_unobserved_promotion_fails_closed() -> None:
    observed_at = datetime(2026, 7, 22, tzinfo=timezone.utc)

    assert model_promotion_allowed(
        "antigravity_subscription",
        "claude-sonnet-4-6",
        "engineer",
        observed_at=observed_at,
        observed_version="1.1.5",
    )
    assert not model_promotion_allowed(
        "antigravity_subscription",
        "claude-sonnet-4-6",
        "engineer",
        observed_at=observed_at,
        observed_version=None,
    )
    assert not model_promotion_allowed(
        "antigravity_subscription",
        "gemini-3.6-flash-medium",
        "reviewer",
        observed_at=observed_at,
        observed_version="1.1.5",
    )


def test_missing_receipts_invalidate_registry_without_touching_defaults(
    tmp_path,
) -> None:
    report = audit_promoted_model_calibrations(
        observed_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        observed_versions=_versions(),
        repo_root=tmp_path,
    )

    assert report["registry_valid"] is False
    assert report["registered_promotions_fresh"] is False
    assert report["unregistered_promotions_allowed"] is False
    assert all(entry["existing_default_action"] == "unchanged" for entry in report["entries"])
    assert all("evidence_receipt_missing" in entry["stale_reasons"] for entry in report["entries"])
