from __future__ import annotations

from pathlib import Path

import pytest

import aiteam.model_selection_intent as intent_mod
from aiteam.model_selection_intent import normalize_owner_explicit_selection


def _projection(candidate_id: str = "candidate:exact", *, archived: bool = False) -> dict:
    return {
        "candidates": [{
            "candidate_id": candidate_id,
            "identity": {
                "profile_id": "profile-a",
                "model_id": "model-a",
            },
            "owner_preference": {
                "state": "archived" if archived else "normal",
                "reason": "fixture",
            },
            "owner_selectable": not archived,
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


def test_owner_boundaries_reject_archived_candidate_for_onboarding_team_and_hiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        intent_mod,
        "contextual_model_selection",
        lambda *args, **kwargs: _projection(archived=True),
    )

    for source in (
        "onboarding_model_role_selector",
        "agent_update_api",
        "accepted_team_proposal",
    ):
        with pytest.raises(ValueError, match="archived by the owner"):
            normalize_owner_explicit_selection(
                tmp_path / "aiteam.db",
                role="reviewer",
                adapter_config={
                    "profile_id": "profile-a",
                    "model": "model-a",
                    "selection_intent": {
                        "schema_version": "model_selection_intent_v1",
                        "mode": "owner_explicit",
                        "candidate_id": "candidate:exact",
                    },
                },
                source=source,
            )


def test_owner_override_cannot_skip_exact_tier1_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projection = _projection()
    projection["candidates"][0]["owner_selectable"] = False
    projection["candidates"][0]["tier1_authority_gate"] = {
        "applicable": True,
        "allowed": False,
        "code": "tier1_authority_lane_mismatch",
        "reason": "Lead exige lead_ready.",
    }
    monkeypatch.setattr(
        intent_mod,
        "contextual_model_selection",
        lambda *args, **kwargs: projection,
    )

    with pytest.raises(ValueError, match="lacks exact Tier 1 authority"):
        normalize_owner_explicit_selection(
            tmp_path / "aiteam.db",
            role="lead",
            adapter_config={
                "profile_id": "profile-a",
                "model": "model-a",
                "selection_intent": {
                    "schema_version": "model_selection_intent_v1",
                    "mode": "owner_explicit",
                    "candidate_id": "candidate:exact",
                },
            },
            source="agent_update_api",
        )
