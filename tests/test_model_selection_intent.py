from __future__ import annotations

from pathlib import Path

import pytest

import aiteam.model_selection_intent as intent_mod
from aiteam.model_selection_intent import normalize_owner_explicit_selection


def _projection(candidate_id: str = "candidate:exact") -> dict:
    return {
        "candidates": [{
            "candidate_id": candidate_id,
            "identity": {
                "profile_id": "profile-a",
                "model_id": "model-a",
            },
        }],
    }


def test_same_pair_inherits_only_a_canonically_bound_owner_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        intent_mod, "contextual_model_selection",
        lambda *args, **kwargs: _projection(),
    )
    existing = {
        "profile_id": "profile-a",
        "model": "model-a",
        "selection_intent": {
            "schema_version": "model_selection_intent_v1",
            "mode": "owner_explicit",
            "source": "model_role_selector",
            "candidate_id": "candidate:exact",
        },
    }

    normalized = normalize_owner_explicit_selection(
        tmp_path / "aiteam.db",
        role="reviewer",
        adapter_config={"profile_id": "profile-a", "model": "model-a"},
        source="agent_update_api",
        existing_config=existing,
    )

    assert normalized["selection_intent"] == existing["selection_intent"]


def test_same_pair_rejects_a_forged_inherited_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        intent_mod, "contextual_model_selection",
        lambda *args, **kwargs: _projection(),
    )
    existing = {
        "profile_id": "profile-a",
        "model": "model-a",
        "selection_intent": {
            "schema_version": "model_selection_intent_v1",
            "mode": "owner_explicit",
            "source": "legacy_or_tampered_row",
            "candidate_id": "candidate:forged",
        },
    }

    with pytest.raises(ValueError, match="candidate_id does not match"):
        normalize_owner_explicit_selection(
            tmp_path / "aiteam.db",
            role="reviewer",
            adapter_config={"profile_id": "profile-a", "model": "model-a"},
            source="agent_update_api",
            existing_config=existing,
        )


def test_owner_boundary_rejects_default_even_for_exact_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        intent_mod, "contextual_model_selection",
        lambda *args, **kwargs: _projection(),
    )

    with pytest.raises(ValueError, match="only accept owner_explicit"):
        normalize_owner_explicit_selection(
            tmp_path / "aiteam.db",
            role="reviewer",
            adapter_config={
                "profile_id": "profile-a",
                "model": "model-a",
                "selection_intent": {
                    "schema_version": "model_selection_intent_v1",
                    "mode": "default",
                    "candidate_id": "candidate:exact",
                },
            },
            source="agent_update_api",
        )
