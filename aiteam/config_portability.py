from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from aiteam.configuration_layers import deep_merge, load_configuration_contract
from aiteam.user_config import user_config_dir


SCHEMA_VERSION = "aiteam_portable_config_v1"
PORTABLE_USER_SETTINGS = {"locale", "theme", "model_default_rollout"}
PORTABLE_PROJECT_ROOTS = {
    "version",
    "adapter_profile_ids",
    "adapter_policy",
    "autonomy",
}
_SECRET_VALUE_KEYS = {
    "api_key",
    "api_keys",
    "apikey",
    "access_token",
    "refresh_token",
    "password",
    "passwords",
    "private_key",
    "secret",
    "secrets",
    "token",
    "tokens",
}
_SENSITIVE_CONTAINERS = {
    "credentials",
    "environment",
    "env",
    "extra_headers",
    "headers",
}
_RUNTIME_STATE_KEYS = {
    "authenticated",
    "availability_reason",
    "available",
    "catalog_status",
    "catalog_candidate_id",
    "candidate_id",
    "cli_status",
    "compatibility",
    "fit_reason",
    "health",
    "identity",
    "installed",
    "last_seen",
    "last_tested",
    "model_catalog",
    "model_role_score",
    "rate_limited_until",
    "recommended",
    "role_score",
    "selection_reason",
    "selectable",
    "tested_at",
}
_WINDOWS_PATH_RE = re.compile(r"(?i)(?:^|[\s\"'])(?:[a-z]:[\\/]|\\\\[^\\/\s]+[\\/])")
_POSIX_PATH_RE = re.compile(r"(?:^|[\s\"'])/(?!/)")
_STATE_PATH_RE = re.compile(
    r"(?i)(?:^|[\\/])(?:runtime|venv|\.venv|node_modules)(?:[\\/]|$)|(?:^|[\\/])[^\\/]+\.db$"
)
_URI_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{12,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{12,}\b", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_OMIT = object()


class PortableConfigurationError(ValueError):
    pass


def export_portable_configuration(
    *,
    source_user_config_dir: Path | None = None,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a redacted, path-independent package without reading secret/state files."""
    source_dir = Path(source_user_config_dir or user_config_dir())
    omissions: list[dict[str, str]] = []

    raw_settings = _read_json_object(source_dir / "settings.json", optional=True)
    settings: dict[str, Any] = {}
    for key, value in raw_settings.items():
        if key not in PORTABLE_USER_SETTINGS:
            omissions.append({"code": "machine_setting_excluded", "field": f"user.settings.{key}"})
            continue
        clean = _sanitize(value, f"user.settings.{key}", omissions)
        if clean is not _OMIT:
            settings[key] = clean

    raw_profiles = _read_json_object(
        source_dir / "adapter_profiles.json", optional=True
    ).get("profiles", [])
    if not isinstance(raw_profiles, list):
        raise PortableConfigurationError("adapter_profiles.json profiles must be a list")
    profiles: list[dict[str, Any]] = []
    for index, profile in enumerate(raw_profiles):
        if not isinstance(profile, Mapping):
            raise PortableConfigurationError(f"adapter profile {index} must be an object")
        profile_id = str(profile.get("id") or "").strip()
        if not profile_id:
            raise PortableConfigurationError(f"adapter profile {index} has no id")
        clean = _sanitize(profile, f"user.adapter_profiles.{profile_id}", omissions)
        if not isinstance(clean, dict):
            raise PortableConfigurationError(f"adapter profile {profile_id} is not portable")
        profiles.append(clean)
    profiles.sort(key=lambda row: str(row.get("id") or ""))

    project_payload: dict[str, Any] | None = None
    selected_profile_ids: list[str] = []
    if project_dir is not None:
        project_path = Path(project_dir)
        config_path = (
            project_path / "project_config.json"
            if project_path.name == ".aiteam"
            else project_path / ".aiteam" / "project_config.json"
        )
        raw_project = _read_json_object(config_path, optional=True)
        contract_roots = set(
            load_configuration_contract()["project_limits"]["allowed_roots"]
        ) | {"version"}
        if not PORTABLE_PROJECT_ROOTS <= contract_roots:
            raise PortableConfigurationError(
                "portable project roots exceed the configuration contract"
            )
        project_payload = {}
        for key, value in raw_project.items():
            if key not in PORTABLE_PROJECT_ROOTS:
                code = (
                    "freeform_project_content_excluded"
                    if key == "instructions"
                    else "project_field_excluded"
                )
                omissions.append(
                    {"code": code, "field": f"project.config.{key}"}
                )
                continue
            clean = _sanitize(value, f"project.config.{key}", omissions)
            if clean is not _OMIT:
                project_payload[key] = clean
        raw_ids = project_payload.get("adapter_profile_ids", [])
        if isinstance(raw_ids, list):
            selected_profile_ids = sorted(
                {str(value).strip() for value in raw_ids if str(value).strip()}
            )

    profile_ids = sorted(
        {str(row.get("id") or "") for row in profiles} | set(selected_profile_ids)
    )
    package: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "user": {
            "settings": settings,
            "adapter_profiles": profiles,
        },
        "project": {"config": project_payload} if project_payload is not None else None,
        "omissions": sorted(omissions, key=lambda row: (row["code"], row["field"])),
        "requires_machine_setup": [
            {
                "profile_id": profile_id,
                "actions": [
                    "install_or_locate_exact_channel_if_missing",
                    "configure_local_credential_or_cli_session",
                    "run_health_and_exact_model_probe",
                ],
                "selectable_after_import": False,
            }
            for profile_id in profile_ids
        ],
    }
    _assert_portable(package)
    package["integrity"] = {
        "algorithm": "sha256",
        "payload_sha256": _payload_hash(package),
    }
    return package


def inspect_portable_configuration(package: Mapping[str, Any]) -> dict[str, Any]:
    validated = _validate_package(package)
    user = validated["user"]
    project = validated.get("project")
    return {
        "valid": True,
        "schema_version": SCHEMA_VERSION,
        "adapter_profile_ids": [
            str(row.get("id") or "") for row in user["adapter_profiles"]
        ],
        "portable_setting_keys": sorted(user["settings"]),
        "has_project_config": bool(
            isinstance(project, Mapping) and isinstance(project.get("config"), Mapping)
        ),
        "omission_codes": sorted(
            {str(row.get("code") or "") for row in validated["omissions"]}
        ),
        "requires_machine_setup": validated["requires_machine_setup"],
    }


def import_portable_configuration(
    package: Mapping[str, Any],
    *,
    target_user_config_dir: Path | None = None,
    project_dir: Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Preflight or apply a merge. Imported profiles always lose previous health."""
    validated = _validate_package(package)
    target_dir = Path(target_user_config_dir or user_config_dir())
    imported_profiles = validated["user"]["adapter_profiles"]
    profile_ids = [str(row["id"]) for row in imported_profiles]
    project_section = validated.get("project")
    has_project = isinstance(project_section, Mapping) and isinstance(
        project_section.get("config"), Mapping
    )
    blockers: list[str] = []
    if has_project and project_dir is None:
        blockers.append("project_target_required")
    if project_dir is not None and not Path(project_dir).is_dir():
        blockers.append("project_target_not_found")

    report = {
        "schema_version": "aiteam_portable_import_report_v1",
        "apply_requested": apply,
        "applied": False,
        "blockers": blockers,
        "adapter_profile_ids": profile_ids,
        "settings_keys": sorted(validated["user"]["settings"]),
        "project_config_included": has_project,
        "health_action": "invalidate_imported_profiles_until_retested",
        "secrets_action": "unchanged_local_store",
        "next_actions": [
            "configure credentials or CLI sessions on this machine",
            "run health and exact-model probes before selection",
        ],
    }

    settings_path = target_dir / "settings.json"
    settings = _read_json_object(settings_path, optional=True)
    settings = deep_merge(settings, validated["user"]["settings"])

    profiles_path = target_dir / "adapter_profiles.json"
    profile_document = _read_json_object(profiles_path, optional=True)
    existing_profiles = profile_document.get("profiles", [])
    if not isinstance(existing_profiles, list):
        raise PortableConfigurationError("target adapter profiles must be a list")
    if any(
        not isinstance(row, Mapping) or not str(row.get("id") or "")
        for row in existing_profiles
    ):
        raise PortableConfigurationError(
            "target adapter profiles contain an invalid profile"
        )
    profiles_by_id = {
        str(row.get("id")): dict(row)
        for row in existing_profiles
        if isinstance(row, Mapping) and str(row.get("id") or "")
    }
    for imported in imported_profiles:
        profile_id = str(imported["id"])
        profiles_by_id[profile_id] = deep_merge(
            profiles_by_id.get(profile_id, {}), imported
        )
    profile_document["profiles"] = [
        profiles_by_id[key] for key in sorted(profiles_by_id)
    ]

    health_path = target_dir / "adapter_health.json"
    health_document = _read_json_object(health_path, optional=True)
    health_profiles = health_document.get("profiles", {})
    if not isinstance(health_profiles, dict):
        raise PortableConfigurationError("target adapter health profiles must be an object")
    for profile_id in profile_ids:
        health_profiles[profile_id] = {
            "status": "untested",
            "reason": "portable_configuration_imported_requires_retest",
        }
    health_document["profiles"] = health_profiles

    project_config_path: Path | None = None
    merged_project: dict[str, Any] | None = None
    if has_project and project_dir is not None and Path(project_dir).is_dir():
        project_root = Path(project_dir)
        runtime_dir = (
            project_root if project_root.name == ".aiteam" else project_root / ".aiteam"
        )
        project_config_path = runtime_dir / "project_config.json"
        existing_project = _read_json_object(project_config_path, optional=True)
        merged_project = deep_merge(existing_project, project_section["config"])

    report["profile_collisions"] = sorted(
        set(profile_ids) & {str(row["id"]) for row in existing_profiles}
    )
    report["project_fields"] = (
        sorted(project_section["config"]) if has_project else []
    )
    if not apply:
        return report
    if blockers:
        raise PortableConfigurationError(
            "import blocked: " + ", ".join(blockers)
        )

    _write_json_atomic(settings_path, settings)
    _write_json_atomic(profiles_path, profile_document)
    _write_json_atomic(health_path, health_document)

    if project_config_path is not None and merged_project is not None:
        _write_json_atomic(project_config_path, merged_project)

    report["applied"] = True
    return report


def write_portable_package(path: Path, package: Mapping[str, Any]) -> None:
    _validate_package(package)
    _write_json_atomic(Path(path), dict(package))


def read_portable_package(path: Path) -> dict[str, Any]:
    package = _read_json_object(Path(path), optional=False)
    return _validate_package(package)


def _validate_package(package: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(package, Mapping):
        raise PortableConfigurationError("portable package must be an object")
    payload = json.loads(json.dumps(package, ensure_ascii=False))
    allowed_top_level = {
        "schema_version",
        "created_at",
        "user",
        "project",
        "omissions",
        "requires_machine_setup",
        "integrity",
    }
    if set(payload) - allowed_top_level:
        raise PortableConfigurationError("portable package contains unknown top-level fields")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise PortableConfigurationError("unsupported portable configuration schema")
    user = payload.get("user")
    if not isinstance(user, dict):
        raise PortableConfigurationError("portable package user section is required")
    if set(user) - {"settings", "adapter_profiles"}:
        raise PortableConfigurationError("portable package contains unknown user fields")
    if not isinstance(user.get("settings"), dict):
        raise PortableConfigurationError("portable settings must be an object")
    if set(user["settings"]) - PORTABLE_USER_SETTINGS:
        raise PortableConfigurationError("portable package contains non-portable settings")
    profiles = user.get("adapter_profiles")
    if not isinstance(profiles, list) or any(not isinstance(row, dict) for row in profiles):
        raise PortableConfigurationError("portable adapter profiles must be a list")
    ids = [str(row.get("id") or "") for row in profiles]
    if any(not profile_id for profile_id in ids) or len(ids) != len(set(ids)):
        raise PortableConfigurationError("portable adapter profile ids must be unique")
    project = payload.get("project")
    if project is not None:
        if not isinstance(project, dict) or set(project) != {"config"}:
            raise PortableConfigurationError("portable project section is invalid")
        if not isinstance(project["config"], dict):
            raise PortableConfigurationError("portable project config must be an object")
        if set(project["config"]) - PORTABLE_PROJECT_ROOTS:
            raise PortableConfigurationError("portable project config contains forbidden fields")
    if not isinstance(payload.get("omissions"), list):
        raise PortableConfigurationError("portable omissions must be a list")
    if not isinstance(payload.get("requires_machine_setup"), list):
        raise PortableConfigurationError("machine setup requirements must be a list")
    integrity = payload.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("algorithm") != "sha256":
        raise PortableConfigurationError("portable package integrity is required")
    expected = str(integrity.get("payload_sha256") or "")
    actual = _payload_hash(payload)
    if not expected or not hmac.compare_digest(expected, actual):
        raise PortableConfigurationError("portable package integrity mismatch")
    _assert_portable({key: value for key, value in payload.items() if key != "integrity"})
    return payload


def _payload_hash(package: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in package.items() if key != "integrity"}
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _sanitize(value: Any, field: str, omissions: list[dict[str, str]]) -> Any:
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            lowered = key.strip().lower()
            child_field = f"{field}.{key}"
            if lowered in _SECRET_VALUE_KEYS:
                omissions.append({"code": "inline_secret_removed", "field": child_field})
                continue
            if lowered in _SENSITIVE_CONTAINERS:
                omissions.append(
                    {"code": "secret_container_removed", "field": child_field}
                )
                continue
            if lowered in _RUNTIME_STATE_KEYS:
                omissions.append(
                    {"code": "runtime_state_removed", "field": child_field}
                )
                continue
            sanitized = _sanitize(item, child_field, omissions)
            if sanitized is not _OMIT:
                clean[key] = sanitized
        return clean
    if isinstance(value, list):
        items = [_sanitize(item, f"{field}[]", omissions) for item in value]
        if any(item is _OMIT for item in items):
            omissions.append({"code": "path_container_removed", "field": field})
            return _OMIT
        return items
    if isinstance(value, str):
        if _looks_machine_path(value):
            omissions.append({"code": "absolute_path_removed", "field": field})
            return _OMIT
        if _looks_secret_value(value):
            omissions.append({"code": "secret_pattern_removed", "field": field})
            return _OMIT
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise PortableConfigurationError(f"unsupported value at {field}")


def _assert_portable(value: Any, field: str = "package") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).strip().lower()
            if lowered in _SECRET_VALUE_KEYS or lowered in _SENSITIVE_CONTAINERS:
                raise PortableConfigurationError(f"secret-bearing field at {field}.{key}")
            if lowered in _RUNTIME_STATE_KEYS:
                raise PortableConfigurationError(f"runtime state field at {field}.{key}")
            _assert_portable(item, f"{field}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_portable(item, f"{field}[{index}]")
        return
    if isinstance(value, str):
        if _looks_machine_path(value):
            raise PortableConfigurationError(f"absolute machine path at {field}")
        if _looks_secret_value(value):
            raise PortableConfigurationError(f"secret-like value at {field}")


def _looks_machine_path(value: str) -> bool:
    text = str(value).strip()
    if not text or _URI_RE.match(text):
        return False
    if text.startswith(("~/", "~\\")):
        return True
    if _STATE_PATH_RE.search(text):
        return True
    if PureWindowsPath(text).is_absolute() or PurePosixPath(text).is_absolute():
        return True
    return bool(_WINDOWS_PATH_RE.search(text) or _POSIX_PATH_RE.search(text))


def _looks_secret_value(value: str) -> bool:
    return any(pattern.search(str(value)) for pattern in _SECRET_PATTERNS)


def _read_json_object(path: Path, *, optional: bool) -> dict[str, Any]:
    if optional and not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise PortableConfigurationError(f"configuration file not found: {path.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PortableConfigurationError(f"invalid configuration file: {path.name}") from exc
    if not isinstance(value, dict):
        raise PortableConfigurationError(f"configuration file must be an object: {path.name}")
    return value


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    "compatibility",
    "fit_reason",
    "model_role_score",
