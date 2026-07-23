from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = PROJECT_ROOT / "config" / "configuration_layers.v1.json"

_INLINE_SECRET_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "private_key",
}


@dataclass(frozen=True)
class ConfigurationResolution:
    values: dict[str, Any]
    provenance: dict[str, str]

    def source_for(self, path: str) -> str | None:
        return self.provenance.get(path)


def load_configuration_contract(path: Path | None = None) -> dict[str, Any]:
    contract = json.loads((path or CONTRACT_PATH).read_text(encoding="utf-8"))
    if contract.get("schema_version") != "configuration_layers_v1":
        raise ValueError("unsupported configuration layer contract")
    layers = contract.get("precedence_low_to_high")
    if not isinstance(layers, list) or [row.get("id") for row in layers] != [
        "versioned_defaults",
        "user_machine",
        "environment",
        "project",
        "run_override",
    ]:
        raise ValueError("invalid configuration layer precedence")
    return contract


def deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Merge mappings recursively; lists and scalar values are atomic overrides."""
    result = _clone_mapping(base)
    for key, value in overlay.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            result[key] = deep_merge(current, value)
        else:
            result[key] = _clone(value)
    return result


def resolve_configuration(
    layers: Iterable[tuple[str, Mapping[str, Any] | None]],
    *,
    allowed_layer_ids: set[str] | None = None,
) -> ConfigurationResolution:
    """Resolve already-allowlisted layers and retain leaf-level provenance.

    Callers must map environment variables explicitly; this function never reads
    the process environment wholesale. Secret values are rejected because
    credentials are injected only after configuration resolution.
    """
    resolved: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    seen: set[str] = set()
    for layer_id, payload in layers:
        if allowed_layer_ids is not None and layer_id not in allowed_layer_ids:
            raise ValueError(f"configuration layer is not allowed here: {layer_id}")
        if layer_id in seen:
            raise ValueError(f"duplicate configuration layer: {layer_id}")
        seen.add(layer_id)
        if payload is None:
            continue
        if not isinstance(payload, Mapping):
            raise TypeError(f"configuration layer {layer_id} must be a mapping")
        _assert_no_inline_secrets(payload)
        resolved = deep_merge(resolved, payload)
        for path in _leaf_paths(payload):
            provenance[path] = layer_id
    return ConfigurationResolution(values=resolved, provenance=provenance)


def _leaf_paths(value: Mapping[str, Any], prefix: str = "") -> list[str]:
    paths: list[str] = []
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping) and item:
            paths.extend(_leaf_paths(item, path))
        else:
            paths.append(path)
    return paths


def _assert_no_inline_secrets(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).strip().lower() in _INLINE_SECRET_KEYS and item not in (None, ""):
                raise ValueError(
                    f"inline secret at {key}; configuration may contain only secret references"
                )
            _assert_no_inline_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_inline_secrets(item)


def _clone_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _clone(item) for key, item in value.items()}


def _clone(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _clone_mapping(value)
    if isinstance(value, list):
        return [_clone(item) for item in value]
    return value
