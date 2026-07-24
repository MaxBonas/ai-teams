"""Decisión pura y provider-neutral de compatibilidad modelo × rol.

No consulta filesystem, SQLite, secretos ni red. Los consumidores resuelven el
perfil y el catálogo vivo y entregan aquí evidencia ya anotada. Así bootstrap,
hiring, Equipo, fallback y preflight pueden compartir exactamente los mismos
códigos sin convertir recomendaciones (`best_for`) en permisos.
"""

from __future__ import annotations

from typing import Any, Iterable

from aiteam.policies import canonical_role, role_status, role_tier


RUN_PROFILES = ("solo_lead", "lead_quorum", "full_team")
CRITICALITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
TIER_RANK = {"budget": 1, "standard": 2, "premium": 3}
SENSITIVE_DATA_CLASSES = frozenset({"confidential", "restricted", "secret", "secrets"})
MODEL_CAPABILITIES = frozenset({
    "coding", "reasoning", "synthesis", "long_ctx", "multimodal",
})

_WRITE_ROLES = frozenset({"lead_executor", "engineer", "qa", "test_designer"})
_READ_ROLES = frozenset({"file_scout", "reviewer", "context_curator", "architect"})

_REASONS = {
    "compatible": "La selección cumple el contrato efectivo del rol.",
    "unknown_role": "El rol no pertenece a la taxonomía gobernada de AI Teams.",
    "legacy_role": "El rol es legacy y no admite nuevas asignaciones de modelo.",
    "deterministic_role": "El rol es determinista y no debe consumir un modelo.",
    "profile_blocked": "El perfil está bloqueado o retirado.",
    "model_not_catalogued": "El modelo no pertenece al catálogo declarado del perfil.",
    "model_unavailable": "El modelo no está ejecutable en este perfil.",
    "model_role_unclassified": (
        "El modelo está catalogado, pero aún no tiene roles aprobados."
    ),
    "profile_role_unsupported": "El perfil no declara soporte para este rol.",
    "model_role_incompatible": "El modelo no está aprobado para este rol.",
    "model_tier_insufficient": "La banda del modelo es inferior a la autoridad del rol.",
    "model_tier_unknown": "Falta declarar la banda de capacidad del modelo.",
    "model_capability_missing": "El modelo no demuestra todas las capacidades requeridas.",
    "run_profile_incompatible": "El modelo o perfil no admite este modo de ejecución.",
    "workspace_read_required": "El rol necesita leer el workspace y el perfil no lo permite.",
    "workspace_write_required": "El rol necesita materializar cambios y el perfil es de solo lectura.",
    "external_mcp_unsupported": "El rol requiere MCP externo y el transporte no ofrece un loop gobernado.",
    "structured_output_required": "El transporte no demuestra el contrato estructurado mínimo.",
    "structured_output_insufficient": "La salida estructurada demostrada es insuficiente para este contrato.",
    "data_classification_required": "Debe clasificarse la información antes de usar este canal restringido.",
    "confidential_data_forbidden": "El canal o modelo no admite datos confidenciales.",
    "criticality_unsupported": "El modelo no está aprobado para esta criticidad.",
}


def compatibility_decision(
    *,
    profile: dict[str, Any],
    model: dict[str, Any] | None,
    role: str,
    run_profile: str = "",
    criticality: str = "medium",
    data_class: str = "",
    required_capabilities: Iterable[str] = (),
    role_profile: dict[str, Any] | None = None,
    candidate_models: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Evalúa una selección y devuelve un resultado estable y explicable."""
    selected = model if isinstance(model, dict) else {}
    role_key = canonical_role(role)
    run_key = str(run_profile or "").strip().lower()
    criticality_key = str(criticality or "medium").strip().lower()
    data_key = str(data_class or "").strip().lower()
    required = {str(item).strip().lower() for item in required_capabilities if str(item).strip()}
    role_meta = role_profile if isinstance(role_profile, dict) else {}
    role_caps = {
        str(item).strip().lower()
        for item in role_meta.get("capabilities_needed") or []
        if str(item).strip()
    }
    effective_required = required | role_caps
    profile_id = str(profile.get("id") or "")
    model_id = str(selected.get("value") or selected.get("model") or "")

    effective = {
        "workspace_mode": _workspace_mode(profile),
        "mcp_transport": _mcp_transport(profile),
        "structured_output": _structured_output(profile, selected),
    }
    required_workspace = _required_workspace_mode(
        role_key, run_key, effective_required
    )
    required_structured = _required_structured_output(effective_required)
    allowed_roles = _normalized_values(selected.get("allowed_roles"))
    denied_roles = _normalized_values(selected.get("denied_roles"))
    profile_roles = _normalized_values(profile.get("supported_roles"))
    allowed_modes = tuple(
        str(item).strip().lower()
        for item in (selected.get("allowed_run_profiles") or profile.get("allowed_run_profiles") or RUN_PROFILES)
        if str(item).strip()
    )

    code = "compatible"
    details: dict[str, Any] = {}
    status = role_status(role_key)
    if status == "unknown":
        code = "unknown_role"
    elif status == "legacy":
        code = "legacy_role"
    elif status == "deterministic":
        code = "deterministic_role"
    elif str(profile.get("status") or "").lower() in {
        "blocked", "blocked_by_provider", "retired", "disabled",
    }:
        code = "profile_blocked"
    elif not model_id:
        code = "model_not_catalogued"
    elif (
        selected.get("selectable") is False
        or ("selectable" not in selected and selected.get("available") is False)
    ):
        code = "model_unavailable"
        details["availability_reason"] = selected.get("availability_reason")
    elif selected.get("assignment_policy") == "catalog_only":
        code = "model_role_unclassified"
    elif profile_roles and role_key not in profile_roles:
        code = "profile_role_unsupported"
    elif allowed_roles and role_key not in allowed_roles:
        code = "model_role_incompatible"
    elif role_key in denied_roles:
        code = "model_role_incompatible"
    elif run_key and run_key not in allowed_modes:
        code = "run_profile_incompatible"
    else:
        tier = str(selected.get("tier") or "").lower()
        minimum_tier = _minimum_model_tier(role_key)
        if not tier and not allowed_roles:
            code = "model_tier_unknown"
        elif tier and TIER_RANK.get(tier, 0) < TIER_RANK.get(minimum_tier, 0):
            code = "model_tier_insufficient"
            details.update({"model_tier": tier, "minimum_tier": minimum_tier})

    if code == "compatible":
        model_caps = {str(item).lower() for item in selected.get("caps") or profile.get("capabilities") or []}
        # Transport/tool requirements belong to the effective role contract,
        # but are not intrinsic model capabilities.
        required_model_caps = effective_required & MODEL_CAPABILITIES
        missing = sorted(required_model_caps - model_caps)
        if missing:
            code = "model_capability_missing"
            details["missing_capabilities"] = missing

    if code == "compatible" and required_workspace == "read" and effective["workspace_mode"] == "none":
        code = "workspace_read_required"
    elif code == "compatible" and required_workspace == "write" and effective["workspace_mode"] != "write":
        code = "workspace_write_required"

    if code == "compatible" and (
        role_key == "mcp_operator" or "external_mcp" in effective_required
    ):
        if effective["mcp_transport"] != "governed":
            code = "external_mcp_unsupported"

    if code == "compatible":
        if effective["structured_output"] == "none":
            code = "structured_output_required"
        elif (
            required_structured == "json_schema"
            and effective["structured_output"] != "json_schema"
        ):
            code = "structured_output_insufficient"

    data_policy = str(profile.get("data_policy") or "").strip().lower()
    confidential_allowed = selected.get("confidential_data_allowed")
    restricted_channel = data_policy in {"non_confidential_only", "provider_free_tier"}
    if code == "compatible" and restricted_channel and not data_key:
        code = "data_classification_required"
    elif code == "compatible" and data_key in SENSITIVE_DATA_CLASSES and (
        restricted_channel or confidential_allowed is False
    ):
        code = "confidential_data_forbidden"

    max_criticality = str(selected.get("max_criticality") or "").strip().lower()
    if code == "compatible" and max_criticality:
        if CRITICALITY_RANK.get(criticality_key, 1) > CRITICALITY_RANK.get(max_criticality, -1):
            code = "criticality_unsupported"
            details["max_criticality"] = max_criticality

    result = {
        "allowed": code == "compatible",
        "code": code,
        "reason": _REASONS[code],
        "profile_id": profile_id,
        "model": model_id,
        "role": role_key,
        "run_profile": run_key or None,
        "criticality": criticality_key,
        "data_class": data_key or None,
        "allowed_roles": list(allowed_roles or profile_roles),
        "allowed_run_profiles": list(allowed_modes),
        "required_workspace_mode": required_workspace,
        "effective": effective,
        "details": details,
        "alternatives": [],
    }
    if code != "compatible":
        result["alternatives"] = _compatible_alternatives(
            profile=profile,
            candidates=candidate_models,
            rejected_model=model_id,
            role=role_key,
            run_profile=run_key,
            criticality=criticality_key,
            data_class=data_key,
            required_capabilities=required,
            role_profile=role_meta,
        )
    return result


def _compatible_alternatives(
    *, profile: dict[str, Any], candidates: Iterable[dict[str, Any]], rejected_model: str,
    role: str, run_profile: str, criticality: str, data_class: str,
    required_capabilities: set[str], role_profile: dict[str, Any],
) -> list[dict[str, str]]:
    alternatives: list[dict[str, str]] = []
    for candidate in candidates:
        value = str(candidate.get("value") or "")
        if not value or value == rejected_model:
            continue
        decision = compatibility_decision(
            profile=profile, model=candidate, role=role, run_profile=run_profile,
            criticality=criticality, data_class=data_class,
            required_capabilities=required_capabilities, role_profile=role_profile,
        )
        if decision["allowed"]:
            alternatives.append({"value": value, "label": str(candidate.get("label") or value)})
    return alternatives[:5]


def _normalized_values(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(dict.fromkeys(canonical_role(item) for item in value if str(item).strip()))


def _minimum_model_tier(role: str) -> str:
    tier = role_tier(role)
    if tier == 1:
        return "premium"
    if tier == 2:
        return "standard"
    return "budget"


def _required_workspace_mode(role: str, run_profile: str, required: set[str]) -> str:
    if "repo_write" in required or role in _WRITE_ROLES or (role in {"lead", "team_lead"} and run_profile == "solo_lead"):
        return "write"
    if "repo_read" in required or role in _READ_ROLES:
        return "read"
    return "none"


def _workspace_mode(profile: dict[str, Any]) -> str:
    explicit = str(profile.get("workspace_mode") or "").lower()
    if explicit in {"none", "read", "write"}:
        return explicit
    config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
    if config.get("read_only") is True or str(config.get("cli_kind") or "").lower() == "opencode":
        return "read"
    adapter_type = str(profile.get("adapter_type") or "").lower()
    if adapter_type in {
        "subscription_cli", "openai_api", "gemini_api", "anthropic_api",
        "anthropic_sonnet", "openai_compatible_api",
    }:
        return "write"
    return "none"


def _mcp_transport(profile: dict[str, Any]) -> str:
    explicit = str(profile.get("mcp_transport") or "").lower()
    if explicit in {"none", "governed"}:
        return explicit
    config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
    if str(config.get("cli_kind") or "").lower() in {"codex", "opencode"}:
        return "governed"
    return "none"


def _structured_output(profile: dict[str, Any], model: dict[str, Any]) -> str:
    explicit = str(model.get("structured_output") or profile.get("structured_output") or "").lower()
    if explicit in {"none", "json_object", "json_schema"}:
        return explicit
    adapter_type = str(profile.get("adapter_type") or "").lower()
    config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
    if adapter_type in {"openai_api", "anthropic_api", "anthropic_sonnet"}:
        return "json_schema"
    if adapter_type == "openai_compatible_api":
        strict = {str(item) for item in config.get("strict_models") or []}
        return "json_schema" if str(model.get("value") or "") in strict else "json_object"
    if adapter_type in {"gemini_api", "subscription_cli"}:
        return "json_object"
    return "none"


def _required_structured_output(required: set[str]) -> str:
    return "json_schema" if "json_schema" in required else "json_object"
