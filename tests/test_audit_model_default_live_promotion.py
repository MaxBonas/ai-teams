from pathlib import Path

from scripts.audit_model_default_live_promotion import (
    audit_fail_closed_matrix,
    audit_live_promotion,
    build_report,
    _projection,
)


def test_negative_promotion_matrix_fails_closed(tmp_path: Path) -> None:
    report = audit_fail_closed_matrix(tmp_path / "gates.sqlite")

    assert report["all_fail_closed"] is True
    assert {row["case"] for row in report["cases"]} == {
        "adapter_red",
        "incompatibility",
        "price_unknown",
        "quota_pressure",
        "stale",
        "tie",
        "owner_override",
    }


def test_live_shadow_never_auto_applies_even_with_winner(tmp_path: Path) -> None:
    live = audit_live_promotion(
        tmp_path / "shadow.sqlite",
        roles=("reviewer",),
        projection_by_role={"reviewer": _projection(auto_eligible=True)},
    )

    assert live["auto_ready"] is True
    assert live["roles"][0]["winner_candidate_id"]
    assert live["roles"][0]["assignment_changed"] is False
    assert live["roles"][0]["auto_applied"] is False
    assert live["roles"][0]["hash_valid"] is True


def test_report_retains_recommend_when_live_role_has_no_winner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "scripts.audit_model_default_live_promotion.audit_live_promotion",
        lambda _db: {
            "roles": [],
            "blockers": [],
            "gates": {
                "shadow_persisted_per_role": True,
                "snapshot_hashes_valid": True,
                "shadow_never_changed_assignment": True,
                "shadow_never_auto_applied": True,
                "all_roles_have_auto_winner": False,
            },
            "auto_ready": False,
        },
    )

    report = build_report(tmp_path / "live.sqlite")

    assert report["ok"] is True
    assert report["decision"]["recommended_rollout"] == "recommend"
    assert report["decision"]["auto_allowed"] is False
    assert report["decision"]["default_change_allowed"] is False
