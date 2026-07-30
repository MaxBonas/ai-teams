from pathlib import Path

from aiteam.model_tier1_parity import (
    _audit_snapshots,
    _audit_ui_contract,
    _compare_maps,
)
from aiteam.model_tier_coverage import TIER_COVERAGE_POLICY_VERSION


def _authority(lane: str = "lead_ready") -> dict:
    return {
        "policy_version": TIER_COVERAGE_POLICY_VERSION,
        "lane": lane,
        "status": "enabled",
        "enabled": True,
        "reason_code": "exact_role_calibration_verified",
    }


def test_parity_map_reports_exact_surface_divergence() -> None:
    expected = {("candidate:a", "lead"): _authority()}
    actual = {("candidate:a", "lead"): _authority("quorum_ready")}
    failures: list[dict] = []

    _compare_maps(failures, "api", expected, actual)

    assert failures == [
        {
            "surface": "api",
            "code": "tier1_authority_divergence",
            "candidate_id": "candidate:a",
            "role": "lead",
        }
    ]


def test_snapshot_parity_accepts_exact_gate_and_rejects_three_bypasses() -> None:
    failures: list[dict] = []

    report = _audit_snapshots(
        {("candidate:a", "lead"): _authority()},
        repo_root=Path(__file__).resolve().parents[1],
        failures=failures,
    )

    assert report == {"checked_decisions": 4}
    assert failures == []


def test_frontend_contract_consumes_backend_authority_without_inference() -> None:
    failures: list[dict] = []

    report = _audit_ui_contract(
        Path(__file__).resolve().parents[1],
        failures=failures,
    )

    assert report == {"checks": 5, "passed": 5}
    assert failures == []
