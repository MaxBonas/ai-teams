"""Proyecciones puras para la API canónica del catálogo de modelos.

Este módulo no calcula compatibilidad ni scores. Filtra y ordena únicamente el
``model_catalog_read_model_v1`` para que API, Equipo y la futura pestaña Modelos
consuman exactamente los mismos gates y razones.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiteam.model_catalog_projection import MODEL_CATALOG_STATE_NAMES
from aiteam.model_role_scoring import rank_model_role_scores
from aiteam.policies import canonical_role


CATALOG_STATE_NAMES = MODEL_CATALOG_STATE_NAMES


def catalog_selection_reason(role_row: Mapping[str, Any]) -> str:
    """Devuelve una razón estable y accionable, sin reinterpretar los gates."""
    compatibility = role_row.get("compatibility")
    if isinstance(compatibility, Mapping) and compatibility.get("allowed") is False:
        return f"compatibility:{compatibility.get('code') or 'denied'}"
    score = role_row.get("score")
    if not isinstance(score, Mapping):
        return "role_score_missing"
    if score.get("auto_eligible") is True:
        return "auto_eligible_shadow_only"
    reasons = score.get("auto_ineligible_reasons") or ()
    return str(next(iter(reasons), "not_auto_eligible"))


def rank_catalog_candidates_for_role(
    read_model: Mapping[str, Any], role: str
) -> list[dict[str, Any]]:
    """Ordena pares modelo+perfil usando solo ``rank_model_role_scores``."""
    role_key = canonical_role(role)
    by_candidate: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    scores: list[dict[str, Any]] = []
    for raw_candidate in read_model.get("candidates") or ():
        candidate = dict(raw_candidate)
        candidate_id = str(candidate.get("candidate_id") or "")
        role_row = next(
            (
                dict(item)
                for item in candidate.get("roles") or ()
                if str(item.get("canonical_role") or "") == role_key
            ),
            None,
        )
        if not candidate_id or role_row is None:
            continue
        score = role_row.get("score")
        if not isinstance(score, Mapping):
            continue
        by_candidate[candidate_id] = (candidate, role_row)
        scores.append(dict(score))

    result: list[dict[str, Any]] = []
    for rank, score in enumerate(rank_model_role_scores(scores), start=1):
        candidate_id = str(score.get("candidate_id") or "")
        candidate, role_row = by_candidate[candidate_id]
        result.append(
            {
                **candidate,
                "roles": [role_row],
                "canonical_role": role_key,
                "role_evaluation": role_row,
                "rank": rank,
                "selection_reason": catalog_selection_reason(role_row),
            }
        )
    return result


def filter_catalog_candidates(
    read_model: Mapping[str, Any],
    *,
    role: str = "",
    provider: str = "",
    channel: str = "",
    tier: str = "",
    state: str = "",
    configured: bool | None = None,
) -> list[dict[str, Any]]:
    """Filtra sin borrar estados ni convertir unknown en false."""
    candidates = (
        rank_catalog_candidates_for_role(read_model, role)
        if role
        else [dict(item) for item in read_model.get("candidates") or ()]
    )
    provider_key = provider.strip().lower()
    channel_key = channel.strip().lower()
    tier_key = tier.strip().lower()
    state_key = state.strip().lower()
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        identity = candidate.get("identity") or {}
        metadata = candidate.get("model_metadata") or {}
        states = candidate.get("states") or {}
        provider_values = {
            str(identity.get("provider_org") or "").lower(),
            str(identity.get("model_vendor") or "").lower(),
        }
        if provider_key and provider_key not in provider_values:
            continue
        if channel_key and str(identity.get("channel") or "").lower() != channel_key:
            continue
        if tier_key and str(metadata.get("tier") or "").lower() != tier_key:
            continue
        if state_key:
            state_row = states.get(state_key)
            if not isinstance(state_row, Mapping) or state_row.get("value") is not True:
                continue
        if configured is not None:
            configured_row = states.get("configured")
            value = (
                configured_row.get("value")
                if isinstance(configured_row, Mapping)
                else None
            )
            if value is not configured:
                continue
        output.append(candidate)
    return output


def summarize_catalog_providers(
    candidates: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Resume por perfil/canal, preservando la identidad operacional."""
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for candidate in candidates:
        identity = candidate.get("identity") or {}
        key = (
            str(identity.get("profile_id") or ""),
            str(identity.get("provider_org") or "unknown"),
            str(identity.get("channel") or "unknown"),
        )
        row = groups.setdefault(
            key,
            {
                "profile_id": key[0],
                "provider": key[1],
                "channel": key[2],
                "capacity_pool": identity.get("capacity_pool"),
                "model_count": 0,
                "configured_count": 0,
                "green_count": 0,
                "selectable_count": 0,
                "blocked_count": 0,
                "data_policy": (candidate.get("provider_metadata") or {}).get(
                    "data_policy"
                ),
                "privacy_note": (candidate.get("provider_metadata") or {}).get(
                    "privacy_note"
                ),
                "economy_classes": [],
            },
        )
        row["model_count"] += 1
        states = candidate.get("states") or {}
        for state_name, count_name in (
            ("configured", "configured_count"),
            ("adapter_green", "green_count"),
            ("selectable", "selectable_count"),
            ("blocked", "blocked_count"),
        ):
            state_row = states.get(state_name)
            if isinstance(state_row, Mapping) and state_row.get("value") is True:
                row[count_name] += 1
        economy = (candidate.get("model_metadata") or {}).get("economy") or {}
        cost_class = str(economy.get("cost_class") or "").strip()
        if cost_class and cost_class not in row["economy_classes"]:
            row["economy_classes"].append(cost_class)
    for row in groups.values():
        row["economy_classes"].sort()
    return [groups[key] for key in sorted(groups)]
