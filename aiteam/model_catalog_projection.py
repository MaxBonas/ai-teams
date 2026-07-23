"""Contrato puro de identidad y estados del catálogo universal de modelos.

M.1 no puntúa ni selecciona. Enumera candidatos operativos sin fusionar canales
y conserva cada estado como evidencia independiente. M.2 añadirá el score por
rol; M.3 conectará esta proyección con SQLite y los recibos durables.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from aiteam.policies import canonical_role
from aiteam.provider_identity import profile_identity


MODEL_CATALOG_IDENTITY_SCHEMA_VERSION = "model_catalog_identity_v1"
MODEL_CATALOG_STATE_NAMES = (
    "catalogued",
    "configured",
    "adapter_green",
    "model_verified",
    "selectable",
    "compatible",
    "calibrated",
    "stale",
    "manual_only",
    "blocked",
    "retired",
)

_BLOCKED_PROFILE_STATES = frozenset(
    {"blocked", "blocked_by_provider", "disabled", "retired"}
)
_BLOCKED_MODEL_STATES = frozenset(
    {"blocked", "disabled", "unavailable", "rate_limited"}
)
_OPTION_SOURCE_PRIORITY = {
    "historical_run": 0,
    "declared_catalog": 10,
    "configured_profile": 20,
    "authenticated_discovery": 30,
}


def build_model_catalog_identity_projection(
    *,
    profiles: Iterable[Mapping[str, Any]],
    declared_options_by_profile: Mapping[str, Iterable[Mapping[str, Any]]]
    | None = None,
    discovered_models: Iterable[Mapping[str, Any]] = (),
    historical_models: Iterable[Mapping[str, Any]] = (),
    observed_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Construye el inventario M.1 sin consultar filesystem, DB, secrets o red.

    ``discovered_models`` y ``historical_models`` aceptan filas con
    ``profile_id``, ``model`` y, si el perfil ya no existe, ``provider`` y
    ``channel``. Discovery solo cataloga: requiere ``verified=True`` explícito
    para demostrar ejecución. Los estados que dependen de rol/evidencia quedan
    ``value=None`` hasta que M.2/M.3 aporten ese contexto.
    """
    timestamp = _iso_timestamp(observed_at)
    profile_rows: dict[str, dict[str, Any]] = {}
    for profile in profiles:
        profile_id = str(profile.get("id") or "").strip()
        if not profile_id:
            continue
        normalized = dict(profile)
        if profile_id in profile_rows and profile_rows[profile_id] != normalized:
            raise ValueError(f"conflicting profile identity for {profile_id!r}")
        profile_rows[profile_id] = normalized
    candidates: dict[tuple[str, str], dict[str, Any]] = {}

    declared = declared_options_by_profile or {}
    for profile_id, profile in profile_rows.items():
        options: list[tuple[Mapping[str, Any], str]] = []
        options.extend(
            (item, "declared_catalog") for item in declared.get(profile_id, ())
        )
        profile_options = profile.get("model_options")
        if isinstance(profile_options, list):
            options.extend(
                (item, "configured_profile")
                for item in profile_options
                if isinstance(item, Mapping)
            )
        for option, source in options:
            model = str(option.get("value") or option.get("model") or "").strip()
            if not model:
                continue
            row = _candidate(candidates, profile_id, model, profile, timestamp)
            option_observed_at = str(option.get("observed_at") or timestamp)
            _merge_option(
                row,
                option,
                kind=source,
                observed_at=option_observed_at,
                source=str(option.get("source") or source),
                version=_optional_text(option.get("provider_version")),
            )
            _add_source(
                row,
                kind=source,
                source=str(option.get("source") or source),
                observed_at=option_observed_at,
                version=_optional_text(option.get("provider_version")),
            )

    for item in discovered_models:
        _merge_observation(
            candidates,
            profile_rows,
            item,
            kind="authenticated_discovery",
            timestamp=timestamp,
        )
    for item in historical_models:
        _merge_observation(
            candidates,
            profile_rows,
            item,
            kind="historical_run",
            timestamp=timestamp,
        )

    rows = [_finalize_candidate(row, timestamp) for row in candidates.values()]
    rows.sort(key=lambda row: row["candidate_id"])
    return {
        "schema_version": MODEL_CATALOG_IDENTITY_SCHEMA_VERSION,
        "observed_at": timestamp,
        "state_names": list(MODEL_CATALOG_STATE_NAMES),
        "identity_contract": {
            "candidate_key_fields": [
                "profile_id",
                "provider_org",
                "model_vendor",
                "perspective_key",
                "channel",
                "capacity_pool",
                "model_id",
            ],
            "role_scoring_key_addition": "canonical_role",
            "discovery_proves_execution": False,
            "available_boolean_is_authoritative": False,
        },
        "candidates": rows,
        "candidate_count": len(rows),
    }


def _candidate(
    candidates: dict[tuple[str, str], dict[str, Any]],
    profile_id: str,
    model: str,
    profile: Mapping[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    normalized_profile = dict(profile)
    normalized_profile.setdefault("id", profile_id)
    identity = profile_identity(normalized_profile, selected_model=model)
    operational_identity = {
        "profile_id": profile_id,
        "provider_org": identity["provider_org"],
        "model_vendor": identity["model_vendor"],
        "perspective_key": identity["perspective_key"],
        "channel": identity["transport"],
        "capacity_pool": identity["capacity_pool"],
        "model_id": model,
    }
    key = (profile_id, model)
    if key in candidates:
        if candidates[key]["identity"] != operational_identity:
            raise ValueError(
                f"conflicting operational identity for {profile_id!r}/{model!r}"
            )
        return candidates[key]
    encoded = json.dumps(
        operational_identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    row = {
        "candidate_id": f"model-candidate:{hashlib.sha256(encoded).hexdigest()[:24]}",
        "identity": operational_identity,
        "label": model,
        "roles_declared": set(),
        "sources": [],
        "option": {},
        "option_field_ranks": {},
        "option_field_provenance": {},
        "profile": normalized_profile,
        "first_observed_at": timestamp,
    }
    candidates[key] = row
    return row


def _merge_observation(
    candidates: dict[tuple[str, str], dict[str, Any]],
    profiles: Mapping[str, dict[str, Any]],
    item: Mapping[str, Any],
    *,
    kind: str,
    timestamp: str,
) -> None:
    profile_id = str(item.get("profile_id") or "").strip()
    model = str(item.get("model") or item.get("model_id") or "").strip()
    if not profile_id or not model:
        return
    profile = profiles.get(profile_id) or {
        "id": profile_id,
        "provider": str(item.get("provider") or "unknown"),
        "channel": str(item.get("channel") or "unknown"),
        "config": {"capacity_pool": str(item.get("capacity_pool") or profile_id)},
    }
    row = _candidate(candidates, profile_id, model, profile, timestamp)
    observation_time = str(item.get("observed_at") or timestamp)
    _merge_option(
        row,
        item,
        kind=kind,
        observed_at=observation_time,
        source=str(item.get("source") or kind),
        version=_optional_text(item.get("provider_version")),
    )
    _add_source(
        row,
        kind=kind,
        source=str(item.get("source") or kind),
        observed_at=observation_time,
        version=_optional_text(item.get("provider_version")),
        verified=item.get("verified") is True,
    )


def _merge_option(
    row: dict[str, Any],
    option: Mapping[str, Any],
    *,
    kind: str,
    observed_at: str,
    source: str,
    version: str | None,
) -> None:
    current = row["option"]
    field_ranks = row["option_field_ranks"]
    field_provenance = row["option_field_provenance"]
    priority = _OPTION_SOURCE_PRIORITY[kind]
    for key, value in option.items():
        if value is None:
            continue
        rank = (priority, observed_at)
        previous_rank = field_ranks.get(key)
        replace = previous_rank is None or rank > previous_rank
        if rank == previous_rank:
            replace = _stable_value(value) < _stable_value(current.get(key))
        if replace:
            current[key] = value
            field_ranks[key] = rank
            field_provenance[key] = {
                "source": source,
                "version": version,
                "observed_at": observed_at,
            }
            if key == "label" and value:
                row["label"] = str(value)
    for role in option.get("best_for") or ():
        role_name = canonical_role(str(role))
        if role_name:
            row["roles_declared"].add(role_name)


def _add_source(
    row: dict[str, Any],
    *,
    kind: str,
    source: str,
    observed_at: str,
    version: str | None,
    verified: bool = False,
) -> None:
    receipt = {
        "kind": kind,
        "source": source,
        "observed_at": observed_at,
        "version": version,
        "verified": verified,
    }
    if receipt not in row["sources"]:
        row["sources"].append(receipt)
    row["first_observed_at"] = min(row["first_observed_at"], observed_at)


def _finalize_candidate(row: dict[str, Any], timestamp: str) -> dict[str, Any]:
    profile = row.pop("profile")
    option = row.pop("option")
    row.pop("option_field_ranks")
    field_provenance = row.pop("option_field_provenance")
    sources = sorted(
        row["sources"],
        key=lambda item: (item["observed_at"], item["kind"], item["source"]),
    )
    latest = (
        sources[-1]
        if sources
        else {
            "source": "projection",
            "observed_at": timestamp,
            "version": None,
        }
    )
    health = profile.get("health") if isinstance(profile.get("health"), Mapping) else {}
    profile_status = str(profile.get("status") or "").strip().lower()
    health_status = str(health.get("status") or "untested").strip().lower()
    availability = (
        str(
            option.get("verification_status")
            or option.get("availability")
            or "unverified"
        )
        .strip()
        .lower()
    )
    model_verified = availability == "verified" or any(
        item["verified"] for item in sources
    )
    retired = profile_status == "retired" or availability == "retired"
    blocked = (
        profile_status in _BLOCKED_PROFILE_STATES
        or availability in _BLOCKED_MODEL_STATES
        or retired
    )
    configured = _explicit_configured(profile, option)
    adapter_green = health_status == "ok"
    selectable = option.get("selectable") is True and not blocked
    manual_only = option.get("automatic") is False or bool(option.get("requires_probe"))
    provenance = {
        "source": latest["source"],
        "version": latest["version"],
        "observed_at": latest["observed_at"],
    }

    def option_provenance(*fields: str) -> dict[str, Any]:
        for field in fields:
            if field in field_provenance:
                return field_provenance[field]
        return provenance

    availability_provenance = option_provenance(
        "verification_status", "availability"
    )
    verified_sources = [item for item in sources if item["verified"]]
    verified_provenance = (
        {
            "source": verified_sources[-1]["source"],
            "version": verified_sources[-1]["version"],
            "observed_at": verified_sources[-1]["observed_at"],
        }
        if verified_sources
        else availability_provenance
    )
    states = {
        "catalogued": _state(
            True, "candidate_seen_in_catalog_discovery_or_history", **provenance
        ),
        "configured": _state(
            configured,
            "profile_configuration_observed"
            if configured
            else "profile_configuration_not_proven",
            **option_provenance("configured", "value", "model"),
        ),
        "adapter_green": _state(
            adapter_green,
            f"adapter_health_{health_status}",
            source="adapter_health",
            version=_optional_text(health.get("version")),
            observed_at=str(health.get("checked_at") or latest["observed_at"]),
        ),
        "model_verified": _state(
            model_verified,
            "exact_model_execution_verified"
            if model_verified
            else "discovery_is_not_execution",
            **verified_provenance,
        ),
        "selectable": _state(
            selectable,
            str(
                option.get("availability_reason")
                or ("selection_allowed" if selectable else "selection_not_proven")
            ),
            **option_provenance("selectable", "availability_reason"),
        ),
        "compatible": _state(
            None,
            "requires_canonical_role_and_execution_context",
            source="model_compatibility",
            version=None,
            observed_at=timestamp,
        ),
        "calibrated": _state(
            None,
            "requires_exact_profile_model_role_evidence",
            source="model_evaluation_coverage",
            version=None,
            observed_at=timestamp,
        ),
        "stale": _state(
            None,
            "requires_evidence_date_and_provider_version",
            source="model_calibration",
            version=None,
            observed_at=timestamp,
        ),
        "manual_only": _state(
            manual_only,
            "automatic_policy_disabled_or_probe_required"
            if manual_only
            else "automatic_policy_not_disabled",
            **option_provenance("automatic", "requires_probe"),
        ),
        "blocked": _state(
            blocked,
            str(option.get("availability_reason") or profile_status or availability),
            **availability_provenance,
        ),
        "retired": _state(
            retired,
            "profile_or_exact_model_retired" if retired else "no_retirement_evidence",
            **option_provenance("availability", "verification_status"),
        ),
    }
    assert tuple(states) == MODEL_CATALOG_STATE_NAMES
    return {
        "candidate_id": row["candidate_id"],
        "identity": row["identity"],
        "label": row["label"],
        "roles_declared": sorted(row["roles_declared"]),
        "first_observed_at": row["first_observed_at"],
        "sources": sources,
        "states": states,
    }


def _explicit_configured(profile: Mapping[str, Any], option: Mapping[str, Any]) -> bool:
    if profile.get("configured") is True or profile.get("connected") is True:
        return True
    health = profile.get("health") if isinstance(profile.get("health"), Mapping) else {}
    if str(health.get("status") or "").lower() in {"ok", "installed"}:
        return True
    config = profile.get("config") if isinstance(profile.get("config"), Mapping) else {}
    selected_model = str(config.get("model") or "").strip()
    model = str(option.get("value") or option.get("model") or "").strip()
    return bool(selected_model and selected_model == model)


def _state(
    value: bool | None,
    reason: str,
    *,
    source: str,
    version: str | None,
    observed_at: str,
) -> dict[str, Any]:
    return {
        "value": value,
        "reason": reason,
        "source": source,
        "version": version,
        "observed_at": observed_at,
    }


def _iso_timestamp(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat()
    if value:
        return str(value)
    return datetime.now(timezone.utc).isoformat()


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _stable_value(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
