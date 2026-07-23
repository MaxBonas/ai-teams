from pathlib import Path

from scripts.audit_opencode_negative_pool import (
    DECLARED_MODELS,
    REJECTED_MODELS,
    build_report,
)


def test_existing_opencode_evidence_closes_without_new_inference() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    report = build_report(
        repo_root=repo_root,
        cli_version="1.18.4",
        discovered_models=DECLARED_MODELS | REJECTED_MODELS,
        observed_at="2026-07-23T00:00:00+02:00",
    )

    assert report["ok"] is True
    assert report["inference_runs"] == 0
    assert report["decision"]["status"] == "closed_by_no_change"
    assert report["decision"]["deepseek_reviewer"] == "partial"
    assert report["decision"]["other_pairs_promoted"] is False
    assert all(len(row["sha256"]) == 64 for row in report["evidence_manifest"])


def test_opencode_closure_reopens_on_transport_signal_change() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    report = build_report(
        repo_root=repo_root,
        cli_version="1.18.5",
        discovered_models=DECLARED_MODELS | REJECTED_MODELS,
        observed_at="2026-07-23T00:00:00+02:00",
    )

    assert report["ok"] is False
    assert report["decision"]["status"] == "reopen"
    assert report["checks"]["cli_version_unchanged"] is False
