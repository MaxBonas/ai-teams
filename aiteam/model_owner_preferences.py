"""Preferencias locales y reversibles del owner sobre modelos.

La proyección y los gates viven en los consumidores; este módulo mantiene una
única validación fail-closed para persistencia y documentos ya cargados.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.user_config import user_config_dir

MODEL_OWNER_PREFERENCES_VERSION = "model_owner_preferences_v1"
MODEL_OWNER_PREFERENCE_STATES = frozenset({"high", "normal", "low", "archived"})
MODEL_OWNER_PREFERENCES_FILENAME = "model_owner_preferences.json"

_DOCUMENT_KEYS = frozenset({"schema_version", "updated_at", "preferences"})
_ENTRY_KEYS = frozenset(
    {"profile_id", "model_id", "state", "reason", "updated_at"}
)
_PREFERENCES_LOCK = threading.Lock()


class ModelOwnerPreferencesError(ValueError):
    """El documento local no cumple el contrato versionado."""


def _preferences_path() -> Path:
    return user_config_dir() / MODEL_OWNER_PREFERENCES_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_timestamp(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ModelOwnerPreferencesError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ModelOwnerPreferencesError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ModelOwnerPreferencesError(f"{field} must include timezone")
    return text


def _validate_identity(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ModelOwnerPreferencesError(f"{field} is required")
    if len(text) > 255:
        raise ModelOwnerPreferencesError(f"{field} exceeds 255 characters")
    if any(character.isspace() or ord(character) < 32 for character in text):
        raise ModelOwnerPreferencesError(f"{field} cannot contain whitespace")
    return text


def _validate_reason(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise ModelOwnerPreferencesError("reason is required")
    if len(text) > 1000:
        raise ModelOwnerPreferencesError("reason exceeds 1000 characters")
    return text


def _validate_entry(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ModelOwnerPreferencesError("preference entry must be an object")
    unknown = set(value) - _ENTRY_KEYS
    missing = _ENTRY_KEYS - set(value)
    if unknown or missing:
        raise ModelOwnerPreferencesError(
            "preference entry fields do not match the v1 contract"
        )
    state = str(value.get("state") or "").strip().lower()
    if state not in MODEL_OWNER_PREFERENCE_STATES:
        raise ModelOwnerPreferencesError(
            "state must be high, normal, low or archived"
        )
    return {
        "profile_id": _validate_identity(value.get("profile_id"), field="profile_id"),
        "model_id": _validate_identity(value.get("model_id"), field="model_id"),
        "state": state,
        "reason": _validate_reason(value.get("reason")),
        "updated_at": _validate_timestamp(value.get("updated_at"), field="updated_at"),
    }


def _validate_document(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelOwnerPreferencesError("preferences document must be an object")
    if set(value) != _DOCUMENT_KEYS:
        raise ModelOwnerPreferencesError(
            "preferences document fields do not match the v1 contract"
        )
    if value.get("schema_version") != MODEL_OWNER_PREFERENCES_VERSION:
        raise ModelOwnerPreferencesError("unsupported preferences schema_version")
    updated_at = value.get("updated_at")
    if updated_at is not None:
        updated_at = _validate_timestamp(updated_at, field="updated_at")
    raw_preferences = value.get("preferences")
    if not isinstance(raw_preferences, list):
        raise ModelOwnerPreferencesError("preferences must be an array")
    preferences: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    for raw_entry in raw_preferences:
        entry = _validate_entry(raw_entry)
        identity = (entry["profile_id"], entry["model_id"])
        if identity in identities:
            raise ModelOwnerPreferencesError(
                "duplicate profile_id/model_id preference"
            )
        identities.add(identity)
        preferences.append(entry)
    preferences.sort(key=lambda item: (item["profile_id"], item["model_id"]))
    return {
        "schema_version": MODEL_OWNER_PREFERENCES_VERSION,
        "updated_at": updated_at,
        "preferences": preferences,
    }


def _empty_document() -> dict[str, Any]:
    return {
        "schema_version": MODEL_OWNER_PREFERENCES_VERSION,
        "updated_at": None,
        "preferences": [],
    }


def normalize_model_owner_preferences(value: object | None) -> dict[str, Any]:
    """Valida una instantánea o devuelve el documento vacío canónico."""
    return _validate_document(_empty_document() if value is None else value)


def model_owner_preference_from_document(
    document: object,
    profile_id: str,
    model_id: str,
) -> dict[str, Any]:
    """Resuelve una identidad exacta sin volver a leer disco."""
    clean_profile = _validate_identity(profile_id, field="profile_id")
    clean_model = _validate_identity(model_id, field="model_id")
    normalized = normalize_model_owner_preferences(document)
    for entry in normalized["preferences"]:
        if (
            entry["profile_id"] == clean_profile
            and entry["model_id"] == clean_model
        ):
            return {**entry, "source": "user_machine"}
    return {
        "profile_id": clean_profile,
        "model_id": clean_model,
        "state": "normal",
        "reason": "default_normal",
        "updated_at": None,
        "source": "default",
    }


def load_model_owner_preferences() -> dict[str, Any]:
    """Carga y valida el documento local; nunca repara corrupción en silencio."""
    path = _preferences_path()
    if not path.exists():
        return _empty_document()
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelOwnerPreferencesError(
            "cannot read model owner preferences"
        ) from exc
    return _validate_document(parsed)


def get_model_owner_preference(profile_id: str, model_id: str) -> dict[str, Any]:
    """Devuelve una preferencia exacta o el default local ``normal``."""
    return model_owner_preference_from_document(
        load_model_owner_preferences(),
        profile_id,
        model_id,
    )


def _write_document_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    payload = json.dumps(
        document,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(temporary, path)
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def set_model_owner_preference(
    profile_id: str,
    model_id: str,
    *,
    state: str,
    reason: str,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Crea o reemplaza una preferencia exacta de forma atómica."""
    timestamp = _validate_timestamp(updated_at or _now(), field="updated_at")
    entry = _validate_entry(
        {
            "profile_id": profile_id,
            "model_id": model_id,
            "state": state,
            "reason": reason,
            "updated_at": timestamp,
        }
    )
    identity = (entry["profile_id"], entry["model_id"])
    with _PREFERENCES_LOCK:
        document = load_model_owner_preferences()
        remaining = [
            current
            for current in document["preferences"]
            if (current["profile_id"], current["model_id"]) != identity
        ]
        remaining.append(entry)
        remaining.sort(key=lambda item: (item["profile_id"], item["model_id"]))
        updated = {
            "schema_version": MODEL_OWNER_PREFERENCES_VERSION,
            "updated_at": timestamp,
            "preferences": remaining,
        }
        validated = _validate_document(updated)
        _write_document_atomic(_preferences_path(), validated)
    return {**entry, "source": "user_machine"}


def replace_model_owner_preferences(
    preferences: list[dict[str, str]],
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Reemplaza toda la política local mediante una única escritura atómica."""
    timestamp = _validate_timestamp(updated_at or _now(), field="updated_at")
    entries = [
        _validate_entry(
            {
                "profile_id": item.get("profile_id"),
                "model_id": item.get("model_id"),
                "state": item.get("state"),
                "reason": item.get("reason"),
                "updated_at": timestamp,
            }
        )
        for item in preferences
    ]
    document = _validate_document(
        {
            "schema_version": MODEL_OWNER_PREFERENCES_VERSION,
            "updated_at": timestamp,
            "preferences": entries,
        }
    )
    with _PREFERENCES_LOCK:
        # Un archivo previo corrupto exige reparación explícita; una operación
        # normal nunca debe borrarlo silenciosamente.
        load_model_owner_preferences()
        _write_document_atomic(_preferences_path(), document)
    return document


def append_model_owner_preferences(
    preferences: list[dict[str, str]],
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Añade identidades ausentes en una sola escritura, sin tocar las previas."""
    timestamp = _validate_timestamp(updated_at or _now(), field="updated_at")
    additions = [
        _validate_entry(
            {
                "profile_id": item.get("profile_id"),
                "model_id": item.get("model_id"),
                "state": item.get("state"),
                "reason": item.get("reason"),
                "updated_at": timestamp,
            }
        )
        for item in preferences
    ]
    addition_identities = [
        (item["profile_id"], item["model_id"]) for item in additions
    ]
    if len(addition_identities) != len(set(addition_identities)):
        raise ModelOwnerPreferencesError(
            "duplicate profile_id/model_id preference"
        )
    with _PREFERENCES_LOCK:
        document = load_model_owner_preferences()
        existing_identities = {
            (item["profile_id"], item["model_id"])
            for item in document["preferences"]
        }
        if existing_identities.intersection(addition_identities):
            raise ModelOwnerPreferencesError(
                "preference identity already exists"
            )
        updated = _validate_document(
            {
                "schema_version": MODEL_OWNER_PREFERENCES_VERSION,
                "updated_at": timestamp,
                "preferences": [*document["preferences"], *additions],
            }
        )
        _write_document_atomic(_preferences_path(), updated)
    return updated
