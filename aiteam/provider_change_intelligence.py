"""Contrato canónico de hechos para cambios de proveedores.

Este módulo no detecta releases ni consulta la red. Compone el inventario de
superficies que P0.N.2 deberá observar y conserva ``unknown`` hasta que exista
una fuente admisible. No concede autoridad de routing ni de actualización.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.installation_support import load_installation_support_contract
from aiteam.mcp_catalog import list_mcp_catalog
from aiteam.user_config import DEFAULT_ADAPTER_PROFILES

SCHEMA_VERSION = "provider_change_intelligence_v1"
REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "config" / "provider_change_intelligence.v1.json"
_FACT_NAMES = (
    "installed_version",
    "supported_version",
    "latest_known_version",
)


def load_provider_change_contract(
    path: Path = CONTRACT_PATH,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_provider_change_contract(payload)
    return payload


def build_provider_change_inventory(
    *,
    profiles: list[dict[str, Any]] | None = None,
    installation_support: Mapping[str, Any] | None = None,
    mcp_entries: list[dict[str, Any]] | None = None,
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Enumera perfiles/canales y MCP sin ejecutar probes ni leer secretos."""
    policy = dict(contract or load_provider_change_contract())
    validate_provider_change_contract(policy)
    profile_rows = profiles if profiles is not None else DEFAULT_ADAPTER_PROFILES
    support = dict(
        installation_support or load_installation_support_contract()
    )
    mcp_rows = mcp_entries if mcp_entries is not None else list_mcp_catalog()
    cli_policies = {
        str(row["adapter_id"]): row
        for row in support["cli_version_contract"]["entries"]
    }

    components: list[dict[str, Any]] = []
    for profile in profile_rows:
        profile_id = str(profile.get("id") or "").strip()
        channel_id = str(profile.get("channel") or "").strip()
        provider_id = str(profile.get("provider") or "").strip()
        adapter_type = str(profile.get("adapter_type") or "").strip()
        if not all((profile_id, channel_id, provider_id, adapter_type)):
            raise ValueError("provider profile identity is incomplete")
        config = profile.get("config")
        config = config if isinstance(config, dict) else {}
        scope = {
            "scope_id": f"profile:{profile_id}",
            "profile_id": profile_id,
            "channel_id": channel_id,
            "provider_id": provider_id,
        }
        components.append(
            _component(
                **scope,
                component_id=f"adapter:{adapter_type}",
                surface="internal_adapter",
                supported_value=f"adapter_contract:{adapter_type}:v1",
                supported_reference="aiteam/adapters/registry.py",
                latest_source="official_release",
                latest_reference="repository_release",
            )
        )
        components.append(
            _component(
                **scope,
                component_id=f"catalog:{profile_id}",
                surface="model_catalog",
                supported_value="model_catalog_read_model_v2",
                supported_reference="aiteam/model_catalog_read_model.py",
                installed_source="authenticated_discovery",
                installed_reference=f"profile_catalog:{profile_id}",
                latest_source="authenticated_discovery",
                latest_reference=f"profile_catalog:{profile_id}",
            )
        )
        cli_policy = cli_policies.get(profile_id)
        if adapter_type == "subscription_cli":
            cli_kind = str(config.get("cli_kind") or provider_id).strip()
            components.append(
                _component(
                    **scope,
                    component_id=f"cli:{cli_kind}",
                    surface="cli_package",
                    supported_value=(
                        str(cli_policy["validated_version"])
                        if cli_policy
                        else None
                    ),
                    supported_reference=(
                        "config/installation_support.v1.json"
                        if cli_policy
                        else "aiteam/user_config.py"
                    ),
                    supported_reason=(
                        None
                        if cli_policy
                        else str(profile.get("status") or "pin_not_declared")
                    ),
                    latest_source="official_release",
                    latest_reference=f"provider_release:{cli_kind}",
                )
            )
            local_provider = str(config.get("local_provider") or "").strip()
            if local_provider:
                components.append(
                    _component(
                        **scope,
                        component_id=f"cli:{local_provider}",
                        surface="cli_package",
                        supported_value=_support_version_for_cli(
                            support, local_provider
                        ),
                        supported_reference="config/installation_support.v1.json",
                        latest_source="official_release",
                        latest_reference=f"provider_release:{local_provider}",
                    )
                )
        else:
            api_version = str(config.get("api_version") or "").strip()
            supported = (
                f"api:{provider_id}:{api_version}"
                if api_version
                else f"adapter_api_contract:{adapter_type}:v1"
            )
            components.append(
                _component(
                    **scope,
                    component_id=f"api:{provider_id}:{adapter_type}",
                    surface="sdk_api",
                    supported_value=supported,
                    supported_reference=f"aiteam/adapters/{_adapter_module(adapter_type)}.py",
                    installed_source="authenticated_discovery",
                    installed_reference=f"provider_endpoint:{provider_id}",
                    latest_source="official_release",
                    latest_reference=f"provider_api_release:{provider_id}",
                )
            )

    for entry in mcp_rows:
        catalog_id = str(entry.get("id") or "").strip()
        publisher = str(entry.get("publisher") or "").strip()
        version = str(entry.get("distribution_version") or "").strip()
        if not all((catalog_id, publisher, version)):
            raise ValueError("MCP catalog identity is incomplete")
        components.append(
            _component(
                scope_id=f"mcp:{catalog_id}",
                profile_id=None,
                channel_id="mcp",
                provider_id=publisher,
                component_id=f"mcp:{catalog_id}",
                surface="mcp_server",
                supported_value=version,
                supported_reference="aiteam/mcp_catalog.py",
                installed_source="local_resolution",
                installed_reference=f"mcp_executable:{catalog_id}",
                latest_source="official_release",
                latest_reference=str(entry.get("homepage") or ""),
            )
        )

    for component in components:
        component["dimensions"] = list(
            policy["surface_dimensions"][component["surface"]]
        )
    inventory = {
        "schema_version": SCHEMA_VERSION,
        "contract_updated_at": policy["updated_at"],
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "login_attempted": False,
            "inference_attempted": False,
            "network_attempted": False,
            "routing_authority_granted": False,
        },
        "components": sorted(
            components,
            key=lambda row: (
                row["scope_id"],
                row["surface"],
                row["component_id"],
            ),
        ),
    }
    inventory["inventory_sha256"] = _inventory_digest(inventory)
    validate_provider_change_inventory(inventory, contract=policy)
    return inventory


def validate_provider_change_contract(contract: Mapping[str, Any]) -> None:
    if contract.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("provider change contract schema drift")
    _parse_timestamp(f"{contract.get('updated_at')}T00:00:00+00:00")
    surfaces = contract.get("surfaces")
    if not isinstance(surfaces, list) or len(surfaces) != len(set(surfaces)):
        raise ValueError("provider change surfaces must be unique")
    required_surfaces = {
        "cli_package",
        "mcp_server",
        "sdk_api",
        "internal_adapter",
        "model_catalog",
    }
    if set(surfaces) != required_surfaces:
        raise ValueError("provider change surface coverage drift")
    dimensions = contract.get("surface_dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != required_surfaces:
        raise ValueError("provider change surface dimensions drift")
    for surface, values in dimensions.items():
        if (
            not isinstance(values, list)
            or not values
            or len(values) != len(set(values))
            or not all(str(value).strip() for value in values)
        ):
            raise ValueError(
                f"provider change dimensions are invalid: {surface}"
            )
    source_kinds = contract.get("source_kinds")
    facts = contract.get("facts")
    if not isinstance(source_kinds, dict) or not isinstance(facts, dict):
        raise TypeError("provider change source/fact contracts must be objects")
    if set(facts) != set(_FACT_NAMES):
        raise ValueError("provider change fact coverage drift")
    for fact_name, fact in facts.items():
        allowed = fact.get("allowed_sources")
        if not isinstance(allowed, list) or not allowed:
            raise ValueError(f"{fact_name} must declare allowed sources")
        if not set(allowed) <= set(source_kinds):
            raise ValueError(f"{fact_name} references an unknown source")
        for source in allowed:
            authoritative = source_kinds[source].get("authoritative_for")
            if fact_name not in (authoritative or []):
                raise ValueError(f"{source} is not authoritative for {fact_name}")
    if "web" in source_kinds or "commercial_name" in source_kinds:
        raise ValueError("unprovenanced web/name sources are forbidden")


def validate_provider_change_inventory(
    inventory: Mapping[str, Any],
    *,
    contract: Mapping[str, Any] | None = None,
) -> None:
    policy = dict(contract or load_provider_change_contract())
    validate_provider_change_contract(policy)
    if inventory.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("provider change inventory schema drift")
    if inventory.get("scope") != {
        "read_only": True,
        "secrets_read": False,
        "login_attempted": False,
        "inference_attempted": False,
        "network_attempted": False,
        "routing_authority_granted": False,
    }:
        raise ValueError("provider change inventory scope drift")
    components = inventory.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("provider change components must be non-empty")
    identities: set[tuple[str, str, str]] = set()
    profile_surfaces: dict[str, set[str]] = {}
    for row in components:
        if not isinstance(row, dict):
            raise TypeError("provider change component must be an object")
        identity = (
            str(row.get("scope_id") or ""),
            str(row.get("surface") or ""),
            str(row.get("component_id") or ""),
        )
        if not all(identity) or identity in identities:
            raise ValueError("provider change component identity drift")
        identities.add(identity)
        if row.get("surface") not in policy["surfaces"]:
            raise ValueError("provider change component surface drift")
        if row.get("dimensions") != policy["surface_dimensions"][row["surface"]]:
            raise ValueError("provider change component dimensions drift")
        profile_id = row.get("profile_id")
        if profile_id is not None:
            profile_surfaces.setdefault(str(profile_id), set()).add(
                str(row["surface"])
            )
        facts = row.get("facts")
        if not isinstance(facts, dict) or set(facts) != set(_FACT_NAMES):
            raise ValueError("provider change component fact coverage drift")
        for fact_name, fact in facts.items():
            _validate_fact(fact_name, fact, policy)
    expected_profiles = {
        str(row["id"]) for row in DEFAULT_ADAPTER_PROFILES
    }
    if set(profile_surfaces) != expected_profiles:
        raise ValueError("provider change profile coverage drift")
    for profile_id, surfaces in profile_surfaces.items():
        if not {"internal_adapter", "model_catalog"} <= surfaces:
            raise ValueError(
                f"provider profile lacks adapter/catalog surfaces: {profile_id}"
            )
        if not ({"cli_package", "sdk_api"} & surfaces):
            raise ValueError(
                f"provider profile lacks transport surface: {profile_id}"
            )
    expected_digest = _inventory_digest(
        {
            key: deepcopy(value)
            for key, value in inventory.items()
            if key != "inventory_sha256"
        }
    )
    if inventory.get("inventory_sha256") != expected_digest:
        raise ValueError("provider change inventory digest drift")


def _component(
    *,
    scope_id: str,
    profile_id: str | None,
    channel_id: str,
    provider_id: str,
    component_id: str,
    surface: str,
    supported_value: str | None,
    supported_reference: str,
    latest_source: str,
    latest_reference: str,
    installed_source: str = "local_resolution",
    installed_reference: str | None = None,
    supported_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "scope_id": scope_id,
        "profile_id": profile_id,
        "channel_id": channel_id,
        "provider_id": provider_id,
        "component_id": component_id,
        "surface": surface,
        "facts": {
            "installed_version": _unknown_fact(
                source_kind=installed_source,
                reference=installed_reference or component_id,
                reason="probe_not_run",
            ),
            "supported_version": (
                _known_fact(
                    value=supported_value,
                    source_kind="repo_contract",
                    reference=supported_reference,
                    observed_at="2026-07-30T00:00:00+00:00",
                )
                if supported_value
                else _unknown_fact(
                    source_kind="repo_contract",
                    reference=supported_reference,
                    reason=supported_reason or "support_pin_not_declared",
                )
            ),
            "latest_known_version": _unknown_fact(
                source_kind=latest_source,
                reference=latest_reference,
                reason="source_not_queried",
            ),
        },
    }


def _known_fact(
    *,
    value: str,
    source_kind: str,
    reference: str,
    observed_at: str,
) -> dict[str, Any]:
    return {
        "state": "known",
        "value": value,
        "reason": None,
        "source": {
            "kind": source_kind,
            "reference": reference,
            "official": source_kind not in {"repo_contract", "local_resolution"},
            "observed_at": observed_at,
        },
    }


def _unknown_fact(
    *, source_kind: str, reference: str, reason: str
) -> dict[str, Any]:
    return {
        "state": "unknown",
        "value": None,
        "reason": reason,
        "source": {
            "kind": source_kind,
            "reference": reference,
            "official": source_kind not in {"repo_contract", "local_resolution"},
            "observed_at": None,
        },
    }


def _validate_fact(
    fact_name: str,
    fact: Any,
    contract: Mapping[str, Any],
) -> None:
    if not isinstance(fact, dict):
        raise TypeError("provider change fact must be an object")
    if set(fact) != {"state", "value", "reason", "source"}:
        raise ValueError("provider change fact fields drift")
    state = fact["state"]
    if state not in contract["fact_states"]:
        raise ValueError("provider change fact state drift")
    value = fact["value"]
    if (state == "known") != bool(str(value or "").strip()):
        raise ValueError("known facts require a value; unknown facts forbid one")
    source = fact["source"]
    if not isinstance(source, dict) or set(source) != {
        "kind",
        "reference",
        "official",
        "observed_at",
    }:
        raise ValueError("provider change source fields drift")
    kind = source["kind"]
    if kind not in contract["facts"][fact_name]["allowed_sources"]:
        raise ValueError(f"source {kind} cannot establish {fact_name}")
    if not str(source["reference"] or "").strip():
        raise ValueError("provider change source reference is required")
    requires_official = contract["source_kinds"][kind]["requires_official"]
    if bool(source["official"]) is not bool(requires_official):
        raise ValueError("provider change source official flag drift")
    observed_at = source["observed_at"]
    if state == "known":
        _parse_timestamp(observed_at)
    elif observed_at is not None:
        raise ValueError("unknown facts cannot claim an observation timestamp")


def _support_version_for_cli(
    support: Mapping[str, Any], cli_id: str
) -> str | None:
    for row in support.get("adapters", []):
        if str(row.get("id") or "") == cli_id:
            value = str(row.get("validated_version") or "").strip()
            return value or None
    return None


def _adapter_module(adapter_type: str) -> str:
    return {
        "openai_api": "openai_adapter",
        "gemini_api": "gemini_adapter",
        "openai_compatible_api": "openai_compatible_adapter",
        "anthropic_sonnet": "anthropic_adapter",
    }.get(adapter_type, "registry")


def _parse_timestamp(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("provider change timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("provider change timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _inventory_digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
