"""Guías manuales por proveedor para la preparación del wizard."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiteam.installation_support import load_installation_support_contract

SCHEMA_VERSION = "guided_setup_provider_guidance_v1"
_LABELS = {
    "codex_subscription": "Codex",
    "antigravity_subscription": "Antigravity",
    "opencode_zen_free": "OpenCode Zen",
    "ollama": "Ollama",
    "lmstudio": "LM Studio",
    "personal_api": "API personal",
}


def build_provider_guidance(
    plan: Mapping[str, Any],
    *,
    support_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if plan.get("schema_version") != "guided_setup_preparation_v1":
        raise ValueError("guided_setup_provider_guidance_plan_schema_mismatch")
    scope = plan.get("scope")
    if not isinstance(scope, Mapping) or any(
        scope.get(key) is not expected
        for key, expected in (
            ("read_only", True),
            ("secrets_read", False),
            ("installations_attempted", False),
            ("terms_accepted", False),
        )
    ):
        raise ValueError("guided_setup_provider_guidance_plan_scope_unsafe")
    support = dict(support_contract or load_installation_support_contract())
    support_by_id = {row["id"]: row for row in support["adapters"]}
    cli_by_adapter = {
        row["adapter_id"]: row
        for row in support["cli_version_contract"]["entries"]
    }
    providers = []
    for adapter in plan.get("adapters", []):
        adapter_id = str(adapter.get("id") or "")
        if adapter_id == "personal_api":
            providers.append(_personal_api_guide(adapter))
            continue
        if adapter.get("setup_class") == "owner_selected_api":
            providers.append(_selected_api_guide(adapter))
            continue
        if adapter_id not in support_by_id:
            raise ValueError("guided_setup_provider_guidance_unknown_adapter")
        providers.append(
            _cli_guide(
                adapter,
                support=support_by_id[adapter_id],
                cli_contract=cli_by_adapter.get(adapter_id),
            )
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_needs_hash": str(plan.get("needs_hash") or ""),
        "policy": {
            "execution": "manual_only",
            "automatic_install": False,
            "automatic_login": False,
            "automatic_terms_acceptance": False,
            "secret_values_in_payload": False,
            "action_completion_grants_ready": False,
        },
        "providers": providers,
    }


def _cli_guide(
    adapter: Mapping[str, Any],
    *,
    support: Mapping[str, Any],
    cli_contract: Mapping[str, Any] | None,
) -> dict[str, Any]:
    adapter_id = str(adapter["id"])
    stages = dict(adapter.get("stages") or {})
    actions: list[dict[str, Any]] = []
    install = support.get("install") or {}
    if stages.get("installation") != "passed":
        command = install.get("windows")
        actions.append(
            _action(
                adapter_id,
                "install",
                phase="installation",
                description=(
                    f"Instala {_LABELS[adapter_id]} manualmente y vuelve al "
                    "asistente para repetir el doctor."
                ),
                command=str(command) if command else None,
                risk=(
                    "remote_script_execution"
                    if command and ("irm " in str(command) or "iex" in str(command))
                    else "global_package_install"
                    if command
                    else "external_installer"
                ),
                evidence="doctor_cli_version_observation",
            )
        )
    if stages.get("version") != "passed":
        actions.append(
            _action(
                adapter_id,
                "verify_version",
                phase="version",
                description="Comprueba que la identidad y versión coinciden con el contrato.",
                command=" ".join(
                    [str(support["commands"][0]), *support["version_args"]]
                ),
                risk="read_only_command",
                evidence="provider_cli_version_audit_v1",
            )
        )
    authentication = support.get("authentication") or {}
    if stages.get("authentication") != "passed":
        actions.append(
            _action(
                adapter_id,
                "authenticate",
                phase="authentication",
                description=(
                    f"Completa el login humano de {_LABELS[adapter_id]}. "
                    "El asistente no captura credenciales."
                ),
                command=authentication.get("command"),
                risk="opens_provider_authentication",
                evidence="adapter_health_auth_status",
            )
        )
    for phase, evidence in (
        ("catalog", "authenticated_catalog_receipt"),
        ("health", "adapter_health_receipt"),
        ("contract", "structured_output_probe_receipt"),
    ):
        if stages.get(phase) != "passed":
            actions.append(
                _action(
                    adapter_id,
                    f"verify_{phase}",
                    phase=phase,
                    description=(
                        f"Ejecuta la comprobación canónica de {phase}; "
                        "puede consumir cuota si realiza un probe remoto."
                    ),
                    command=None,
                    risk="remote_quota_possible",
                    evidence=evidence,
                )
            )
    notes = []
    if authentication.get("mode"):
        notes.append(f"authentication_mode:{authentication['mode']}")
    if authentication.get("notes"):
        notes.append(str(authentication["notes"]))
    if support.get("data_policy"):
        notes.append(f"data_policy:{support['data_policy']}")
    return {
        "adapter_id": adapter_id,
        "label": _LABELS[adapter_id],
        "requirement": adapter["requirement"],
        "current_state": adapter["state"],
        "minimum_version": (cli_contract or {}).get("minimum_version"),
        "validated_version": (cli_contract or {}).get("validated_version"),
        "notes": notes,
        "actions": actions,
    }


def _personal_api_guide(adapter: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "adapter_id": "personal_api",
        "label": _LABELS["personal_api"],
        "requirement": adapter["requirement"],
        "current_state": adapter["state"],
        "minimum_version": None,
        "validated_version": None,
        "notes": [
            "La suscripción y la API son canales independientes.",
            "La clave se envía solo al almacén de secretos; el wizard conserva su ref.",
        ],
        "actions": [
            _action(
                "personal_api",
                "select_provider",
                phase="installation",
                description="Elige el proveedor API que quieres configurar.",
                command=None,
                risk="provider_terms_and_pricing",
                evidence="selected_adapter_profile_id",
            ),
            _action(
                "personal_api",
                "store_secret_reference",
                phase="authentication",
                description=(
                    "Guarda la clave mediante /api/user-adapters/secrets y "
                    "conserva únicamente la referencia secret:provider:name."
                ),
                command=None,
                risk="secret_entry",
                evidence="secret_ref_only",
            ),
            _action(
                "personal_api",
                "verify_adapter",
                phase="contract",
                description=(
                    "Prueba auth, catálogo, health y salida estructurada tras "
                    "confirmar el posible consumo de cuota."
                ),
                command=None,
                risk="remote_quota_possible",
                evidence="structured_output_probe_receipt",
            ),
        ],
    }


def _selected_api_guide(adapter: Mapping[str, Any]) -> dict[str, Any]:
    adapter_id = str(adapter["id"])
    return {
        "adapter_id": adapter_id,
        "label": adapter_id,
        "requirement": adapter["requirement"],
        "current_state": adapter["state"],
        "minimum_version": None,
        "validated_version": None,
        "notes": [
            "Perfil API elegido explícitamente por el owner.",
            "La clave no forma parte del wizard; solo se conserva secret_ref.",
        ],
        "actions": [
            _action(
                adapter_id,
                "store_secret_reference",
                phase="authentication",
                description=(
                    "Guarda la clave mediante /api/user-adapters/secrets y "
                    "conserva únicamente secret_ref."
                ),
                command=None,
                risk="secret_entry",
                evidence="secret_ref_only",
            ),
            _action(
                adapter_id,
                "verify_adapter",
                phase="contract",
                description=(
                    "Revalida auth, catálogo, health y JSON estructurado tras "
                    "confirmar el posible consumo de cuota."
                ),
                command=None,
                risk="remote_quota_possible",
                evidence="structured_output_probe_receipt",
            ),
        ],
    }


def _action(
    adapter_id: str,
    action: str,
    *,
    phase: str,
    description: str,
    command: str | None,
    risk: str,
    evidence: str,
) -> dict[str, Any]:
    return {
        "id": f"{adapter_id}:{action}",
        "phase": phase,
        "description": description,
        "execution": "manual_only",
        "confirmation_required": True,
        "automatic": False,
        "copyable_command": command,
        "risk": risk,
        "completion_evidence": evidence,
        "completion_grants_ready": False,
    }
