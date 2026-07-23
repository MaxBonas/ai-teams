"""Resolución efectiva del gate de compatibilidad sobre configuración real."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from aiteam.model_compatibility import compatibility_decision
from aiteam.policies import canonical_role
from aiteam.user_config import ROLE_CAPABILITY_PROFILES, load_adapter_profiles


NON_LLM_ADAPTERS = frozenset({"", "manual", "lead_builtin", "role_builtin", "builtin"})

_SERVICE_REASONS = {
    "profile_not_selected": "La asignación LLM no identifica un perfil de adapter.",
    "profile_not_found": "El perfil configurado ya no existe.",
}


class ModelCompatibilityError(ValueError):
    def __init__(self, decision: dict[str, Any]) -> None:
        self.decision = decision
        super().__init__(str(decision.get("reason") or decision.get("code") or "incompatible model"))


def resolve_assignment_compatibility(
    *,
    adapter_type: str,
    adapter_config: dict[str, Any] | None,
    role: str,
    run_profile: str = "",
    criticality: str = "medium",
    data_class: str = "",
    required_capabilities: Iterable[str] = (),
    profiles: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resuelve el contrato desde la asignación que realmente se ejecutará."""
    adapter_key = str(adapter_type or "").strip()
    config = adapter_config if isinstance(adapter_config, dict) else {}
    role_key = canonical_role(role)
    if adapter_key in NON_LLM_ADAPTERS or role_key == "test_runner":
        return {
            "allowed": True,
            "code": "not_applicable_builtin",
            "reason": "El trabajo se ejecuta de forma determinista o manual.",
            "profile_id": None,
            "model": None,
            "role": role_key,
            "alternatives": [],
        }
    profile_id = str(config.get("profile_id") or "").strip()
    if not profile_id:
        return _service_deny("profile_not_selected", role=role_key)
    available_profiles = list(profiles) if profiles is not None else load_adapter_profiles()
    profile = next(
        (item for item in available_profiles if str(item.get("id") or "") == profile_id),
        None,
    )
    if profile is None:
        return _service_deny("profile_not_found", role=role_key, profile_id=profile_id)
    profile_config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
    model_id = str(config.get("model") or profile_config.get("model") or "").strip()
    options = profile.get("model_options") if isinstance(profile.get("model_options"), list) else []
    selected = next(
        (item for item in options if str(item.get("value") or "") == model_id),
        None,
    )
    return compatibility_decision(
        profile=profile,
        model=selected,
        role=role_key,
        run_profile=run_profile,
        criticality=criticality,
        data_class=data_class,
        required_capabilities=required_capabilities,
        role_profile=ROLE_CAPABILITY_PROFILES.get(role_key, {}),
        candidate_models=options,
    )


def require_compatible_assignment(**kwargs: Any) -> dict[str, Any]:
    decision = resolve_assignment_compatibility(**kwargs)
    if not decision.get("allowed"):
        raise ModelCompatibilityError(decision)
    return decision


def issue_compatibility_context(db_path: Path, issue_id: str) -> dict[str, Any]:
    """Hereda contexto de la issue/ancestros sin inventar clasificación."""
    context: dict[str, Any] = {
        "run_profile": "", "criticality": "medium", "data_class": "",
        "required_capabilities": [],
    }
    current = str(issue_id or "").strip()
    seen: set[str] = set()
    criticality_found = False
    with contextlib.closing(sqlite3.connect(str(db_path), timeout=20.0)) as conn:
        conn.row_factory = sqlite3.Row
        while current and current not in seen and len(seen) < 32:
            seen.add(current)
            row = conn.execute(
                "SELECT parent_id, criticality, metadata_json FROM issues WHERE id = ?",
                (current,),
            ).fetchone()
            if row is None:
                break
            metadata = _decode_json(row["metadata_json"])
            if not context["run_profile"]:
                selection = metadata.get("profile_selection") if isinstance(metadata.get("profile_selection"), dict) else {}
                context["run_profile"] = str(
                    metadata.get("profile") or selection.get("profile") or ""
                ).strip().lower()
            if not criticality_found and str(row["criticality"] or "").strip():
                context["criticality"] = str(row["criticality"]).strip().lower()
                criticality_found = True
            if not context["data_class"]:
                classification = metadata.get("data_classification")
                if isinstance(classification, dict):
                    classification = classification.get("class") or classification.get("level")
                context["data_class"] = str(
                    metadata.get("data_class") or classification or ""
                ).strip().lower()
            raw_required = metadata.get("required_capabilities")
            if isinstance(raw_required, list):
                context["required_capabilities"] = sorted({
                    *context["required_capabilities"],
                    *(
                        str(item).strip()
                        for item in raw_required
                        if str(item).strip()
                    ),
                })
            current = str(row["parent_id"] or "").strip()
    return context


def _service_deny(
    code: str, *, role: str, profile_id: str | None = None
) -> dict[str, Any]:
    return {
        "allowed": False,
        "code": code,
        "reason": _SERVICE_REASONS[code],
        "profile_id": profile_id,
        "model": None,
        "role": role,
        "alternatives": [],
    }


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}
