import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.audit_model_catalog_drift import build_report, compare_catalog


def test_compare_catalog_accepts_explicit_non_product_dispositions() -> None:
    row = compare_catalog(
        profile_id="opencode_zen_free",
        source="opencode models opencode",
        cli_version="1.18.4",
        declared=["opencode/a-free"],
        discovered=[
            "opencode/a-free",
            "opencode/big-pickle",
            "opencode/pending-free",
        ],
        excluded={
            "opencode/big-pickle": {
                "disposition": "rejected",
                "reason": "opaque",
            },
            "opencode/pending-free": {
                "disposition": "pending_calibration",
                "reason": "needs durable canary",
            },
        },
    )

    assert row["coverage_ok"] is True
    assert (
        row["excluded_discovered"]["opencode/big-pickle"]["disposition"] == "rejected"
    )
    assert (
        row["excluded_discovered"]["opencode/pending-free"]["disposition"]
        == "pending_calibration"
    )


def test_compare_catalog_reports_missing_unexpected_and_duplicates() -> None:
    row = compare_catalog(
        profile_id="antigravity_subscription",
        source="agy models",
        cli_version="1.1.5",
        declared=["model-a", "model-b"],
        discovered=["model-a", "model-c", "model-c"],
    )

    assert row["coverage_ok"] is False
    assert row["missing_declared"] == ["model-b"]
    assert row["unexpected_discovered"] == ["model-c"]
    assert row["duplicate_discovered"] == ["model-c"]


def test_report_separates_codex_blocker_from_completed_drift_audit() -> None:
    report = build_report(
        catalog_rows=[
            {
                "profile_id": "antigravity_subscription",
                "status": "current",
                "cli_version": "1.1.5",
                "coverage_ok": True,
            }
        ],
        flow_report={
            "ok": True,
            "profile_count": 12,
            "model_count": 47,
            "positive_cell_count": 334,
            "negative_cell_count": 402,
            "failures": [],
        },
        codex_catalog={
            "status": "cli_update_required",
            "reason": "catalog_requires_newer_cli",
            "installed_version": "0.128.0",
        },
        observed_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    assert report["ok"] is True
    assert report["promotion_allowed"] is False
    assert report["attention_required"] == [
        {
            "profile_id": "codex_subscription",
            "reason": "catalog_requires_newer_cli",
        }
    ]
    assert report["policy"]["next_review_due"] == "2026-08-19"
    assert report["policy"]["calibration_next_review_due"] == "2026-08-19"
    assert report["gates"]["promoted_model_calibration_registry"] is True
    assert report["gates"]["promoted_model_calibrations_fresh"] is True
    assert report["model_calibration_freshness"]["registered_promotions_fresh"] is True
    assert report["model_calibration_freshness"]["unregistered_promotions_allowed"] is False


def test_report_opens_attention_when_promoted_calibration_is_stale() -> None:
    report = build_report(
        catalog_rows=[
            {
                "profile_id": "antigravity_subscription",
                "status": "current",
                "cli_version": "1.2.0",
                "coverage_ok": True,
            }
        ],
        flow_report={"ok": True},
        codex_catalog={"status": "current", "installed_version": "0.128.0"},
        observed_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    assert report["ok"] is False
    assert report["gates"]["promoted_model_calibrations_fresh"] is False
    assert {
        (item.get("model"), item.get("role"), item["reason"])
        for item in report["attention_required"]
    } == {
        ("claude-sonnet-4-6", "engineer", "model_calibration_stale"),
        ("claude-sonnet-4-6", "software_engineer", "model_calibration_stale"),
    }


def test_versioned_drift_receipt_contains_fresh_exact_calibrations() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "results"
        / "model_catalog_drift"
        / "model-catalog-drift-2026-07-22.json"
    )
    report = json.loads(path.read_text(encoding="utf-8"))

    assert all(report["gates"].values())
    freshness = report["model_calibration_freshness"]
    assert freshness["registry_valid"] is True
    assert freshness["registered_promotions_fresh"] is True
    assert freshness["unregistered_promotions_allowed"] is False
    assert freshness["existing_defaults_changed"] is False
    assert len(freshness["entries"]) == 3
