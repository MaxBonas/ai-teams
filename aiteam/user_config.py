from __future__ import annotations

import base64
import ctypes
import json
import os
import shutil
import stat
import subprocess
from ctypes import wintypes
from pathlib import Path
from typing import Any


DEFAULT_ADAPTER_PROFILES: list[dict[str, Any]] = [
    {
        "id": "codex_subscription",
        "label": "Codex CLI subscription",
        "adapter_type": "subscription_cli",
        "channel": "subscription",
        "provider": "openai-codex",
        "config": {
            "cli_kind": "codex",
            "command": ["codex"],
            # model omitted by default → codex uses ~/.codex/config.toml's model.
            # A per-agent model override (e.g. the picker choosing gpt-5.5 for the
            # Lead) is applied via `-c model="<slug>"` in _build_codex_command,
            # which keeps the ChatGPT-subscription auth path.
            "sandbox": "workspace-write",
            "approval_policy": "never",
            "api_key_ref": "secret:openai:default",
            "api_key_env": "OPENAI_API_KEY",
        },
    },
    {
        "id": "antigravity_subscription",
        "label": "Antigravity CLI subscription",
        "adapter_type": "subscription_cli",
        "channel": "subscription",
        "provider": "google-antigravity",
        "config": {
            "cli_kind": "antigravity",
            "command": ["agy"],
            "model": "Gemini 3.1 Pro (High)",
            "sandbox": "read-only",
        },
    },
    {
        "id": "claude_subscription_blocked",
        "label": "Claude CLI subscription",
        "adapter_type": "subscription_cli",
        "channel": "subscription",
        "provider": "anthropic-claude",
        "status": "blocked_by_provider",
        "config": {"cli_kind": "claude", "command": ["claude"]},
    },
    {
        "id": "openai_api",
        "label": "OpenAI API",
        "adapter_type": "openai_api",
        "channel": "api",
        "provider": "openai",
        "config": {"model": "gpt-4.1", "api_key_ref": "secret:openai:default"},
    },
    {
        "id": "gemini_api",
        "label": "Gemini API",
        "adapter_type": "gemini_api",
        "channel": "api",
        "provider": "google",
        "config": {"model": "gemini-2.5-flash", "api_key_ref": "secret:google:default"},
    },
    {
        "id": "anthropic_api",
        "label": "Anthropic API",
        "adapter_type": "anthropic_sonnet",
        "channel": "api",
        "provider": "anthropic",
        "config": {"model": "claude-sonnet-4-5", "api_key_ref": "secret:anthropic:default"},
    },
    {
        "id": "local_qwen_ollama",
        "label": "Qwen local via Codex/Ollama",
        "adapter_type": "subscription_cli",
        "channel": "local",
        "provider": "ollama",
        "config": {
            "cli_kind": "codex",
            "command": ["codex"],
            "oss": True,
            "local_provider": "ollama",
            "model": "qwen2.5-coder:14b",
            "sandbox": "workspace-write",
            "approval_policy": "never",
        },
    },
    {
        "id": "local_gem4_lmstudio",
        "label": "Gemma/Gem4 local via Codex/LM Studio",
        "adapter_type": "subscription_cli",
        "channel": "local",
        "provider": "lmstudio",
        "config": {
            "cli_kind": "codex",
            "command": ["codex"],
            "oss": True,
            "local_provider": "lmstudio",
            "model": "gemma-3-4b-it",
            "sandbox": "workspace-write",
            "approval_policy": "never",
        },
    },
    {
        "id": "local_gemma4_ollama",
        "label": "Gemma 4 local via Codex/Ollama",
        "adapter_type": "subscription_cli",
        "channel": "local",
        "provider": "ollama",
        "config": {
            "cli_kind": "codex",
            "command": ["codex"],
            "oss": True,
            "local_provider": "ollama",
            "model": "gemma4:e4b",
            "sandbox": "workspace-write",
            "approval_policy": "never",
        },
    },
]

# ---------------------------------------------------------------------------
# Model metadata
# ---------------------------------------------------------------------------
# Each option can carry:
#   tier        premium | standard | budget   (intelligence / price band)
#   caps        list of capability keywords the model excels at
#                 coding      — writes/edits code well
#                 reasoning   — multi-step planning, complex reasoning
#                 synthesis   — reading, summarizing, reviewing text/docs
#                 long_ctx    — handles very long contexts reliably
#   best_for    roles where this model is the top recommendation
#   price_note  short human-readable cost hint shown in UI
# ---------------------------------------------------------------------------

MODEL_OPTIONS_BY_PROFILE: dict[str, list[dict[str, Any]]] = {
    # Codex subscription: model is selected by ~/.codex/config.toml, not -m.
    # These options are informational for OSS/local_provider paths and the UI picker.
    # Codex ChatGPT subscription. The model is applied via `-c model="<slug>"`
    # (see subscription_cli_adapter._build_codex_command). Slugs mirror codex's
    # own model catalog (~/.codex/models_cache.json). Flat-rate plan → no
    # per-token cost; the cost policy treats this channel as zero-cost.
    "codex_subscription": [
        {
            "value": "gpt-5.5", "label": "GPT-5.5",
            "tier": "premium", "caps": ["coding", "reasoning", "synthesis", "long_ctx"],
            "best_for": ["lead", "quorum_auditor", "quorum_senior", "reviewer"],
            "price_note": "Frontier agentic coding · Tier 1 Lead/Quorum",
        },
        {
            "value": "gpt-5.4", "label": "GPT-5.4",
            "tier": "premium", "caps": ["coding", "reasoning", "synthesis"],
            "best_for": ["engineer", "reviewer", "quorum_senior"],
            "price_note": "Alta capacidad · Tier 1/2",
        },
        {
            "value": "gpt-5.4-mini", "label": "GPT-5.4 Mini",
            "tier": "budget", "caps": ["coding", "synthesis"],
            "best_for": ["file_scout", "web_scout", "context_curator", "test_runner", "worker"],
            "price_note": "Económico · Tier 3 Scouts/Worker",
        },
    ],
    # Ordered best-to-cheapest so options[0] → senior model, last "mini"/"nano" → junior.
    # Tier 1 (lead, quorum): premium models — flagship general-purpose intelligence.
    # Tier 2 (engineer, reviewer): standard — reasoning+coding specialists.
    # Tier 3 (scouts, curator, worker): budget — cheapest capable model.
    "openai_api": [
        {
            "value": "gpt-4.1", "label": "GPT-4.1",
            "tier": "premium", "caps": ["coding", "synthesis", "reasoning"],
            "best_for": ["lead", "quorum_auditor", "quorum_senior", "reviewer"],
            "price_note": "Flagship general · Tier 1 Lead/Quorum",
        },
        {
            "value": "o3", "label": "o3",
            "tier": "premium", "caps": ["reasoning", "synthesis", "coding"],
            "best_for": ["quorum_senior", "quorum_auditor", "architect"],
            "price_note": "Reasoning especializado · alternativa Tier 1",
        },
        {
            "value": "o4-mini", "label": "o4-mini",
            "tier": "standard", "caps": ["reasoning", "coding"], "best_for": ["engineer"],
            "price_note": "Reasoning+coding · Tier 2 Engineer",
        },
        {
            "value": "gpt-4.1-mini", "label": "GPT-4.1 Mini",
            "tier": "budget", "caps": ["coding", "synthesis"],
            "best_for": ["file_scout", "web_scout", "context_curator", "qa", "worker"],
            "price_note": "Económico · Tier 3 Scouts/Worker",
        },
        {
            "value": "gpt-4.1-nano", "label": "GPT-4.1 Nano",
            "tier": "budget", "caps": ["synthesis"],
            "best_for": ["file_scout", "web_scout", "context_curator", "worker"],
            "price_note": "Ultra-económico · Tier 3 lectura/síntesis",
        },
    ],
    "gemini_api": [
        {
            "value": "gemini-2.5-pro", "label": "Gemini 2.5 Pro",
            "tier": "premium", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["lead", "quorum_auditor", "quorum_senior"],
            "price_note": "Flagship Gemini · Tier 1 Lead/Quorum",
        },
        {
            "value": "gemini-2.5-flash", "label": "Gemini 2.5 Flash",
            "tier": "standard", "caps": ["synthesis", "coding", "long_ctx"],
            "best_for": ["engineer", "reviewer"],
            "price_note": "Rápido y capaz · Tier 2 Engineer/Reviewer",
        },
        {
            "value": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite",
            "tier": "budget", "caps": ["synthesis"],
            "best_for": ["file_scout", "web_scout", "context_curator", "qa", "worker"],
            "price_note": "Ultra-económico · Tier 3 Scouts/Worker",
        },
    ],
    "antigravity_subscription": [
        {
            "value": "Gemini 3.1 Pro (High)", "label": "Gemini 3.1 Pro (High)",
            "tier": "premium", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["lead", "quorum_auditor", "quorum_senior"],
            "price_note": "Antigravity subscription · Tier 1 Lead/Quorum",
        },
        {
            "value": "Gemini 3.5 Flash (High)", "label": "Gemini 3.5 Flash (High)",
            "tier": "standard", "caps": ["synthesis", "coding", "long_ctx"],
            "best_for": ["reviewer", "context_curator"],
            "price_note": "Antigravity subscription · síntesis rápida",
        },
    ],
    "anthropic_api": [
        {
            "value": "claude-sonnet-4-5", "label": "Claude Sonnet 4.5",
            "tier": "premium", "caps": ["synthesis", "coding", "reasoning"],
            "best_for": ["lead", "quorum_auditor", "quorum_senior"],
            "price_note": "Flagship Claude · Tier 1 Lead/Quorum",
        },
        {
            "value": "claude-opus-4-5", "label": "Claude Opus 4.5",
            "tier": "premium", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["quorum_senior", "quorum_auditor"],
            "price_note": "Máxima inteligencia Anthropic · alternativa Tier 1",
        },
        {
            "value": "claude-haiku-4-5", "label": "Claude Haiku 4.5",
            "tier": "budget", "caps": ["synthesis"],
            "best_for": ["file_scout", "web_scout", "context_curator", "reviewer", "qa", "worker"],
            "price_note": "Económico · Tier 3 Scouts/Reviewer ligero",
        },
    ],
    "local_qwen_ollama": [
        {
            "value": "qwen2.5-coder:14b", "label": "Qwen 2.5 Coder 14B",
            "tier": "budget", "caps": ["coding"],
            "best_for": ["engineer", "file_scout", "context_curator"],
            "price_note": "Local gratuito · coding · Tier 3",
        },
        {
            "value": "qwen2.5-coder:32b", "label": "Qwen 2.5 Coder 32B",
            "tier": "standard", "caps": ["coding", "reasoning"], "best_for": ["engineer"],
            "price_note": "Local gratuito · Tier 2 Engineer",
        },
        {
            "value": "qwen3-coder:30b", "label": "Qwen3 Coder 30B",
            "tier": "standard", "caps": ["coding", "reasoning"], "best_for": ["engineer"],
            "price_note": "Local gratuito · Tier 2 Engineer · última generación",
        },
    ],
    "local_gem4_lmstudio": [
        {
            "value": "gemma-3-4b-it", "label": "Gemma/Gem4 4B Instruct",
            "tier": "budget", "caps": ["synthesis"],
            "best_for": ["file_scout", "web_scout", "context_curator", "qa", "worker"],
            "price_note": "Local gratuito · Tier 3 Scouts/Worker",
        },
        {
            "value": "gemma-3-12b-it", "label": "Gemma/Gem4 12B Instruct",
            "tier": "standard", "caps": ["synthesis", "coding"],
            "best_for": ["engineer", "reviewer"],
            "price_note": "Local gratuito · Tier 2 Engineer/Reviewer",
        },
        {
            "value": "gemma-3-27b-it", "label": "Gemma/Gem4 27B Instruct",
            "tier": "premium", "caps": ["synthesis", "coding", "reasoning"],
            "best_for": ["lead", "engineer"],
            "price_note": "Local gratuito · Tier 1/2 · más RAM",
        },
    ],
    "local_gemma4_ollama": [
        {
            "value": "gemma4:e4b", "label": "Gemma 4 E4B",
            "tier": "budget", "caps": ["coding", "synthesis"],
            "best_for": ["engineer", "reviewer", "researcher", "qa"],
            "price_note": "Local gratuito · cabe 100% en VRAM · ultrarrápido",
        },
    ],
}


def user_config_dir() -> Path:
    override = os.environ.get("AITEAM_USER_CONFIG_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "AI Teams"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aiteams"


def get_app_settings() -> dict[str, Any]:
    """Load application-level settings (projects_root, theme, etc.)."""
    return _read_json(user_config_dir() / "settings.json")


def update_app_settings(updates: dict[str, Any]) -> None:
    """Merge *updates* into the persisted application settings."""
    settings_path = user_config_dir() / "settings.json"
    data = _read_json(settings_path)
    data.update(updates)
    _write_json(settings_path, data)


def get_projects_root() -> Path | None:
    """Return the user-configured projects root, or None if not set."""
    raw = str(get_app_settings().get("projects_root") or "").strip()
    return Path(raw).resolve() if raw else None


def load_adapter_profiles() -> list[dict[str, Any]]:
    path = user_config_dir() / "adapter_profiles.json"
    custom = _read_json(path).get("profiles", [])
    profiles = {p["id"]: p for p in DEFAULT_ADAPTER_PROFILES}
    if isinstance(custom, list):
        for item in custom:
            if isinstance(item, dict) and str(item.get("id") or "").strip():
                profiles[str(item["id"])] = item
    health = _read_json(user_config_dir() / "adapter_health.json").get("profiles", {})
    out = []
    for profile in profiles.values():
        redacted = _redact_profile(profile)
        profile_id = str(redacted.get("id") or "")
        redacted["model_options"] = MODEL_OPTIONS_BY_PROFILE.get(profile_id, [])
        redacted["health"] = health.get(profile_id, {"status": "untested"})
        out.append(redacted)
    return out


def profile_is_connected(profile: dict[str, Any]) -> bool:
    """Best-effort connectivity check for an adapter profile.

    Mirrors the frontend's ``profileState``: a profile counts as connected when
    its health check passed (or the CLI is at least installed), or when it is
    an API-channel profile whose secret is present in the local store.
    """
    health = profile.get("health") if isinstance(profile.get("health"), dict) else {}
    if str(health.get("status") or "") in {"ok", "installed"}:
        return True
    if str(profile.get("channel") or "") == "api":
        config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
        ref = str(config.get("api_key_ref") or "").strip() or _default_secret_ref(str(profile.get("adapter_type") or ""))
        return bool(ref and read_secret(ref))
    return False


def upsert_adapter_profile(profile: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(profile.get("id") or "").strip()
    if not profile_id:
        raise ValueError("profile id is required")
    assert_no_inline_secret(profile)
    path = user_config_dir() / "adapter_profiles.json"
    data = _read_json(path)
    existing = data.get("profiles", [])
    if not isinstance(existing, list):
        existing = []
    profiles = [p for p in existing if not (isinstance(p, dict) and p.get("id") == profile_id)]
    profiles.append(profile)
    data["profiles"] = profiles
    _write_json(path, data)
    return _redact_profile(profile)


def resolve_adapter_config(adapter_type: str, adapter_config: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(adapter_config.get("profile_id") or "").strip()
    merged: dict[str, Any] = {}
    if profile_id:
        profile = _profile_by_id(profile_id)
        if profile:
            if str(profile.get("adapter_type") or "") and str(profile.get("adapter_type")) != adapter_type:
                merged["adapter_type_mismatch"] = profile.get("adapter_type")
            profile_config = profile.get("config")
            if isinstance(profile_config, dict):
                merged.update(profile_config)
    merged.update(adapter_config)
    return merged


def inject_adapter_secrets(env: dict[str, str], adapter_type: str, adapter_config: dict[str, Any]) -> dict[str, str]:
    ref = str(adapter_config.get("api_key_ref") or "").strip()
    if not ref:
        ref = _default_secret_ref(adapter_type)
    secret = read_secret(ref) if ref else None
    if not secret:
        return env
    target = str(adapter_config.get("api_key_env") or _default_secret_env(adapter_type) or "").strip()
    return {**env, target: secret} if target else env


def store_secret(*, provider: str, name: str = "default", secret: str) -> str:
    provider_key = _safe_key(provider)
    name_key = _safe_key(name or "default")
    if not provider_key:
        raise ValueError("provider is required")
    if not secret:
        raise ValueError("secret is required")
    path = user_config_dir() / "secrets.json"
    data = _read_json(path)
    secrets = data.get("secrets")
    if not isinstance(secrets, dict):
        secrets = {}
    key = f"{provider_key}:{name_key}"
    secrets[key] = {
        "provider": provider_key,
        "name": name_key,
        "value": _protect(secret.encode("utf-8")),
    }
    data = {"version": 1, "secrets": secrets}
    _write_json(path, data)
    return f"secret:{provider_key}:{name_key}"


def read_secret(ref: str) -> str | None:
    parts = str(ref or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "secret":
        return None
    data = _read_json(user_config_dir() / "secrets.json")
    row = (data.get("secrets") or {}).get(f"{_safe_key(parts[1])}:{_safe_key(parts[2])}")
    if not isinstance(row, dict) or not isinstance(row.get("value"), str):
        return None
    try:
        return _unprotect(row["value"]).decode("utf-8")
    except Exception:
        return None


def list_secrets() -> list[dict[str, Any]]:
    data = _read_json(user_config_dir() / "secrets.json")
    rows = data.get("secrets") or {}
    if not isinstance(rows, dict):
        return []
    out = []
    for key, row in rows.items():
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or key.split(":", 1)[0])
        name = str(row.get("name") or "default")
        out.append({"ref": f"secret:{provider}:{name}", "provider": provider, "name": name, "has_secret": True})
    return sorted(out, key=lambda item: (item["provider"], item["name"]))


def adapter_health() -> dict[str, Any]:
    return _read_json(user_config_dir() / "adapter_health.json")


def save_adapter_health(profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = user_config_dir() / "adapter_health.json"
    data = _read_json(path)
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
    profiles[profile_id] = payload
    data["profiles"] = profiles
    _write_json(path, data)
    return payload


def model_options() -> dict[str, list[dict[str, Any]]]:
    return MODEL_OPTIONS_BY_PROFILE


# ---------------------------------------------------------------------------
# Role capability profiles — what each role needs from its model
# ---------------------------------------------------------------------------

# Each entry defines what the role needs:
#   capabilities_needed: which model caps the role relies on
#   requires_workspace:  must the model write/edit files?
#   prefers_cheaper:     true for roles where cost-efficiency matters more than max intelligence
#   note:                shown in UI to explain the tradeoff
ROLE_CAPABILITY_PROFILES: dict[str, dict[str, Any]] = {
    # ── TIER 1 — Command & Oversight ────────────────────────────────────────
    "lead": {
        "preferred_tier": "premium",
        "capabilities_needed": ["reasoning", "synthesis"],
        "requires_workspace": False,
        "prefers_cheaper": False,
        "note": "Orquesta, planifica y toma decisiones. Usa el modelo flagship premium de cada proveedor.",
    },
    "team_lead": {
        "preferred_tier": "premium",
        "capabilities_needed": ["reasoning", "synthesis"],
        "requires_workspace": False,
        "prefers_cheaper": False,
        "note": "Alias de lead. Mismo tier.",
    },
    "architect": {
        "preferred_tier": "premium",
        "capabilities_needed": ["reasoning", "synthesis", "coding"],
        "requires_workspace": False,
        "prefers_cheaper": False,
        "note": "Diseña sistemas complejos. Tier 1.",
    },
    "quorum_auditor": {
        "preferred_tier": "premium",
        "capabilities_needed": ["reasoning", "synthesis"],
        "requires_workspace": False,
        "prefers_cheaper": False,
        "note": "Revisión independiente del plan. Tier 1 — debe ser proveedor distinto al Lead.",
    },
    "quorum_senior": {
        "preferred_tier": "premium",
        "capabilities_needed": ["reasoning", "synthesis"],
        "requires_workspace": False,
        "prefers_cheaper": False,
        "note": "Revisión senior del plan. Tier 1 — debe ser proveedor distinto al Lead.",
    },
    # ── TIER 2 — Execution Specialists ──────────────────────────────────────
    "engineer": {
        "preferred_tier": "standard",
        "capabilities_needed": ["coding"],
        "requires_workspace": True,
        "prefers_cheaper": True,
        "note": "Implementa código. Requiere CLI (codex/gemini). Tier 2 — modelo con reasoning+coding.",
    },
    "reviewer": {
        "preferred_tier": "standard",
        "capabilities_needed": ["synthesis", "reasoning"],
        "requires_workspace": False,
        "prefers_cheaper": True,
        "note": "Revisa código y hace análisis estático (también cubre QA). Tier 2. API adapter OK.",
    },
    "mcp_operator": {
        "preferred_tier": "standard",
        "capabilities_needed": ["reasoning", "synthesis"],
        "requires_workspace": False,
        "prefers_cheaper": True,
        "note": "Controla MCPs avanzados. Tier 2.",
    },
    # ── TIER 3 — Cheap Specialists ───────────────────────────────────────────
    "file_scout": {
        "preferred_tier": "budget",
        "capabilities_needed": ["synthesis"],
        "requires_workspace": True,
        "prefers_cheaper": True,
        "note": "Lee archivos y devuelve resúmenes al Lead. Tier 3 — modelo barato o local.",
    },
    "web_scout": {
        "preferred_tier": "budget",
        "capabilities_needed": ["synthesis"],
        "requires_workspace": False,
        "prefers_cheaper": True,
        "note": "Busca y resume información web para el Lead. Tier 3 — modelo barato.",
    },
    "context_curator": {
        "preferred_tier": "budget",
        "capabilities_needed": ["synthesis"],
        "requires_workspace": False,
        "prefers_cheaper": True,
        "note": "Comprime threads largos en documentos de plan. Tier 3 — modelo barato.",
    },
    "qa": {
        "preferred_tier": "budget",
        "capabilities_needed": ["synthesis", "coding"],
        "requires_workspace": True,
        "prefers_cheaper": True,
        "note": "Verificación opcional. Tier 3. No es rol por defecto — el Reviewer cubre el análisis estático.",
    },
    "worker": {
        "preferred_tier": "budget",
        "capabilities_needed": ["synthesis"],
        "requires_workspace": True,
        "prefers_cheaper": True,
        "note": "Tareas delegadas genéricas. Tier 3.",
    },
}


def model_options_for_role(profile_id: str, role: str) -> list[dict[str, Any]]:
    """Return model options for a profile sorted and annotated for a specific role.

    Each option gains:
      recommended  bool   — true if this model is in the option's ``best_for`` list for this role
      role_score   int    — sorting key (higher = better fit for the role)
      fit_reason   str    — short human-readable explanation of why this model fits

    Scoring is primarily driven by ``preferred_tier`` from ROLE_CAPABILITY_PROFILES:
      Tier 1 (lead, quorum)   → premium models score highest (+40)
      Tier 2 (engineer, reviewer) → standard models score highest (+40)
      Tier 3 (scouts, worker) → budget models score highest (+40)

    Capability match and ``best_for`` recommendation provide secondary differentiation.
    """
    options = MODEL_OPTIONS_BY_PROFILE.get(profile_id, [])
    role_key = str(role or "").strip().lower()
    profile = ROLE_CAPABILITY_PROFILES.get(role_key, {})
    needs_coding = "coding" in profile.get("capabilities_needed", [])
    needs_reasoning = "reasoning" in profile.get("capabilities_needed", [])
    needs_synthesis = "synthesis" in profile.get("capabilities_needed", [])
    preferred_tier = str(profile.get("preferred_tier") or "standard")

    # Tier-match bonus: highest when model tier matches role's preferred tier.
    # One step away (e.g. standard role gets premium model): smaller bonus.
    # Two steps away (e.g. budget role gets premium): no bonus.
    _tier_match: dict[tuple[str, str], int] = {
        ("premium", "premium"): 40,
        ("standard", "standard"): 40,
        ("budget", "budget"): 40,
        ("premium", "standard"): 15,  # slightly over-provisioned
        ("standard", "premium"): 12,  # more than needed but still useful
        ("standard", "budget"): 10,
        ("budget", "standard"): 8,
        ("premium", "budget"): 0,
        ("budget", "premium"): 0,
    }

    result = []
    for opt in options:
        caps = set(opt.get("caps") or [])
        tier = str(opt.get("tier") or "standard")
        is_recommended = role_key in (opt.get("best_for") or [])

        # Capability match score
        cap_score = 0
        if needs_coding and "coding" in caps:
            cap_score += 25
        if needs_reasoning and "reasoning" in caps:
            cap_score += 25
        if needs_synthesis and "synthesis" in caps:
            cap_score += 15
        if not needs_coding and "long_ctx" in caps:
            cap_score += 5  # long-context bonus for read-heavy roles

        # Tier alignment (primary differentiator)
        tier_adj = _tier_match.get((preferred_tier, tier), 0)

        # Explicit recommendation bonus
        role_score = cap_score + tier_adj + (20 if is_recommended else 0)

        # Build a short fit reason for the UI
        fit_parts = []
        matched = [c for c in ["coding", "reasoning", "synthesis"] if c in caps and locals()[f"needs_{c}"]]
        if matched:
            fit_parts.append(" + ".join(matched))
        if tier == preferred_tier:
            tier_labels = {"premium": "Tier 1", "standard": "Tier 2", "budget": "Tier 3"}
            fit_parts.append(tier_labels.get(tier, tier))
        fit_reason = " · ".join(fit_parts) if fit_parts else opt.get("price_note") or ""

        result.append({
            **opt,
            "recommended": is_recommended,
            "role_score": role_score,
            "fit_reason": fit_reason,
        })

    return sorted(result, key=lambda x: (-x["role_score"], x.get("label", "")))


def cli_status() -> list[dict[str, Any]]:
    codex = _resolve_cli_executable("codex")
    antigravity = _resolve_cli_executable("agy")
    claude = _resolve_cli_executable("claude")
    ollama = _resolve_cli_executable("ollama")
    return [
        {
            "id": "codex",
            "label": "Codex CLI",
            "command": "codex",
            "resolved_command": codex,
            "available": codex is not None,
            "login_supported": True,
            "login_command": _login_display_command(["codex", "login"]),
            "alternate_login_commands": [_login_display_command(["codex", "auth"])],
            "login_hint": "Abre `codex login` en una ventana local. Si lo lanzas en PowerShell con ruta absoluta, usa el prefijo &.",
        },
        {
            "id": "antigravity",
            "label": "Antigravity CLI",
            "command": "agy",
            "resolved_command": antigravity,
            "available": antigravity is not None,
            "login_supported": False,
            "login_hint": "La autenticacion se gestiona mediante Antigravity y se verifica con una llamada headless.",
        },
        {
            "id": "claude",
            "label": "Claude CLI",
            "command": "claude",
            "resolved_command": claude,
            "available": claude is not None,
            "login_supported": True,
            "login_command": _login_display_command(["claude", "auth"]),
            "alternate_login_commands": [_login_display_command(["claude", "login"])],
            "login_hint": "Anthropic puede bloquear el canal de suscripcion; se mantiene para diagnostico.",
        },
        {
            "id": "ollama",
            "label": "Ollama",
            "command": "ollama",
            "resolved_command": ollama,
            "available": ollama is not None,
            "login_supported": False,
            "login_hint": "Ollama no requiere login; necesita servicio/modelo local.",
        },
    ]


def launch_subscription_login(cli_id: str) -> dict[str, Any]:
    cli_key = _safe_key(cli_id)
    commands: dict[str, list[str]] = {
        "codex": ["codex", "login"],
        "claude": ["claude", "auth"],
    }
    command = commands.get(cli_key)
    if command is None:
        raise ValueError(f"unsupported login cli: {cli_id}")
    executable = _resolve_cli_executable(command[0])
    if executable is None:
        raise FileNotFoundError(command[0])
    full_command = [executable, *command[1:]]
    if os.name == "nt":
        script_path = _write_windows_login_launcher(cli_key, full_command)
        subprocess.Popen(
            ["cmd.exe", "/k", str(script_path)],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        launcher = "cmd-script"
    else:
        subprocess.Popen(full_command)
        launcher = "direct"
    display_command = _login_display_command(full_command)
    return {
        "cli_id": cli_key,
        "command": command,
        "resolved_command": full_command,
        "display_command": display_command,
        "manual_command": display_command,
        "launcher": launcher,
        "launcher_path": str(script_path) if os.name == "nt" else None,
        "launched": True,
    }


def _resolve_cli_executable(command: str) -> str | None:
    key = _safe_key(command).replace("-", "_").upper()
    override = os.environ.get(f"AITEAM_{key}_CLI", "").strip()
    if override:
        return override if Path(override).exists() else shutil.which(override)
    for candidate in _known_cli_candidates(command):
        if candidate.exists():
            return str(candidate)
    for candidate in _where_candidates(command):
        if _is_usable_cli_path(candidate):
            return candidate
    candidates = [command]
    if os.name == "nt" and not command.lower().endswith((".exe", ".cmd", ".bat")):
        candidates.extend([f"{command}.cmd", f"{command}.exe"])
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved and _is_usable_cli_path(resolved):
            return resolved
    return None


def _known_cli_candidates(command: str) -> list[Path]:
    if os.name != "nt":
        return []
    local_appdata = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    key = _safe_key(command)
    if key == "codex":
        return [local_appdata / "OpenAI" / "Codex" / "bin" / "codex.exe"]
    if key == "agy":
        return [local_appdata / "agy" / "bin" / "agy.exe"]
    return []


def _where_candidates(command: str) -> list[str]:
    if os.name != "nt":
        return []
    try:
        proc = subprocess.run(["where.exe", command], capture_output=True, text=True, timeout=5)
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _is_usable_cli_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if os.name == "nt" and "\\windowsapps\\" in text.lower():
        # Store app execution targets often exist in PATH but deny direct process execution.
        # Prefer the vendor shim in AppData or an explicit AITEAM_*_CLI override.
        return False
    return True


def _write_windows_login_launcher(cli_key: str, command: list[str]) -> Path:
    launcher_dir = user_config_dir() / "login_launchers"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    script_path = launcher_dir / f"{_safe_key(cli_key)}_login.cmd"
    command_line = _cmd_command(command)
    body = "\r\n".join(
        [
            "@echo off",
            f"title AI Teams - {cli_key} login",
            "echo AI Teams subscription login",
            f"echo Running: {command_line}",
            "echo.",
            f"call {command_line}",
            "set EXITCODE=%ERRORLEVEL%",
            "echo.",
            "echo Login command finished with exit code %EXITCODE%.",
            "echo You can close this window after the provider login completes.",
            "pause",
            "exit /b %EXITCODE%",
            "",
        ]
    )
    script_path.write_text(body, encoding="utf-8")
    return script_path


def _login_display_command(command: list[str]) -> str:
    if not command:
        return ""
    executable = _resolve_cli_executable(command[0]) or command[0]
    full_command = [executable, *command[1:]]
    if os.name == "nt":
        return _powershell_command(full_command)
    return " ".join(_quote_sh_arg(part) for part in full_command)


def _powershell_command(command: list[str]) -> str:
    if not command:
        raise ValueError("command is required")
    executable = _quote_powershell_arg(command[0])
    args = " ".join(_quote_powershell_arg(part) for part in command[1:])
    return f"& {executable}{(' ' + args) if args else ''}"


def _cmd_command(command: list[str]) -> str:
    if not command:
        raise ValueError("command is required")
    return " ".join(_quote_cmd_arg(part) for part in command)


def _quote_cmd_arg(value: str) -> str:
    escaped = str(value).replace('"', '""')
    return f'"{escaped}"'


def _quote_powershell_arg(value: str) -> str:
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _quote_sh_arg(value: str) -> str:
    text = str(value)
    if not text or any(ch.isspace() or ch in "'\"\\$`!" for ch in text):
        return "'" + text.replace("'", "'\"'\"'") + "'"
    return text


def _profile_by_id(profile_id: str) -> dict[str, Any] | None:
    for profile in DEFAULT_ADAPTER_PROFILES:
        if profile.get("id") == profile_id:
            return profile
    path = user_config_dir() / "adapter_profiles.json"
    for item in _read_json(path).get("profiles", []) or []:
        if isinstance(item, dict) and item.get("id") == profile_id:
            return item
    return None


def _default_secret_ref(adapter_type: str) -> str:
    if adapter_type == "openai_api":
        return "secret:openai:default"
    if adapter_type == "gemini_api":
        return "secret:google:default"
    if adapter_type in {"anthropic_api", "anthropic_sonnet"}:
        return "secret:anthropic:default"
    return ""


def _default_secret_env(adapter_type: str) -> str:
    if adapter_type == "openai_api":
        return "OPENAI_API_KEY"
    if adapter_type == "gemini_api":
        return "GEMINI_API_KEY"
    if adapter_type in {"anthropic_api", "anthropic_sonnet"}:
        return "ANTHROPIC_API_KEY"
    return ""


def _redact_profile(profile: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(profile, ensure_ascii=False))
    config = out.get("config")
    if isinstance(config, dict):
        for key in list(config):
            if "key" in key.lower() and key != "api_key_ref":
                config[key] = "***"
    return out


def assert_no_inline_secret(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"api_key", "apikey", "secret", "token", "password"} and item:
                raise ValueError("do not store inline secrets; use /api/user-adapters/secrets")
            assert_no_inline_secret(item)
    elif isinstance(value, list):
        for item in value:
            assert_no_inline_secret(item)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def _safe_key(value: str) -> str:
    return "".join(ch for ch in str(value).strip().lower() if ch.isalnum() or ch in {"_", "-", "."})


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _protect(raw: bytes) -> str:
    if os.name != "nt":
        return "plain:" + base64.b64encode(raw).decode("ascii")
    blob_in, _buf_in = _blob_from_bytes(raw)
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptProtectData failed")
    try:
        protected = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        return "dpapi:" + base64.b64encode(protected).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _unprotect(value: str) -> bytes:
    if value.startswith("plain:"):
        return base64.b64decode(value[6:])
    if not value.startswith("dpapi:"):
        raise ValueError("unsupported secret encoding")
    blob_in, _buf_in = _blob_from_bytes(base64.b64decode(value[6:]))
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _blob_from_bytes(raw: bytes) -> tuple[_DATA_BLOB, ctypes.Array[ctypes.c_char]]:
    buf = ctypes.create_string_buffer(raw, len(raw))
    return _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf
