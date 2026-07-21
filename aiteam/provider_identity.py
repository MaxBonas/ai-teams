"""Identidad normalizada de proveedor, transporte, capacidad y perspectiva.

Un perfil no equivale necesariamente a un fabricante de modelo. Codex y la API
de OpenAI son transportes/capacidades distintos, pero comparten perspectiva de
modelo; Antigravity puede transportar modelos de varios fabricantes. Las
decisiones de quorum/review usan ``perspective_key`` y las de cuota usan
``capacity_pool_key``.

Módulo leaf: no carga configuración ni adapters y funciona con registros
históricos que solo tengan provider/model/channel.
"""

from __future__ import annotations

from typing import Any


_PROVIDER_ORG_ALIASES = {
    "openai": "openai",
    "openai-codex": "openai",
    "codex": "openai",
    "anthropic": "anthropic",
    "anthropic-claude": "anthropic",
    "claude": "anthropic",
    "google": "google",
    "google-antigravity": "google",
    "gemini": "google",
    "opencode-zen": "opencode",
    "opencode": "opencode",
    "groq": "groq",
}


def provider_org(provider: str) -> str:
    """Organización que presta el canal, separada del vendor del modelo."""
    key = str(provider or "").strip().lower().replace("_", "-")
    return _PROVIDER_ORG_ALIASES.get(key, key or "unknown")


def model_vendor(model: str, *, fallback_provider: str = "") -> str:
    """Fabricante/familia de perspectiva inferible a partir del modelo exacto."""
    key = str(model or "").strip().lower()
    if not key:
        return provider_org(fallback_provider)
    if key.startswith(("openai/", "gpt-", "o1", "o3", "o4")) or "gpt-oss" in key:
        return "openai"
    if key.startswith("anthropic/") or "claude" in key:
        return "anthropic"
    if key.startswith("google/") or any(token in key for token in ("gemini", "gemma")):
        return "google"
    if key.startswith("deepseek/") or "deepseek" in key:
        return "deepseek"
    if key.startswith("nvidia/") or any(token in key for token in ("nemotron", "nvidia")):
        return "nvidia"
    if key.startswith("xiaomi/") or "mimo" in key:
        return "xiaomi"
    if key.startswith("cohere/") or any(token in key for token in ("command-r", "north")):
        return "cohere"
    if key.startswith(("qwen/", "alibaba/")) or "qwen" in key:
        return "alibaba"
    if key.startswith("meta/") or "llama" in key:
        return "meta"
    if key.startswith("mistral/") or "mistral" in key:
        return "mistral"
    return provider_org(fallback_provider)


def perspective_key(provider: str, model: str = "") -> str:
    """Clave para independencia cognitiva en quorum y review."""
    return model_vendor(model, fallback_provider=provider)


def capacity_pool_key(
    *, profile_id: str = "", provider: str = "", config: dict[str, Any] | None = None
) -> str:
    """Clave de cuota/rate-limit; por defecto cada perfil/key es independiente."""
    cfg = config if isinstance(config, dict) else {}
    explicit = str(cfg.get("capacity_pool") or "").strip().lower()
    if explicit:
        return explicit
    profile = str(profile_id or cfg.get("profile_id") or "").strip().lower()
    return profile or provider_org(provider)


def profile_model(profile: dict[str, Any], *, selected_model: str = "") -> str:
    if selected_model:
        return str(selected_model).strip()
    config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
    return str(config.get("model") or profile.get("model") or "").strip()


def profile_perspective_key(profile: dict[str, Any], *, selected_model: str = "") -> str:
    return perspective_key(
        str(profile.get("provider") or ""),
        profile_model(profile, selected_model=selected_model),
    )


def profile_identity(profile: dict[str, Any], *, selected_model: str = "") -> dict[str, str]:
    """Proyección estable para API, auditoría y decisiones de routing."""
    config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
    raw_provider = str(profile.get("provider") or "")
    model = profile_model(profile, selected_model=selected_model)
    return {
        "provider_org": provider_org(raw_provider),
        "model_vendor": model_vendor(model, fallback_provider=raw_provider),
        "perspective_key": perspective_key(raw_provider, model),
        "transport": str(profile.get("channel") or profile.get("adapter_type") or "unknown"),
        "capacity_pool": capacity_pool_key(
            profile_id=str(profile.get("id") or ""), provider=raw_provider, config=config
        ),
    }
