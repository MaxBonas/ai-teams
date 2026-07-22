"""Evaluación shadow y contrato durable de defaults automáticos."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from aiteam.db.model_score_snapshots import (
    model_role_score_snapshot_hash_valid,
    persist_model_role_score_snapshot,
)
from aiteam.model_selection_context import contextual_model_selection


DEFAULT_ROLLOUT_VERSION = "model_default_rollout_v1"


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

    winner_candidate_id = str((result.get("default") or {}).get("candidate_id") or "") or None
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
    snapshot = persist_model_role_score_snapshot(
        Path(db_path),
        selection_scope=selection_scope,
        canonical_role=str(result.get("canonical_role") or role),
        score_version=str(result.get("score_version") or "model_role_score_v1"),
        read_model_version=str(result.get("schema_version") or "model_catalog_read_model_v1"),
        candidates=candidates,
        winner_candidate_id=winner_candidate_id,
        winner_reason=reason,
        auto_applied=False,
        issue_id=issue_id or None,
    )
    return {
        "rollout_version": DEFAULT_ROLLOUT_VERSION,
        "rollout": "shadow_only",
        "decision": "winner" if winner_candidate_id else "no_winner",
        "divergence": divergence,
        "current_candidate_id": current_candidate_id,
        "winner_candidate_id": winner_candidate_id,
        "winner_reason": reason,
        "snapshot": snapshot,
        "assignment_changed": False,
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
