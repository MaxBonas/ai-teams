"""Proyección read-only de requisitos de máquina y adapters para el wizard."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiteam.guided_setup_needs import validate_needs_submission
from aiteam.installation_support import (
    load_installation_support_contract,
    version_meets_minimum,
)

SCHEMA_VERSION = "guided_setup_preparation_v1"
_SUBSCRIPTION_ADAPTERS = {
    "codex": "codex_subscription",
    "antigravity": "antigravity_subscription",
}
_STAGE_ORDER = (
    "installation",
    "version",
    "authentication",
    "catalog",
    "health",
    "contract",
)


def build_preparation_plan(
    needs: Mapping[str, Any],
    inventory: Mapping[str, Any],
    *,
    provider_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    selected_api_profiles: list[Mapping[str, Any]] | None = None,
    support_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map choices plus canonical doctor evidence without executing mutations."""
    scope = str(needs.get("scope") or "")
    sealed = validate_needs_submission(needs, scope=scope)
    if inventory.get("schema_version") != "machine_doctor_v1":
        raise ValueError("guided_setup_preparation_inventory_schema_mismatch")
    inventory_scope = inventory.get("scope")
    if not isinstance(inventory_scope, Mapping) or any(
        inventory_scope.get(key) is not expected
        for key, expected in (
            ("read_only", True),
            ("secrets_read", False),
            ("credentials_probed", False),
        )
    ):
        raise ValueError("guided_setup_preparation_inventory_scope_unsafe")

    support = dict(support_contract or load_installation_support_contract())
    adapter_support = {row["id"]: row for row in support["adapters"]}
    cli_contract = {
        row["adapter_id"]: row
        for row in support["cli_version_contract"]["entries"]
    }
    observed = {
        str(row.get("id") or ""): row
        for row in inventory.get("adapters", [])
        if isinstance(row, Mapping) and str(row.get("id") or "")
    }
    evidence = dict(provider_evidence or {})
    answers = sealed["answers"]
    requested: dict[str, str] = {}
    for subscription in answers.get("subscriptions", []):
        adapter_id = _SUBSCRIPTION_ADAPTERS.get(subscription)
        if adapter_id:
            requested[adapter_id] = "recommended"
    if answers.get("budget_priority") in {"zero_cost", "prefer_free"}:
        requested["opencode_zen_free"] = "recommended"
    if answers.get("local_models") in {"available", "willing"}:
        requested["ollama"] = "optional"
        requested["lmstudio"] = "optional"

    adapters = [
        _adapter_item(
            adapter_id,
            requirement=requirement,
            observation=observed.get(adapter_id),
            support=adapter_support[adapter_id],
            cli_contract=cli_contract.get(adapter_id),
            evidence=evidence.get(adapter_id, {}),
        )
        for adapter_id, requirement in requested.items()
    ]
    selected_apis = list(selected_api_profiles or [])
    selected_api_ids = [str(row.get("id") or "") for row in selected_apis]
    if len(selected_api_ids) != len(set(selected_api_ids)):
        raise ValueError("guided_setup_preparation_api_profile_duplicate")
    if selected_apis and answers.get("api_access") not in {"existing", "willing"}:
        raise ValueError("guided_setup_preparation_api_not_opted_in")
    if answers.get("api_access") in {"existing", "willing"}:
        if selected_apis:
            for profile in selected_apis:
                adapters.append(
                    _api_profile_item(
                        profile,
                        evidence=evidence.get(str(profile.get("id") or ""), {}),
                    )
                )
        else:
            adapters.append(_personal_api_item())

    runtimes = [
        {
            "id": str(row["id"]),
            "requirement": "required",
            "state": "ready" if row.get("ready") is True else "blocked",
            "installed": row.get("installed") is True,
            "version": row.get("version"),
            "minimum_version": row.get("minimum_version"),
            "action_code": None
            if row.get("ready") is True
            else f"install_or_update:{row['id']}",
        }
        for row in inventory.get("runtimes", [])
        if isinstance(row, Mapping)
        and row.get("requirement") in {"required", "required_on_windows"}
    ]
    ready_primary = [
        item["id"]
        for item in adapters
        if item.get("primary_candidate") is True and item["state"] == "ready"
    ]
    declared_primary = [
        item["id"] for item in adapters if item.get("primary_candidate") is True
    ]
    lead_channel = {
        "requirement": "required",
        "state": "ready" if ready_primary else "blocked",
        "ready_adapter_ids": ready_primary,
        "declared_adapter_ids": declared_primary,
        "action_code": (
            None
            if ready_primary
            else "choose_lead_channel"
            if not declared_primary
            else "complete_lead_adapter_verification"
        ),
    }
    blockers = [
        f"runtime:{row['id']}" for row in runtimes if row["state"] == "blocked"
    ]
    if lead_channel["state"] == "blocked":
        blockers.append("lead_channel")
    return {
        "schema_version": SCHEMA_VERSION,
        "needs_hash": sealed["assessment_hash"],
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
            "installations_attempted": False,
            "terms_accepted": False,
        },
        "runtimes": runtimes,
        "adapters": adapters,
        "lead_channel": lead_channel,
        "summary": {
            "ready": not blockers,
            "blockers": blockers,
            "requested_adapter_count": len(adapters),
            "optional_local_present": any(
                item["id"] in {"ollama", "lmstudio"} for item in adapters
            ),
        },
    }


def _adapter_item(
    adapter_id: str,
    *,
    requirement: str,
    observation: Mapping[str, Any] | None,
    support: Mapping[str, Any],
    cli_contract: Mapping[str, Any] | None,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    cli = (observation or {}).get("cli")
    installed = isinstance(cli, Mapping) and cli.get("installed") is True
    version = cli.get("version") if isinstance(cli, Mapping) else None
    minimum = (cli_contract or {}).get("minimum_version")
    auth = str((observation or {}).get("authentication_status") or "not_checked")
    health = str((observation or {}).get("health_status") or "untested")
    stages = {
        "installation": "passed" if installed else "failed",
        "version": (
            "passed"
            if installed and version_meets_minimum(version, minimum)
            else "failed"
            if installed and minimum
            else "not_checked"
        ),
        "authentication": (
            _evidence_stage(evidence, "authentication")
            if "authentication" in evidence
            else "passed"
            if auth in {"authenticated", "not_applicable"}
            else "failed"
            if auth == "not_authenticated"
            else "not_checked"
        ),
        "catalog": _evidence_stage(evidence, "catalog"),
        "health": (
            _evidence_stage(evidence, "health")
            if "health" in evidence
            else "passed"
            if health == "ok"
            else "failed"
            if health in {"failed", "degraded", "unavailable"}
            else "not_checked"
        ),
        "contract": _evidence_stage(evidence, "contract"),
    }
    state = (
        "ready"
        if all(stages[key] == "passed" for key in _STAGE_ORDER)
        else "blocked"
        if any(stages[key] == "failed" for key in _STAGE_ORDER)
        else "unverified"
    )
    return {
        "id": adapter_id,
        "requirement": requirement,
        "setup_class": support["setup_class"],
        "primary_candidate": support["setup_class"] == "primary_option",
        "state": state,
        "stages": stages,
        "human_auth_required": bool(
            (support.get("authentication") or {}).get("human_required")
        ),
        "automatic_install": False,
        "install_guidance": (support.get("install") or {}).get("windows"),
        "auth_guidance": (support.get("authentication") or {}).get("command"),
    }


def _personal_api_item() -> dict[str, Any]:
    return {
        "id": "personal_api",
        "requirement": "recommended",
        "setup_class": "owner_selected_api",
        "primary_candidate": False,
        "state": "unverified",
        "stages": {
            "installation": "not_applicable",
            "version": "not_applicable",
            "authentication": "not_checked",
            "catalog": "not_checked",
            "health": "not_checked",
            "contract": "not_checked",
        },
        "human_auth_required": True,
        "automatic_install": False,
        "install_guidance": None,
        "auth_guidance": "Selecciona proveedor y guarda una referencia de secreto.",
    }


def _api_profile_item(
    profile: Mapping[str, Any],
    *,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    profile_id = str(profile.get("id") or "").strip()
    if not profile_id or str(profile.get("channel") or "") != "api":
        raise ValueError("guided_setup_preparation_api_profile_invalid")
    options = [
        row
        for row in profile.get("model_options", [])
        if isinstance(row, Mapping)
    ]
    primary_candidate = any(
        role in {"lead", "team_lead", "lead_executor"}
        for option in options
        for role in (
            list(option.get("best_for") or ())
            + list(option.get("allowed_roles") or ())
        )
    )
    stages = {
        "installation": "not_applicable",
        "version": "not_applicable",
        "authentication": _evidence_stage(evidence, "authentication"),
        "catalog": _evidence_stage(evidence, "catalog"),
        "health": _evidence_stage(evidence, "health"),
        "contract": _evidence_stage(evidence, "contract"),
    }
    relevant = ("authentication", "catalog", "health", "contract")
    state = (
        "ready"
        if all(stages[key] == "passed" for key in relevant)
        else "blocked"
        if any(stages[key] == "failed" for key in relevant)
        else "unverified"
    )
    return {
        "id": profile_id,
        "requirement": "recommended",
        "setup_class": "owner_selected_api",
        "primary_candidate": primary_candidate,
        "state": state,
        "stages": stages,
        "human_auth_required": True,
        "automatic_install": False,
        "install_guidance": None,
        "auth_guidance": "Guarda la clave en el vault y conserva solo secret_ref.",
    }


def _evidence_stage(evidence: Mapping[str, Any], key: str) -> str:
    value = str(evidence.get(key) or "not_checked")
    if value not in {"passed", "failed", "not_checked"}:
        raise ValueError(f"guided_setup_preparation_{key}_evidence_invalid")
    return value
