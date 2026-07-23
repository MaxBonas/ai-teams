import sqlite3
from pathlib import Path

import pytest

from aiteam.db.model_score_snapshots import persist_model_role_score_snapshot
from aiteam.model_default_rollout import (
    default_adapter_config_from_snapshot,
    evaluate_model_default,
    evaluate_shadow_model_default,
    model_default_rollout_mode,
    select_model_default_for_new_slot,
)


def _projection(*, winner: str | None = "candidate:a") -> dict:
    candidates = [
        {
            "candidate_id": "candidate:a",
            "identity": {"profile_id": "profile-a", "model_id": "model-a"},
            "rank": 1,
            "selection_reason": "score_delta:8",
            "selection_score": {
                "score_version": "model_role_score_v1",
                "score": 90,
                "auto_eligible": winner == "candidate:a",
                "hard_gates": {"calibrated": {"passed": winner == "candidate:a"}},
            },
            "capacity_evidence": {"state": "available", "source": "fixture"},
        },
        {
            "candidate_id": "candidate:b",
            "identity": {"profile_id": "profile-b", "model_id": "model-b"},
            "rank": 2,
            "selection_reason": "lower_score",
            "selection_score": {
                "score_version": "model_role_score_v1",
                "score": 82,
                "auto_eligible": False,
                "hard_gates": {"calibrated": {"passed": False}},
            },
            "capacity_evidence": {"state": "capacity_unknown", "source": "fixture"},
        },
    ]
    return {
        "schema_version": "model_catalog_read_model_v1",
        "score_version": "model_role_score_v1",
        "canonical_role": "reviewer",
        "default": {"candidate_id": winner},
        "candidates": candidates,
    }


def _db(tmp_path: Path) -> Path:
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(Path("aiteam/db/schema.sql").read_text(encoding="utf-8"))
    return db_path


def test_shadow_default_persists_complete_idempotent_divergence_without_mutation(
    tmp_path: Path,
) -> None:
    db_path = _db(tmp_path)
    kwargs = {
        "db_path": db_path,
        "selection_scope": "hiring:reviewer",
        "role": "reviewer",
        "current_profile_id": "profile-b",
        "current_model": "model-b",
        "projection": _projection(),
    }

    first = evaluate_shadow_model_default(**kwargs)
    second = evaluate_shadow_model_default(**kwargs)

    assert first["decision"] == "winner"
    assert first["divergence"] == "different_from_current"
    assert first["assignment_changed"] is False
    assert first["snapshot"]["id"] == second["snapshot"]["id"]
    assert first["snapshot"]["hash_valid"] is True
    assert first["snapshot"]["auto_applied"] is False
    assert len(first["snapshot"]["candidates"]) == 2
    current = next(
        item for item in first["snapshot"]["candidates"] if item["is_current_assignment"]
    )
    assert current["candidate_id"] == "candidate:b"
    assert current["capacity_evidence"]["state"] == "capacity_unknown"

    with pytest.raises(ValueError, match="shadow snapshot"):
        default_adapter_config_from_snapshot(first["snapshot"])


@pytest.mark.parametrize(
    ("has_current", "expected"),
    [(True, "preserve_current_no_winner"), (False, "require_owner_no_winner")],
)
def test_shadow_no_winner_never_invents_fallback(
    tmp_path: Path, has_current: bool, expected: str
) -> None:
    decision = evaluate_shadow_model_default(
        _db(tmp_path),
        selection_scope=f"scope:{has_current}",
        role="reviewer",
        current_profile_id="profile-b" if has_current else "",
        current_model="model-b" if has_current else "",
        projection=_projection(winner=None),
    )

    assert decision["decision"] == "no_winner"
    assert decision["winner_candidate_id"] is None
    assert decision["divergence"] == expected
    assert decision["assignment_changed"] is False


def test_default_intent_requires_hash_valid_auto_applied_eligible_snapshot(
    tmp_path: Path,
) -> None:
    db_path = _db(tmp_path)
    candidate = {
        "candidate_id": "candidate:a",
        "identity": {"profile_id": "profile-a", "model_id": "model-a"},
        "auto_eligible": True,
    }
    snapshot = persist_model_role_score_snapshot(
        db_path,
        selection_scope="apply:reviewer",
        canonical_role="reviewer",
        score_version="model_role_score_v1",
        read_model_version="model_catalog_read_model_v1",
        candidates=[candidate],
        winner_candidate_id="candidate:a",
        winner_reason="highest_auto_eligible",
        auto_applied=True,
    )

    config = default_adapter_config_from_snapshot(snapshot)

    assert config["profile_id"] == "profile-a"
    assert config["model"] == "model-a"
    assert config["selection_intent"] == {
        "schema_version": "model_selection_intent_v1",
        "mode": "default",
        "source": "model_default_rollout_v1",
        "candidate_id": "candidate:a",
        "snapshot_id": snapshot["id"],
        "snapshot_hash": snapshot["input_hash"],
    }

    # El booleano cacheado no es autoridad: se recalcula el sello completo.
    assert default_adapter_config_from_snapshot(
        {**snapshot, "hash_valid": False}
    )["model"] == "model-a"
    tampered = {**snapshot, "candidates": [{**candidate, "auto_eligible": False}]}
    with pytest.raises(ValueError, match="hash"):
        default_adapter_config_from_snapshot(tampered)


def test_rollout_flag_is_fail_closed_and_supports_immediate_rollback() -> None:
    assert model_default_rollout_mode({}) == "shadow"
    assert model_default_rollout_mode({"AITEAM_MODEL_DEFAULT_ROLLOUT": "recommend"}) == "recommend"
    assert model_default_rollout_mode({"AITEAM_MODEL_DEFAULT_ROLLOUT": "auto"}) == "auto"
    assert model_default_rollout_mode({"AITEAM_MODEL_DEFAULT_ROLLOUT": "unsafe"}) == "shadow"
    assert model_default_rollout_mode({"AITEAM_MODEL_DEFAULT_ROLLOUT": "shadow"}) == "shadow"


def test_recommend_persists_winner_without_assignment_authority(tmp_path: Path) -> None:
    decision = evaluate_model_default(
        _db(tmp_path),
        selection_scope="recommend:reviewer",
        role="reviewer",
        projection=_projection(),
        rollout="recommend",
        new_slot=True,
    )

    assert decision["rollout"] == "recommend_only"
    assert decision["winner_candidate_id"] == "candidate:a"
    assert decision["snapshot"]["auto_applied"] is False
    assert decision["assignment_changed"] is False


def test_auto_applies_only_to_new_unpinned_slot(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    applied = evaluate_model_default(
        db_path,
        selection_scope="auto:new:reviewer",
        role="reviewer",
        projection=_projection(),
        rollout="auto",
        new_slot=True,
    )
    existing = evaluate_model_default(
        db_path,
        selection_scope="auto:existing:reviewer",
        role="reviewer",
        current_profile_id="profile-b",
        current_model="model-b",
        projection=_projection(),
        rollout="auto",
        new_slot=True,
    )

    assert applied["snapshot"]["auto_applied"] is True
    assert applied["assignment_changed"] is True
    assert existing["snapshot"]["auto_applied"] is False
    assert existing["assignment_changed"] is False


def test_rollout_revalidates_projected_winner_and_fails_closed(tmp_path: Path) -> None:
    projection = _projection()
    projection["candidates"][0]["selection_score"]["auto_eligible"] = False

    decision = evaluate_model_default(
        _db(tmp_path),
        selection_scope="auto:forged:reviewer",
        role="reviewer",
        projection=projection,
        rollout="auto",
        new_slot=True,
    )

    assert decision["decision"] == "no_winner"
    assert decision["winner_candidate_id"] is None
    assert decision["snapshot"]["auto_applied"] is False
    assert decision["assignment_changed"] is False


def test_new_slot_selection_materializes_profile_and_durable_default_intent(
    tmp_path: Path,
) -> None:
    selected = select_model_default_for_new_slot(
        _db(tmp_path),
        selection_scope="auto:slot:reviewer",
        role="reviewer",
        projection=_projection(),
        rollout="auto",
        profiles=[{"id": "profile-a", "adapter_type": "subscription_cli"}],
    )

    assert selected is not None
    assert selected["adapter_type"] == "subscription_cli"
    assert selected["adapter_config"]["profile_id"] == "profile-a"
    assert selected["adapter_config"]["model"] == "model-a"
    assert selected["adapter_config"]["selection_intent"]["mode"] == "default"
    assert selected["rollout_decision"]["snapshot"]["auto_applied"] is True


@pytest.mark.parametrize("rollout", ["shadow", "recommend", "auto"])
def test_new_slot_without_winner_never_invents_assignment(
    tmp_path: Path, rollout: str
) -> None:
    assert select_model_default_for_new_slot(
        _db(tmp_path),
        selection_scope=f"{rollout}:empty:reviewer",
        role="reviewer",
        projection=_projection(winner=None),
        rollout=rollout,
        profiles=[{"id": "profile-a", "adapter_type": "subscription_cli"}],
    ) is None
