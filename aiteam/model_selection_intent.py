"""Contrato durable para elecciones explícitas de modelo + adapter."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from aiteam.model_selection_context import contextual_model_selection


SELECTION_INTENT_VERSION = "model_selection_intent_v1"
OWNER_EXPLICIT = "owner_explicit"


def normalize_owner_explicit_selection(
    db_path: Path,
    *,
    role: str,
    adapter_config: Mapping[str, Any],
    issue_id: str = "",
    source: str,
    existing_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind owner intent to the canonical candidate for the exact saved pair.

    A same-pair partial/legacy update inherits an existing explicit intent.
    Pair changes and accepted proposals receive a canonical marker. Public owner
    flows cannot claim ``default`` before M.7 has a reproducible auto winner.
    """
    config = dict(adapter_config)
    profile_id = str(config.get("profile_id") or "").strip()
    model = str(config.get("model") or "").strip()
    if not profile_id or not model:
        return config

    old = dict(existing_config or {})
    incoming_intent = config.get("selection_intent")
    old_intent = old.get("selection_intent")
    same_pair = (
        str(old.get("profile_id") or "").strip(),
        str(old.get("model") or "").strip(),
    ) == (profile_id, model)
    if incoming_intent is None and same_pair and _is_owner_explicit(old_intent):
        # Inherit the owner's source, but re-bind the stored candidate below.
        # Rows created by an older client or manual DB edits must not bypass the
        # exact profile+model identity check merely because the pair is stable.
        incoming_intent = dict(old_intent)

    projection = contextual_model_selection(
        Path(db_path),
        role=role,
        issue_id=issue_id,
    )
    candidate = next(
        (
            item for item in projection.get("candidates") or ()
            if str((item.get("identity") or {}).get("profile_id") or "") == profile_id
            and str((item.get("identity") or {}).get("model_id") or "") == model
        ),
        None,
    )
    if candidate is None:
        raise ValueError(
            f"model selection ({profile_id!r}, {model!r}) has no canonical candidate"
        )
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    if not candidate_id:
        raise ValueError("canonical model candidate has no candidate_id")

    if incoming_intent is not None:
        if not isinstance(incoming_intent, Mapping):
            raise ValueError("selection_intent must be an object")
        schema_version = str(incoming_intent.get("schema_version") or "")
        mode = str(incoming_intent.get("mode") or "")
        claimed_candidate = str(incoming_intent.get("candidate_id") or "")
        if schema_version != SELECTION_INTENT_VERSION:
            raise ValueError("unsupported model selection intent schema")
        if mode != OWNER_EXPLICIT:
            raise ValueError("owner APIs only accept owner_explicit model selection intent")
        if claimed_candidate != candidate_id:
            raise ValueError("selection_intent candidate_id does not match profile_id + model")
        intent_source = str(incoming_intent.get("source") or source).strip() or source
    else:
        intent_source = source

    config["selection_intent"] = {
        "schema_version": SELECTION_INTENT_VERSION,
        "mode": OWNER_EXPLICIT,
        "source": intent_source,
        "candidate_id": candidate_id,
    }
    return config


def _is_owner_explicit(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("schema_version") == SELECTION_INTENT_VERSION
        and value.get("mode") == OWNER_EXPLICIT
        and bool(str(value.get("candidate_id") or "").strip())
    )
