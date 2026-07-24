"""Hermetic contract audit for every built-in profile/model pair."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from aiteam.model_compatibility import compatibility_decision
from aiteam.policies import role_status
from aiteam.user_config import (
    DEFAULT_ADAPTER_PROFILES,
    MODEL_OPTIONS_BY_PROFILE,
    ROLE_CAPABILITY_PROFILES,
)


ASSIGNABLE_ROLES = tuple(
    role for role in ROLE_CAPABILITY_PROFILES
    if role_status(role) in {"active", "conditional"}
)


def prepared_builtin_profiles() -> list[dict[str, Any]]:
    """Return static catalog profiles with runtime availability proven by fixture.

    This is test/audit evidence, never production health. Live availability
    remains owned by authenticated discovery and exact model probes.
    """
    profiles: list[dict[str, Any]] = []
    for source in DEFAULT_ADAPTER_PROFILES:
        profile = deepcopy(source)
        profile_id = str(profile["id"])
        profile["model_options"] = [
            {**deepcopy(option), "available": True, "selectable": True}
            for option in MODEL_OPTIONS_BY_PROFILE.get(profile_id, [])
        ]
        profiles.append(profile)
    return profiles


def audit_builtin_model_flows() -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    cells: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for profile in prepared_builtin_profiles():
        profile_id = str(profile["id"])
        options = profile.get("model_options") or []
        for model in options:
            model_id = str(model.get("value") or "")
            pair = (profile_id, model_id)
            if not model_id or pair in seen_pairs:
                failures.append(_failure(pair, "catalog_identity", "ID vacío o duplicado"))
                continue
            seen_pairs.add(pair)
            for field in ("label", "tier", "caps", "best_for", "price_note"):
                if field not in model:
                    failures.append(_failure(pair, "catalog_metadata", f"falta {field}"))

            decisions = {
                role: _decision(profile, model, role, data_class="public")
                for role in ASSIGNABLE_ROLES
            }
            allowed_roles = sorted(role for role, item in decisions.items() if item["allowed"])
            denied_roles = {
                role: str(item["code"])
                for role, item in decisions.items()
                if not item["allowed"]
            }
            blocked = str(profile.get("status") or "").lower() in {
                "blocked", "blocked_by_provider", "retired", "disabled",
            }
            catalog_only = model.get("assignment_policy") == "catalog_only"
            if blocked:
                if any(code != "profile_blocked" for code in denied_roles.values()):
                    failures.append(_failure(pair, "blocked_profile", "deny no uniforme"))
            elif catalog_only:
                if allowed_roles or any(
                    code != "model_role_unclassified" for code in denied_roles.values()
                ):
                    failures.append(
                        _failure(
                            pair,
                            "catalog_only",
                            "un modelo sin clasificar debe denegar todos los roles",
                        )
                    )
            elif not allowed_roles:
                failures.append(_failure(pair, "positive_path", "ningún rol activo compatible"))

            for role in (() if blocked else (model.get("best_for") or [])):
                if role_status(role) not in {"active", "conditional"}:
                    failures.append(_failure(pair, "best_for", f"rol no activo: {role}"))
                elif not decisions.get(role, {}).get("allowed"):
                    code = decisions.get(role, {}).get("code", "missing")
                    failures.append(_failure(pair, "best_for", f"{role} queda {code}"))

            deterministic = _decision(profile, model, "test_runner", data_class="public")
            if deterministic["code"] != "deterministic_role":
                failures.append(_failure(pair, "deterministic_deny", deterministic["code"]))

            data_policy = str(profile.get("data_policy") or "")
            if (
                data_policy in {"non_confidential_only", "provider_free_tier"}
                and not catalog_only
            ):
                probe_role = allowed_roles[0] if allowed_roles else "reviewer"
                missing_class = _decision(profile, model, probe_role, data_class="")
                confidential = _decision(
                    profile, model, probe_role, data_class="confidential"
                )
                if missing_class["code"] != "data_classification_required":
                    failures.append(_failure(pair, "privacy_missing", missing_class["code"]))
                if confidential["code"] != "confidential_data_forbidden":
                    failures.append(_failure(pair, "privacy_confidential", confidential["code"]))

            if str(profile.get("channel") or "") == "api" and allowed_roles:
                external_mcp = _decision(
                    profile,
                    model,
                    allowed_roles[0],
                    data_class="public",
                    required_capabilities=["external_mcp"],
                )
                if external_mcp["code"] != "external_mcp_unsupported":
                    failures.append(_failure(pair, "api_mcp_deny", external_mcp["code"]))

            if model.get("max_criticality") and allowed_roles:
                high = _decision(
                    profile, model, allowed_roles[0], data_class="public", criticality="high"
                )
                if high["code"] != "criticality_unsupported":
                    failures.append(_failure(pair, "criticality_deny", high["code"]))

            if model.get("structured_output") == "json_object" and allowed_roles:
                strict = _decision(
                    profile,
                    model,
                    allowed_roles[0],
                    data_class="public",
                    required_capabilities=["json_schema"],
                )
                if strict["code"] != "structured_output_insufficient":
                    failures.append(_failure(pair, "schema_deny", strict["code"]))

            cells.append({
                "profile_id": profile_id,
                "model": model_id,
                "allowed_roles": allowed_roles,
                "denied_roles": denied_roles,
                "blocked_profile": blocked,
                "catalog_only": catalog_only,
            })

    return {
        "ok": not failures,
        "profile_count": len(prepared_builtin_profiles()),
        "model_count": len(cells),
        "positive_cell_count": sum(len(item["allowed_roles"]) for item in cells),
        "negative_cell_count": sum(len(item["denied_roles"]) for item in cells),
        "cells": cells,
        "failures": failures,
    }


def _decision(
    profile: dict[str, Any],
    model: dict[str, Any],
    role: str,
    *,
    data_class: str,
    criticality: str = "medium",
    required_capabilities: list[str] | None = None,
) -> dict[str, Any]:
    return compatibility_decision(
        profile=profile,
        model=model,
        role=role,
        run_profile="full_team",
        criticality=criticality,
        data_class=data_class,
        required_capabilities=required_capabilities or [],
        role_profile=ROLE_CAPABILITY_PROFILES.get(role, {}),
        candidate_models=profile.get("model_options") or [],
    )


def _failure(pair: tuple[str, str], check: str, detail: str) -> dict[str, str]:
    return {"profile_id": pair[0], "model": pair[1], "check": check, "detail": detail}
