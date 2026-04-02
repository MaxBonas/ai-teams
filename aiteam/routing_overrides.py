from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path

from aiteam.config import RouterPolicy
from aiteam.types import Role


def _normalize_name_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = str(raw or "").strip().lower()
        if not item or item in seen:
            continue
        output.append(item)
        seen.add(item)
    return output


@dataclass
class RoleOverride:
    providers: list[str] | None = None
    models: list[str] | None = None
    primary_provider: str | None = None
    excluded_providers: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: object) -> "RoleOverride":
        if not isinstance(payload, dict):
            return cls()
        providers = payload.get("providers")
        models = payload.get("models")
        primary_provider = str(payload.get("primary_provider", "") or "").strip().lower() or None
        excluded_providers = _normalize_name_list(payload.get("excluded_providers", []))
        return cls(
            providers=_normalize_name_list(providers) if isinstance(providers, list) else None,
            models=_normalize_name_list(models) if isinstance(models, list) else None,
            primary_provider=primary_provider,
            excluded_providers=excluded_providers,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "providers": list(self.providers) if self.providers is not None else None,
            "models": list(self.models) if self.models is not None else None,
            "primary_provider": self.primary_provider,
            "excluded_providers": list(self.excluded_providers),
        }

    def is_empty(self) -> bool:
        return (
            self.providers is None
            and self.models is None
            and not self.primary_provider
            and not self.excluded_providers
        )


@dataclass
class RoutingOverrides:
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    overrides_by_role: dict[str, RoleOverride] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: object) -> "RoutingOverrides":
        if not isinstance(payload, dict):
            return cls()
        overrides_payload = payload.get("overrides_by_role", {})
        overrides_by_role: dict[str, RoleOverride] = {}
        if isinstance(overrides_payload, dict):
            for raw_role, raw_override in overrides_payload.items():
                role_name = str(raw_role or "").strip().lower()
                if not role_name:
                    continue
                override = RoleOverride.from_dict(raw_override)
                if not override.is_empty():
                    overrides_by_role[role_name] = override
        return cls(
            version=int(payload.get("version", 1) or 1),
            created_at=str(payload.get("created_at", "") or ""),
            updated_at=str(payload.get("updated_at", "") or ""),
            overrides_by_role=overrides_by_role,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "version": int(self.version or 1),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "overrides_by_role": {
                role: override.to_dict()
                for role, override in sorted(self.overrides_by_role.items())
                if not override.is_empty()
            },
        }

    def has_entries(self) -> bool:
        return any(not override.is_empty() for override in self.overrides_by_role.values())


def load_overrides(runtime_dir: Path) -> RoutingOverrides:
    path = Path(runtime_dir) / "routing_overrides.json"
    if not path.exists():
        return RoutingOverrides()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return RoutingOverrides()
    return RoutingOverrides.from_dict(payload)


def save_overrides(runtime_dir: Path, overrides: RoutingOverrides) -> None:
    runtime_dir = Path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / "routing_overrides.json"
    tmp_path = runtime_dir / "routing_overrides.tmp"
    now = datetime.now(timezone.utc).isoformat()
    payload = RoutingOverrides(
        version=int(overrides.version or 1),
        created_at=overrides.created_at or now,
        updated_at=now,
        overrides_by_role=dict(overrides.overrides_by_role),
    )
    tmp_path.write_text(
        json.dumps(payload.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def reset_overrides(runtime_dir: Path) -> None:
    path = Path(runtime_dir) / "routing_overrides.json"
    path.unlink(missing_ok=True)


def validate_overrides(overrides: RoutingOverrides, policy: RouterPolicy) -> list[str]:
    errors: list[str] = []
    valid_roles = {role.value for role in Role}
    for role_name, override in sorted(overrides.overrides_by_role.items()):
        if role_name not in valid_roles:
            errors.append(f"{role_name}:invalid_role")
            continue
        default_providers = _normalize_name_list(
            policy.role_provider_preferences.get(role_name, [])
        )
        effective_providers = (
            list(override.providers) if override.providers is not None else list(default_providers)
        )
        excluded = set(_normalize_name_list(override.excluded_providers))
        if override.primary_provider:
            if override.primary_provider in excluded:
                errors.append(f"{role_name}:primary_provider_excluded")
            elif override.primary_provider not in effective_providers:
                effective_providers.insert(0, override.primary_provider)
        filtered_providers = [item for item in effective_providers if item not in excluded]
        if not filtered_providers:
            errors.append(f"{role_name}:no_provider_remaining")
        if override.models is not None and not list(override.models):
            errors.append(f"{role_name}:models_empty")
    return errors


def apply_overrides_to_policy(
    policy: RouterPolicy,
    overrides: RoutingOverrides,
) -> RouterPolicy:
    merged = deepcopy(policy)
    valid_roles = {role.value for role in Role}
    for role_name, override in overrides.overrides_by_role.items():
        role_key = str(role_name or "").strip().lower()
        if not role_key or role_key not in valid_roles:
            continue
        base_providers = (
            list(override.providers)
            if override.providers is not None
            else list(merged.role_provider_preferences.get(role_key, []) or [])
        )
        excluded = _normalize_name_list(override.excluded_providers)
        primary_provider = str(override.primary_provider or "").strip().lower()
        filtered_providers = [item for item in base_providers if item not in set(excluded)]
        if primary_provider and primary_provider not in filtered_providers and primary_provider not in set(excluded):
            filtered_providers.insert(0, primary_provider)
        if primary_provider:
            filtered_providers = [primary_provider] + [
                item for item in filtered_providers if item != primary_provider
            ]
            merged.role_primary_provider[role_key] = primary_provider
        else:
            merged.role_primary_provider.pop(role_key, None)
        merged.role_provider_preferences[role_key] = _normalize_name_list(filtered_providers)
        if excluded:
            merged.role_provider_exclusions[role_key] = list(excluded)
        else:
            merged.role_provider_exclusions.pop(role_key, None)
        if override.models is not None:
            merged.role_model_preferences[role_key] = _normalize_name_list(override.models)
    return merged
