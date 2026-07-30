from __future__ import annotations

from aiteam.model_owner_preferences import normalize_model_owner_preferences
from scripts.reconcile_model_residual_preferences import build_plan
from tests.test_audit_model_residual_policy import _fixture


def test_plan_adds_only_default_identity_as_low() -> None:
    read_model, preferences = _fixture()

    additions, report = build_plan(read_model, preferences)

    assert additions == [
        {
            "profile_id": "profile-b",
            "model_id": "shared-model",
            "state": "low",
            "reason": (
                "Prioridad residual baja por directiva explícita del owner "
                "2026-07-24"
            ),
        }
    ]
    assert report["summary"] == {
        "plan_ready": True,
        "addition_count": 1,
        "apply_required": True,
    }
    assert normalize_model_owner_preferences(preferences) == preferences


def test_plan_is_idempotent_after_reconciliation() -> None:
    read_model, preferences = _fixture()
    pending = read_model["candidates"][1]
    pending["owner_preference"] = {
        "state": "low",
        "reason": "reconciled",
        "source": "user_machine",
    }
    preferences["preferences"].append(
        {
            "profile_id": "profile-b",
            "model_id": "shared-model",
            "state": "low",
            "reason": "reconciled",
            "updated_at": "2026-07-30T12:00:00+00:00",
        }
    )

    additions, report = build_plan(read_model, preferences)

    assert additions == []
    assert report["summary"]["apply_required"] is False
