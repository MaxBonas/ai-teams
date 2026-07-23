"""Evaluación shadow y contrato durable de defaults automáticos."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Any, Mapping

from aiteam.db.model_score_snapshots import (
    model_role_score_snapshot_hash_valid,
    persist_model_role_score_snapshot,
)
from aiteam.model_selection_context import contextual_model_selection


DEFAULT_ROLLOUT_VERSION = "model_default_rollout_v1"
MODEL_DEFAULT_ROLLOUT_ENV = "AITEAM_MODEL_DEFAULT_ROLLOUT"
MODEL_DEFAULT_ROLLOUT_MODES = frozenset({"shadow", "recommend", "auto"})


def model_default_rollout_mode(environ: Mapping[str, str] | None = None) -> str:
    """Return the rollout mode, failing closed to shadow on bad input.

    This environment flag is deliberately process-local: changing it back to
    ``shadow`` is an immediate rollback and cannot rewrite existing agents.
    """
    values = os.environ if environ is None else environ
    raw = str(values.get(MODEL_DEFAULT_ROLLOUT_ENV) or "shadow").strip().lower()
    aliases = {"shadow_only": "shadow", "recommend_only": "recommend"}
    mode = aliases.get(raw, raw)
    return mode if mode in MODEL_DEFAULT_ROLLOUT_MODES else "shadow"


def evaluate_shadow_model_default(
    db_path: Path,
    *,
    selection_scope: str,
    role: str,
    issue_id: str = "",
    current_profile_id: str = "",
    current_model: str = "",
    projection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a complete contextual decision without changing an assignment."""
    return evaluate_model_default(
        db_path,
        selection_scope=selection_scope,
        role=role,
        issue_id=issue_id,
        current_profile_id=current_profile_id,
        current_model=current_model,
        projection=projection,
        rollout="shadow",
        new_slot=False,
    )


def evaluate_model_default(
    db_path: Path,
    *,
    selection_scope: str,
    role: str,
    issue_id: str = "",
    current_profile_id: str = "",
    current_model: str = "",
    projection: Mapping[str, Any] | None = None,
    rollout: str | None = None,
    new_slot: bool = False,
) -> dict[str, Any]:
    """Evaluate one governed default decision and persist its complete input.

    ``auto`` may mark a snapshot as applied only for a genuinely new slot with
    no current pair. Recommend and shadow never create assignment authority.
    """
    mode = model_default_rollout_mode(
        {MODEL_DEFAULT_ROLLOUT_ENV: rollout} if rollout is not None else None
    )
    result = dict(projection) if projection is not None else contextual_model_selection(
        Path(db_path), role=role, issue_id=issue_id
    )
    candidates: list[dict[str, Any]] = []
    current_candidate_id: str | None = None
    for raw in result.get("candidates") or ():
        candidate = deepcopy(dict(raw))
        identity = candidate.get("identity") or {}
        is_current = (
            bool(current_profile_id and current_model)
            and str(identity.get("profile_id") or "") == current_profile_id
            and str(identity.get("model_id") or "") == current_model
        )
        candidate["auto_eligible"] = (
            (candidate.get("selection_score") or {}).get("auto_eligible") is True
        )
        candidate["is_current_assignment"] = is_current
        if is_current:
            current_candidate_id = str(candidate.get("candidate_id") or "") or None
        candidates.append(candidate)

    winner_candidate_id = _validated_winner_candidate_id(result, candidates)
    divergence = _divergence(
        winner_candidate_id=winner_candidate_id,
        current_candidate_id=current_candidate_id,
        has_current=bool(current_profile_id and current_model),
    )
    winner = next(
        (item for item in candidates if item.get("candidate_id") == winner_candidate_id),
        None,
    )
    reason = (
        str((winner or {}).get("selection_reason") or "highest_auto_eligible")
        if winner_candidate_id
        else "no_auto_eligible_candidate"
    )
    has_current = bool(current_profile_id and current_model)
    auto_applied = bool(mode == "auto" and new_slot and not has_current and winner_candidate_id)
    snapshot = persist_model_role_score_snapshot(
        Path(db_path),
        selection_scope=selection_scope,
        canonical_role=str(result.get("canonical_role") or role),
        score_version=str(result.get("score_version") or "model_role_score_v2"),
        read_model_version=str(result.get("schema_version") or "model_catalog_read_model_v1"),
        candidates=candidates,
        winner_candidate_id=winner_candidate_id,
        winner_reason=reason,
        auto_applied=auto_applied,
        issue_id=issue_id or None,
    )
    return {
        "rollout_version": DEFAULT_ROLLOUT_VERSION,
        "rollout": {
            "shadow": "shadow_only",
            "recommend": "recommend_only",
            "auto": "auto",
        }[mode],
        "decision": "winner" if winner_candidate_id else "no_winner",
        "divergence": divergence,
        "current_candidate_id": current_candidate_id,
        "winner_candidate_id": winner_candidate_id,
        "winner_reason": reason,
        "snapshot": snapshot,
        "assignment_changed": auto_applied,
    }


def select_model_default_for_new_slot(
    db_path: Path,
    *,
    selection_scope: str,
    role: str,
    issue_id: str = "",
    projection: Mapping[str, Any] | None = None,
    rollout: str | None = None,
    profiles: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return an adapter selection only when governed auto rollout applies.

    Calling this function in shadow/recommend mode still writes the decision
    snapshot, but returns no assignment. A missing winner also fails closed.
    """
    decision = evaluate_model_default(
        Path(db_path),
        selection_scope=selection_scope,
        role=role,
        issue_id=issue_id,
        projection=projection,
        rollout=rollout,
        new_slot=True,
    )
    snapshot = decision["snapshot"]
    if snapshot.get("auto_applied") is not True:
        return None
    config = default_adapter_config_from_snapshot(snapshot)
    profile_id = str(config["profile_id"])
    if profiles is None:
        from aiteam.user_config import load_adapter_profiles

        profile_rows = load_adapter_profiles()
    else:
        profile_rows = profiles
    profile = next(
        (dict(item) for item in profile_rows if str(item.get("id") or "") == profile_id),
        None,
    )
    if profile is None:
        raise ValueError("default winner profile is not configured on this machine")
    adapter_type = str(profile.get("adapter_type") or "").strip()
    if not adapter_type:
        raise ValueError("default winner profile has no adapter_type")
    return {
        "adapter_type": adapter_type,
        "adapter_config": config,
        "adapter_profile_id": profile_id,
        "model": config["model"],
        "rollout_decision": decision,
    }


def default_adapter_config_from_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Build a default assignment only from an auto-applied, valid snapshot."""
    if not model_role_score_snapshot_hash_valid(snapshot):
        raise ValueError("default snapshot hash is invalid")
    if snapshot.get("auto_applied") is not True:
        raise ValueError("shadow snapshot cannot create a default assignment")
    winner_id = str(snapshot.get("winner_candidate_id") or "")
    winner = next(
        (
            item for item in snapshot.get("candidates") or ()
            if str(item.get("candidate_id") or "") == winner_id
        ),
        None,
    )
    if winner is None or winner.get("auto_eligible") is not True:
        raise ValueError("default snapshot has no auto-eligible winner")
    identity = winner.get("identity") or {}
    profile_id = str(identity.get("profile_id") or "").strip()
    model = str(identity.get("model_id") or "").strip()
    if not profile_id or not model:
        raise ValueError("default winner has incomplete operational identity")
    return {
        "profile_id": profile_id,
        "model": model,
        "selection_intent": {
            "schema_version": "model_selection_intent_v1",
            "mode": "default",
            "source": DEFAULT_ROLLOUT_VERSION,
            "candidate_id": winner_id,
            "snapshot_id": snapshot.get("id"),
            "snapshot_hash": snapshot.get("input_hash"),
        },
    }


def _divergence(
    *, winner_candidate_id: str | None, current_candidate_id: str | None, has_current: bool
) -> str:
    if winner_candidate_id is None:
        return "preserve_current_no_winner" if has_current else "require_owner_no_winner"
    if current_candidate_id == winner_candidate_id:
        return "matches_current"
    return "different_from_current" if has_current else "new_slot_winner"


def _validated_winner_candidate_id(
    projection: Mapping[str, Any], candidates: list[Mapping[str, Any]]
) -> str | None:
    """Revalida la recomendación; la proyección nunca es autoridad por sí sola."""
    candidate_id = str(
        (projection.get("default") or {}).get("candidate_id") or ""
    ).strip()
    if not candidate_id:
        return None
    winner = next(
        (
            item
            for item in candidates
            if str(item.get("candidate_id") or "") == candidate_id
        ),
        None,
    )
    if winner is None or winner.get("auto_eligible") is not True:
        return None
    return candidate_id
