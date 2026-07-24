from datetime import datetime, timezone
from pathlib import Path

from aiteam.model_catalog_read_model import build_current_model_catalog_read_model
from aiteam.model_evaluation_coverage import audit_model_evaluation_coverage
from aiteam.model_normalized_metrics import (
    NORMALIZED_METRICS_VERSION,
    normalized_metrics_from_evaluation,
)


def _coverage() -> dict:
    return audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.145.0",
            "antigravity_subscription": "1.1.5",
            "opencode_zen_free": "1.18.4",
            "local_gemma4_ollama": "0.32.1",
            "local_qwen_ollama": "0.32.1",
        },
    )


def test_every_calibrated_pair_gets_exact_quality_and_evidence_metadata() -> None:
    coverage = _coverage()

    report = normalized_metrics_from_evaluation(coverage)

    assert report["schema_version"] == NORMALIZED_METRICS_VERSION
    assert report["pair_count"] == coverage["pair_counts"]["calibrated"] == 25
    assert report["case_diversity_counts"] == {
        "multi_family": 21,
        "single_family": 4,
    }
    assert report["diagnostics"] == []
    for key, metric in report["metrics"].items():
        assert len(key) == 3
        assert metric["components"]["quality"]["value"] == 100
        assert metric["components"]["quality"]["samples_passed"] > 0
        assert metric["evidence"]["status"] == "calibrated"
        assert metric["evidence"]["seeds"] == 3
        assert metric["evidence"]["receipts"]
        assert metric["evidence"]["kind"] in {
            "exact_role_canary",
            "exact_tool_fixture",
        }
        assert metric["evidence"]["case_families"]
        assert metric["normalization"]["scope"] == "exact_profile_model_role"


def test_partial_and_negative_pairs_never_receive_normalized_quality() -> None:
    coverage = _coverage()
    metrics = normalized_metrics_from_evaluation(coverage)["metrics"]

    assert (
        "local_gemma4_ollama",
        "gemma4:26b",
        "engineer",
    ) not in metrics
    assert (
        "antigravity_subscription",
        "gpt-oss-120b-medium",
        "file_scout",
    ) not in metrics
    assert (
        "codex_subscription",
        "gpt-5.6-luna",
        "file_scout",
    ) not in metrics
    assert (
        "antigravity_subscription",
        "gemini-3.5-flash-low",
        "worker",
    ) not in metrics
    assert (
        "codex_subscription",
        "gpt-5.6-luna",
        "context_curator",
    ) in metrics


def test_stale_or_invalid_calibrated_row_fails_closed() -> None:
    coverage = _coverage()
    target = next(
        role
        for model in coverage["rows"]
        for role in model["roles"]
        if role.get("status") == "calibrated"
    )
    target["stale_reasons"] = ["provider_version_changed"]

    report = normalized_metrics_from_evaluation(coverage)

    assert report["pair_count"] == 24
    assert report["diagnostics"][0]["reason"] == (
        "calibrated_row_not_fresh_or_valid"
    )


def test_current_read_model_uses_authoritative_drift_fallback(
    monkeypatch,
) -> None:
    from aiteam.user_config import DEFAULT_ADAPTER_PROFILES

    profiles = [
        {**profile, "health": {"status": "ok"}}
        for profile in DEFAULT_ADAPTER_PROFILES
    ]
    monkeypatch.setattr(
        "aiteam.model_catalog_read_model.load_adapter_profiles",
        lambda: profiles,
    )
    monkeypatch.setattr(
        "aiteam.model_catalog_read_model.profile_is_connected",
        lambda _profile: True,
    )
    monkeypatch.setattr(
        "aiteam.model_catalog_read_model.observed_profile_cli_version",
        lambda _profile: None,
    )

    read_model = build_current_model_catalog_read_model(
        observed_at=datetime(2026, 7, 23, 14, 0, tzinfo=timezone.utc),
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert read_model["normalized_metrics"]["pair_count"] == 25
    known_quality = [
        role
        for candidate in read_model["candidates"]
        for role in candidate["roles"]
        if role["score"]["breakdown"]["quality"]["status"] == "known"
    ]
    assert len(known_quality) == 25
    diversity_gates = [
        role["score_inputs"]["hard_gates"]["case_diversity"]
        for candidate in read_model["candidates"]
        for role in candidate["roles"]
        if role["score"]["breakdown"]["quality"]["status"] == "known"
    ]
    assert diversity_gates.count(True) == 21
    assert diversity_gates.count(False) == 4
    assert read_model["evaluation_version_evidence"][
        "codex_subscription"
    ]["source"].startswith("drift_receipt:")
