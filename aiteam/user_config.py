from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import shutil
import stat
import subprocess
import tomllib
from ctypes import wintypes
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from aiteam.policies import canonical_role
from aiteam.model_compatibility import compatibility_decision


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
            # A per-agent model override (e.g. the picker choosing gpt-5.6-sol for the
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
            "model": "gemini-3.1-pro-high",
            "sandbox": "read-only",
        },
    },
    {
        "id": "opencode_zen_free",
        "label": "OpenCode Zen · modelos gratuitos",
        "adapter_type": "subscription_cli",
        "channel": "free_gateway",
        "provider": "opencode-zen",
        "supported_roles": [
            "lead", "team_lead", "architect", "quorum_auditor",
            "reviewer", "code_reviewer", "qa",
            "file_scout", "web_scout", "context_curator",
        ],
        "data_policy": "non_confidential_only",
        "privacy_note": (
            "Oferta temporal: los prompts pueden conservarse o usarse para mejorar los modelos. "
            "No usar con código, secretos ni datos confidenciales."
        ),
        "config": {
            "cli_kind": "opencode",
            "command": ["opencode"],
            "model": "opencode/nemotron-3-ultra-free",
            "read_only": True,
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
        "config": {"model": "gpt-5.6-terra", "api_key_ref": "secret:openai:default"},
    },
    {
        "id": "gemini_api",
        "label": "Gemini API",
        "adapter_type": "gemini_api",
        "channel": "api",
        "provider": "google",
        "config": {"model": "gemini-3.5-flash", "api_key_ref": "secret:google:default"},
    },
    {
        "id": "gemini_api_free",
        "label": "Gemini API · Free tier BYOK",
        "adapter_type": "gemini_api",
        "channel": "api",
        "provider": "google",
        "supported_roles": [
            "reviewer", "code_reviewer", "qa", "test_designer",
            "file_scout", "web_scout", "context_curator",
        ],
        "data_policy": "provider_free_tier",
        "privacy_note": "El free tier puede usar prompts y respuestas para mejorar productos de Google.",
        "config": {
            "model": "gemini-3.5-flash",
            "api_key_ref": "secret:google-free:default",
            "api_key_env": "GEMINI_API_KEY",
            "free_tier": True,
            "quota_tracking": True,
        },
    },
    {
        "id": "groq_api_free",
        "label": "Groq API · Free plan BYOK",
        "adapter_type": "openai_compatible_api",
        "channel": "api",
        "provider": "groq",
        "supported_roles": [
            "reviewer", "code_reviewer", "qa", "test_designer",
            "file_scout", "web_scout", "context_curator",
        ],
        "data_policy": "provider_free_tier",
        "config": {
            "provider": "groq",
            "base_url": "https://api.groq.com/openai/v1",
            "model": "openai/gpt-oss-120b",
            "api_key_ref": "secret:groq:default",
            "api_key_env": "GROQ_API_KEY",
            "free_tier": True,
            "strict_models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
            "quota_tracking": True,
            "api_quota_source": "provider_response_headers",
        },
    },
    {
        "id": "anthropic_api",
        "label": "Anthropic API",
        "adapter_type": "anthropic_sonnet",
        "channel": "api",
        "provider": "anthropic",
        "config": {"model": "claude-sonnet-5", "api_key_ref": "secret:anthropic:default"},
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
        "label": "Gemma local configurado via Codex/LM Studio",
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
    "opencode_zen_free": [
        {
            "value": "opencode/nemotron-3-ultra-free",
            "label": "Nemotron 3 Ultra Free",
            "tier": "premium",
            "research_score": 86,
            "caps": ["coding", "reasoning", "synthesis", "long_ctx"],
            "best_for": ["lead", "team_lead", "architect", "quorum_auditor"],
            "allowed_roles": [
                "lead", "team_lead", "architect", "quorum_auditor",
                "reviewer", "qa", "file_scout", "web_scout",
                "context_curator",
            ],
            "price_note": "Gratis temporal · Tier 1 por capacidad · solo datos no confidenciales",
            "temporary": True,
            "confidential_data_allowed": False,
        },
        {
            "value": "opencode/deepseek-v4-flash-free",
            "label": "DeepSeek V4 Flash Free",
            "tier": "standard",
            "research_score": 82,
            "caps": ["coding", "reasoning", "synthesis", "long_ctx"],
            "best_for": ["reviewer", "code_reviewer", "qa"],
            "allowed_roles": ["reviewer", "qa"],
            "price_note": "Gratis temporal · Tier 2 read-only · solo datos no confidenciales",
            "temporary": True,
            "confidential_data_allowed": False,
        },
        {
            "value": "opencode/mimo-v2.5-free",
            "label": "MiMo V2.5 Free",
            "tier": "standard",
            "research_score": 80,
            "caps": ["coding", "reasoning", "synthesis", "long_ctx", "multimodal"],
            "best_for": ["reviewer", "qa", "web_scout"],
            "allowed_roles": ["reviewer", "qa", "web_scout"],
            "price_note": "Gratis temporal · Tier 2 multimodal · solo datos no confidenciales",
            "temporary": True,
            "confidential_data_allowed": False,
        },
        {
            "value": "opencode/north-mini-code-free",
            "label": "North Mini Code Free",
            "tier": "budget",
            "research_score": 74,
            "caps": ["coding", "reasoning", "synthesis", "long_ctx"],
            "best_for": ["file_scout", "web_scout", "context_curator"],
            "allowed_roles": ["file_scout", "web_scout", "context_curator"],
            "max_criticality": "medium",
            "price_note": "Gratis temporal · Tier 3 agentic coding · solo datos no confidenciales",
            "temporary": True,
            "confidential_data_allowed": False,
        },
    ],
    # Codex subscription: model is selected by ~/.codex/config.toml, not -m.
    # These options are informational for OSS/local_provider paths and the UI picker.
    # Codex ChatGPT subscription. The model is applied via `-c model="<slug>"`
    # (see subscription_cli_adapter._build_codex_command). Slugs mirror codex's
    # own model catalog (~/.codex/models_cache.json). Flat-rate plan → no
    # per-token cost; the cost policy treats this channel as zero-cost.
    "codex_subscription": [
        {
            "value": "gpt-5.6-sol", "label": "GPT-5.6 Sol",
            "tier": "premium", "caps": ["coding", "reasoning", "synthesis", "long_ctx"],
            "best_for": ["lead", "team_lead", "lead_executor", "architect", "quorum_auditor"],
            "price_note": "Suscripción · máxima capacidad · Tier 1",
        },
        {
            "value": "gpt-5.6-terra", "label": "GPT-5.6 Terra",
            "tier": "standard", "caps": ["coding", "reasoning", "synthesis", "long_ctx"],
            "best_for": ["engineer", "software_engineer", "reviewer", "code_reviewer", "qa", "test_designer", "mcp_operator"],
            "price_note": "Suscripción · equilibrio capacidad/cuota · Tier 2",
        },
        {
            "value": "gpt-5.6-luna", "label": "GPT-5.6 Luna",
            "tier": "budget", "caps": ["coding", "reasoning", "synthesis", "long_ctx"],
            "best_for": ["file_scout", "web_scout", "worker"],
            "price_note": "Suscripción · rápido/eficiente · Tier 3",
        },
        {
            "value": "gpt-5.5", "label": "GPT-5.5 (calibrado)",
            "tier": "premium", "caps": ["coding", "reasoning", "synthesis", "long_ctx"],
            "best_for": ["context_curator"],
            "price_note": "Fallback calibrado para Context Curator hasta evaluar Luna",
        },
    ],
    # Ordered best-to-cheapest so options[0] → senior model, last "mini"/"nano" → junior.
    # Tier 1 (lead, quorum): premium models — flagship general-purpose intelligence.
    # Tier 2 (engineer, reviewer): standard — reasoning+coding specialists.
    # Tier 3 (scouts, curator, worker): budget — cheapest capable model.
    "openai_api": [
        {
            "value": "gpt-5.6-sol", "label": "GPT-5.6 Sol",
            "tier": "premium", "caps": ["coding", "synthesis", "reasoning", "long_ctx"],
            "best_for": ["lead", "team_lead", "lead_executor", "architect", "quorum_auditor"],
            "price_note": "$5/$30 MTok · Tier 1",
        },
        {
            "value": "gpt-5.6-terra", "label": "GPT-5.6 Terra",
            "tier": "standard", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["engineer", "software_engineer", "reviewer", "code_reviewer", "qa", "test_designer"],
            "price_note": "$2.50/$15 MTok · Tier 2",
        },
        {
            "value": "gpt-5.6-luna", "label": "GPT-5.6 Luna",
            "tier": "budget", "caps": ["coding", "reasoning", "synthesis", "long_ctx"],
            "best_for": ["file_scout", "web_scout", "context_curator", "worker"],
            "price_note": "$1/$6 MTok · Tier 3",
        },
    ],
    "gemini_api": [
        {
            "value": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro Preview",
            "tier": "premium", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["lead", "team_lead", "lead_executor", "architect", "quorum_auditor"],
            "price_note": "$2/$12 MTok hasta 200K · preview · Tier 1",
        },
        {
            "value": "gemini-3.5-flash", "label": "Gemini 3.5 Flash",
            "tier": "standard", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["engineer", "software_engineer", "reviewer", "code_reviewer", "qa", "test_designer"],
            "price_note": "$1.50/$9 MTok · estable · Tier 2",
        },
        {
            "value": "gemini-3.1-flash-lite", "label": "Gemini 3.1 Flash-Lite",
            "tier": "budget", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["file_scout", "web_scout", "context_curator"],
            "price_note": "$0.25/$1.50 MTok · estable · Tier 3",
        },
    ],
    "gemini_api_free": [
        {
            "value": "gemini-3.5-flash", "label": "Gemini 3.5 Flash · Free tier",
            "tier": "standard", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["reviewer", "code_reviewer", "qa", "test_designer"],
            "allowed_roles": ["reviewer", "qa", "test_designer"],
            "price_note": "Free tier BYOK · cuota del proyecto · datos sujetos a términos free",
        },
        {
            "value": "gemini-3.1-flash-lite", "label": "Gemini 3.1 Flash-Lite · Free tier",
            "tier": "budget", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["file_scout", "web_scout", "context_curator"],
            "allowed_roles": ["file_scout", "web_scout", "context_curator"],
            "max_criticality": "medium",
            "price_note": "Free tier BYOK · Tier 3 · cuota del proyecto",
        },
    ],
    "groq_api_free": [
        {
            "value": "openai/gpt-oss-120b", "label": "GPT-OSS 120B · Groq Free",
            "tier": "standard", "caps": ["reasoning", "synthesis", "coding"],
            "best_for": ["reviewer", "code_reviewer", "qa", "test_designer"],
            "allowed_roles": ["reviewer", "qa", "test_designer"],
            "price_note": "Free plan · 1000 RPD / 8K TPM publicados · pendiente calibración local",
        },
        {
            "value": "qwen/qwen3.6-27b", "label": "Qwen 3.6 27B · Groq Free",
            "tier": "budget", "caps": ["reasoning", "synthesis", "coding"],
            "best_for": ["file_scout", "web_scout", "context_curator"],
            "allowed_roles": ["file_scout", "web_scout", "context_curator"],
            "max_criticality": "medium",
            "structured_output": "json_object",
            "structured_output_repair": "bounded_once_authority_preserving",
            "price_note": "Free plan · 1000 RPD / 8K TPM publicados · Tier 3 preliminar",
        },
        {
            "value": "openai/gpt-oss-20b", "label": "GPT-OSS 20B · Groq Free",
            "tier": "budget", "caps": ["reasoning", "synthesis", "coding"],
            "best_for": ["file_scout", "web_scout", "context_curator"],
            "allowed_roles": ["file_scout", "web_scout", "context_curator"],
            "max_criticality": "medium",
            "price_note": "Free plan · 1000 RPD / 8K TPM · structured output estricto",
        },
    ],
    "antigravity_subscription": [
        {
            "value": "gemini-3.6-flash-high", "label": "Gemini 3.6 Flash (High)",
            "tier": "standard", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": [], "automatic": False, "requires_probe": True,
            "price_note": "Antigravity · catálogo vivo; submit rechazado en 1.1.5",
        },
        {
            "value": "gemini-3.6-flash-medium", "label": "Gemini 3.6 Flash (Medium)",
            "tier": "standard", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": [], "automatic": False, "requires_probe": True,
            "price_note": "Antigravity · review durable 3/3; baseline 3.5 High conservado",
        },
        {
            "value": "gemini-3.6-flash-low", "label": "Gemini 3.6 Flash (Low)",
            "tier": "budget", "caps": ["synthesis", "coding", "long_ctx"],
            "best_for": [], "automatic": False, "requires_probe": True,
            "price_note": "Antigravity · catálogo vivo; submit rechazado en 1.1.5",
        },
        {
            "value": "gemini-3.1-pro-high", "label": "Gemini 3.1 Pro (High)",
            "tier": "premium", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["lead", "team_lead", "lead_executor", "architect", "quorum_auditor"],
            "price_note": "Antigravity subscription · Tier 1 Lead/Quorum",
        },
        {
            "value": "gemini-3.1-pro-low", "label": "Gemini 3.1 Pro (Low)",
            "tier": "standard", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": [],
            "automatic": False,
            "price_note": "Antigravity subscription · Pro con esfuerzo Low · selección manual",
        },
        {
            "value": "gemini-3.5-flash-high", "label": "Gemini 3.5 Flash (High)",
            "tier": "standard", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["reviewer", "code_reviewer", "qa", "test_designer"],
            "price_note": "Suscripción Antigravity · Tier 2 review/QA",
        },
        {
            "value": "claude-opus-4-6-thinking", "label": "Claude Opus 4.6 (Thinking)",
            "tier": "premium", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": [],
            "price_note": "Alternativa premium disponible en Antigravity; misma cuota/transporte",
        },
        {
            "value": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6",
            "tier": "standard", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["engineer", "software_engineer"],
            "price_note": "Antigravity · Tier 2 coding calibrado (3×9/9)",
        },
        {
            "value": "gemini-3.5-flash-medium", "label": "Gemini 3.5 Flash (Medium)",
            "tier": "standard", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["worker"],
            "price_note": "Suscripción Antigravity · menor esfuerzo",
        },
        {
            "value": "gemini-3.5-flash-low", "label": "Gemini 3.5 Flash (Low)",
            "tier": "budget", "caps": ["synthesis", "coding", "long_ctx"],
            "best_for": ["file_scout", "web_scout", "context_curator", "worker"],
            "price_note": "Suscripción Antigravity · Tier 3",
        },
        {
            "value": "gpt-oss-120b-medium", "label": "GPT-OSS 120B (Medium)",
            "tier": "budget", "caps": ["reasoning", "synthesis", "coding"],
            "best_for": ["file_scout", "web_scout", "worker"],
            "price_note": "Fallback incluido en el catálogo de Antigravity",
        },
    ],
    "anthropic_api": [
        {
            "value": "claude-opus-4-8", "label": "Claude Opus 4.8",
            "tier": "premium", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["lead", "team_lead", "lead_executor", "quorum_auditor"],
            "price_note": "$5/$25 MTok · default Tier 1",
        },
        {
            "value": "claude-fable-5", "label": "Claude Fable 5",
            "tier": "premium", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["architect"],
            "automatic": False,
            "price_note": "$10/$50 MTok · escalado excepcional Tier 1 · retención 30 días",
        },
        {
            "value": "claude-sonnet-5", "label": "Claude Sonnet 5",
            "tier": "standard", "caps": ["synthesis", "coding", "reasoning", "long_ctx"],
            "best_for": ["engineer", "software_engineer", "reviewer", "code_reviewer", "qa", "test_designer"],
            "price_note": "$3/$15 MTok estándar · Tier 2",
        },
        {
            "value": "claude-haiku-4-5", "label": "Claude Haiku 4.5",
            "tier": "budget", "caps": ["reasoning", "synthesis", "coding"],
            "best_for": ["file_scout", "web_scout", "context_curator", "worker"],
            "price_note": "$1/$5 MTok · Tier 3",
        },
    ],
    "claude_subscription_blocked": [
        {
            "value": "claude-opus-4-8", "label": "Claude Opus 4.8",
            "tier": "premium", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["lead", "team_lead", "lead_executor", "quorum_auditor"],
            "price_note": "Claude Code · Tier 1; sujeto a plan/cuota",
        },
        {
            "value": "claude-fable-5", "label": "Claude Fable 5",
            "tier": "premium", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["architect"],
            "automatic": False,
            "price_note": "Claude Code · escalado excepcional; puede requerir créditos",
        },
        {
            "value": "claude-sonnet-5", "label": "Claude Sonnet 5",
            "tier": "standard", "caps": ["reasoning", "synthesis", "coding", "long_ctx"],
            "best_for": ["engineer", "software_engineer", "reviewer", "code_reviewer", "qa", "test_designer"],
            "price_note": "Claude Code · Tier 2; sujeto a plan/cuota",
        },
        {
            "value": "claude-haiku-4-5", "label": "Claude Haiku 4.5",
            "tier": "budget", "caps": ["reasoning", "synthesis", "coding"],
            "best_for": ["file_scout", "web_scout", "context_curator", "worker"],
            "price_note": "Claude Code · Tier 3; sujeto a plan/cuota",
        },
    ],
    "local_qwen_ollama": [
        {
            "value": "qwen3-coder:30b", "label": "Qwen3 Coder 30B",
            "tier": "standard", "caps": ["coding", "reasoning", "long_ctx"],
            "best_for": ["engineer", "software_engineer", "test_designer"],
            "price_note": "Local · Tier 2; requiere instalación y health explícitos",
        },
        {
            "value": "qwen2.5-coder:14b", "label": "Qwen 2.5 Coder 14B",
            "tier": "budget", "caps": ["coding", "synthesis"],
            "best_for": ["file_scout", "context_curator"],
            "price_note": "Local gratuito · coding · Tier 3",
        },
        {
            "value": "qwen2.5-coder:32b", "label": "Qwen 2.5 Coder 32B",
            "tier": "standard", "caps": ["coding", "reasoning"], "best_for": [],
            "price_note": "Local gratuito · Tier 2 Engineer",
        },
    ],
    "local_gem4_lmstudio": [
        {
            "value": "gemma-3-4b-it", "label": "Gemma 3 4B Instruct (legacy configurado)",
            "tier": "budget", "caps": ["synthesis"],
            "best_for": ["file_scout", "web_scout", "context_curator", "worker"],
            "price_note": "Local gratuito · Tier 3 Scouts/Worker",
        },
        {
            "value": "gemma-3-12b-it", "label": "Gemma 3 12B Instruct (legacy)",
            "tier": "standard", "caps": ["synthesis", "coding"],
            "best_for": ["engineer"],
            "price_note": "Local gratuito · Tier 2 Engineer/Reviewer",
        },
        {
            "value": "google/gemma-4-26b-a4b", "label": "Gemma 4 26B A4B",
            "tier": "standard", "caps": ["synthesis", "coding", "reasoning", "long_ctx"],
            "best_for": ["engineer", "software_engineer", "reviewer"],
            "price_note": "Local · Tier 2; requiere descarga y health explícitos",
        },
    ],
    "local_gemma4_ollama": [
        {
            "value": "gemma4:e4b", "label": "Gemma 4 E4B",
            "tier": "budget", "caps": ["coding", "synthesis"],
            "best_for": ["file_scout", "context_curator", "worker"],
            "price_note": "Local gratuito · cabe 100% en VRAM · ultrarrápido",
        },
        {
            "value": "gemma4:26b", "label": "Gemma 4 26B MoE",
            "tier": "standard", "caps": ["reasoning", "coding", "synthesis", "long_ctx"],
            "best_for": ["engineer", "software_engineer", "reviewer", "test_designer"],
            "price_note": "Local · Tier 2; requiere instalación y health explícitos",
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
        redacted_config = redacted.get("config")
        if isinstance(redacted_config, dict) and redacted_config.get("model"):
            redacted_config["model"] = _canonical_model_id(
                profile_id, str(redacted_config["model"])
            )
        profile_health = health.get(profile_id, {"status": "untested"})
        options, catalog = executable_model_options(
            profile_id,
            profile=redacted,
            health=profile_health,
        )
        redacted["model_options"] = options
        redacted["model_catalog"] = catalog
        redacted["health"] = profile_health
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
    if profile_id and merged.get("model"):
        merged["model"] = _canonical_model_id(profile_id, str(merged["model"]))
    if str(merged.get("cli_kind") or "").strip().lower() == "codex":
        _inject_codex_context_capacity(merged)
    return merged


_ANTIGRAVITY_MODEL_ALIASES = {
    "Gemini 3.6 Flash (High)": "gemini-3.6-flash-high",
    "Gemini 3.6 Flash (Medium)": "gemini-3.6-flash-medium",
    "Gemini 3.6 Flash (Low)": "gemini-3.6-flash-low",
    "Gemini 3.1 Pro (High)": "gemini-3.1-pro-high",
    "Gemini 3.1 Pro (Low)": "gemini-3.1-pro-low",
    "Gemini 3.5 Flash (High)": "gemini-3.5-flash-high",
    "Gemini 3.5 Flash (Medium)": "gemini-3.5-flash-medium",
    "Gemini 3.5 Flash (Low)": "gemini-3.5-flash-low",
    "Claude Opus 4.6 (Thinking)": "claude-opus-4-6-thinking",
    "Claude Sonnet 4.6 (Thinking)": "claude-sonnet-4-6",
    "GPT-OSS 120B (Medium)": "gpt-oss-120b-medium",
}


def _canonical_model_id(profile_id: str, model: str) -> str:
    clean = str(model or "").strip()
    if profile_id == "antigravity_subscription":
        return _ANTIGRAVITY_MODEL_ALIASES.get(clean, clean)
    return clean


def _inject_codex_context_capacity(config: dict[str, Any]) -> None:
    """Enrich Codex config from its locally refreshed catalog, never vendor constants."""
    try:
        if int(config.get("context_window_tokens") or 0) > 0:
            return
    except (TypeError, ValueError):
        pass
    codex_root = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    model = str(config.get("model") or "").strip()
    if not model:
        try:
            model = str(tomllib.loads((codex_root / "config.toml").read_text(encoding="utf-8")).get("model") or "")
        except Exception:
            return
    try:
        catalog = json.loads((codex_root / "models_cache.json").read_text(encoding="utf-8"))
        entry = next(
            item for item in (catalog.get("models") or [])
            if isinstance(item, dict) and str(item.get("slug") or "") == model
        )
        context_window = int(entry.get("context_window") or 0)
        effective_percent = int(entry.get("effective_context_window_percent") or 100)
    except Exception:
        return
    if context_window > 0:
        config["context_window_tokens"] = max(1, context_window * effective_percent // 100)
        config["context_window_source"] = "codex_models_cache"


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
    current = profiles.get(profile_id) if isinstance(profiles.get(profile_id), dict) else {}
    profiles[profile_id] = {**current, **payload}
    data["profiles"] = profiles
    _write_json(path, data)
    return profiles[profile_id]


def record_model_health(
    profile_id: str,
    model: str,
    *,
    available: bool,
    reason: str,
    status: str | None = None,
) -> None:
    """Persist model-level evidence without discarding profile health."""
    clean_profile = str(profile_id or "").strip()
    clean_model = _canonical_model_id(clean_profile, str(model or ""))
    if not clean_profile or not clean_model:
        return
    path = user_config_dir() / "adapter_health.json"
    data = _read_json(path)
    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
    current = profiles.get(clean_profile) if isinstance(profiles.get(clean_profile), dict) else {}
    verified = {
        _canonical_model_id(clean_profile, str(item))
        for item in current.get("verified_models", [])
        if str(item).strip()
    }
    unavailable = current.get("unavailable_models") if isinstance(current.get("unavailable_models"), dict) else {}
    unavailable = {
        _canonical_model_id(clean_profile, str(key)): str(value)
        for key, value in unavailable.items()
    }
    model_states = current.get("model_states") if isinstance(current.get("model_states"), dict) else {}
    model_states = {
        _canonical_model_id(clean_profile, str(key)): dict(value)
        for key, value in model_states.items()
        if isinstance(value, dict)
    }
    state = str(status or ("verified" if available else _model_failure_status(reason))).strip().lower()
    if state not in {"verified", "unavailable", "rate_limited", "retired"}:
        state = "verified" if available else "unavailable"
    if available:
        verified.add(clean_model)
        unavailable.pop(clean_model, None)
    else:
        verified.discard(clean_model)
        unavailable[clean_model] = str(reason or "model_unavailable")
    model_states[clean_model] = {
        "status": state,
        "reason": str(reason or state),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    profiles[clean_profile] = {
        **current,
        "verified_models": sorted(verified),
        "unavailable_models": unavailable,
        "model_states": model_states,
    }
    data["profiles"] = profiles
    _write_json(path, data)


def record_model_catalog(
    profile_id: str,
    models: list[str],
    *,
    source: str,
    status: str = "current",
    reason: str = "",
) -> None:
    """Persist authenticated discovery separately from execution evidence."""
    clean_profile = str(profile_id or "").strip()
    if not clean_profile:
        return
    clean_models = sorted({
        _canonical_model_id(clean_profile, str(model))
        for model in models
        if str(model).strip()
    })
    save_adapter_health(clean_profile, {
        "catalog_status": str(status or "unverified"),
        "catalog_source": str(source or "provider_api"),
        "catalog_models": clean_models,
        "catalog_reason": str(reason or ""),
        "catalog_checked_at": datetime.now(timezone.utc).isoformat(),
    })


def _model_failure_status(reason: str) -> str:
    value = str(reason or "").strip().lower()
    if any(token in value for token in ("rate_limit", "rate limited", "429", "quota")):
        return "rate_limited"
    if any(token in value for token in ("retired", "deprecated", "removed")):
        return "retired"
    return "unavailable"


def model_is_selectable(option: dict[str, Any]) -> bool:
    """Whether Team/hiring may promise execution for this exact option."""
    if "selectable" in option:
        return option.get("selectable") is True
    return option.get("available") is not False


def model_options() -> dict[str, list[dict[str, Any]]]:
    return MODEL_OPTIONS_BY_PROFILE


def executable_model_options(
    profile_id: str,
    *,
    profile: dict[str, Any] | None = None,
    health: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Annotate the commercial catalog with executable local availability.

    A model may remain visible for diagnosis while ``available=False`` keeps
    Team from presenting it as a working selection. Catalog discovery is
    adapter-specific and never treats another provider's labels as aliases.
    """
    selected = profile or _profile_by_id(profile_id) or {"id": profile_id}
    current_health = health if isinstance(health, dict) else (
        adapter_health().get("profiles", {}).get(profile_id, {})
    )
    verified = {
        _canonical_model_id(profile_id, str(item))
        for item in current_health.get("verified_models", [])
        if str(item).strip()
    }
    unavailable = current_health.get("unavailable_models") if isinstance(current_health.get("unavailable_models"), dict) else {}
    unavailable = {
        _canonical_model_id(profile_id, str(key)): str(value)
        for key, value in unavailable.items()
    }
    model_states = current_health.get("model_states") if isinstance(current_health.get("model_states"), dict) else {}
    model_states = {
        _canonical_model_id(profile_id, str(key)): value
        for key, value in model_states.items()
        if isinstance(value, dict)
    }
    declared_options = selected.get("model_options") if isinstance(selected.get("model_options"), list) else []
    options = [
        dict(item) for item in (MODEL_OPTIONS_BY_PROFILE.get(profile_id) or declared_options)
        if isinstance(item, dict)
    ]
    status = str(selected.get("status") or "")
    config = selected.get("config") if isinstance(selected.get("config"), dict) else {}
    channel = str(selected.get("channel") or "")
    provider = str(selected.get("provider") or "").lower()
    cli_kind = str(config.get("cli_kind") or "").lower()

    catalog: dict[str, Any] = {"status": "official_catalog", "source": "project_catalog"}
    discovered: set[str] | None = None
    unavailable_reason = ""
    if status == "blocked_by_provider":
        catalog = {"status": "blocked", "source": "profile", "reason": "blocked_by_provider"}
        discovered = set()
        unavailable_reason = "Perfil bloqueado por el proveedor"
    elif channel == "local":
        names = _local_model_names(config)
        if names is not None:
            discovered = set(names)
            catalog = {"status": "current", "source": str(config.get("local_provider") or "local"), "count": len(names)}
        else:
            catalog = {"status": "unverified", "source": str(config.get("local_provider") or "local")}
    elif cli_kind == "antigravity" or "antigravity" in provider:
        names = _antigravity_model_names(config)
        if names is not None:
            discovered = set(names)
            catalog = {"status": "current", "source": "agy models", "count": len(names)}
        else:
            catalog = {"status": "unverified", "source": "agy models"}
    elif cli_kind == "opencode" or "opencode" in provider:
        names = _opencode_model_names(config)
        if names is not None:
            discovered = set(names)
            catalog = {"status": "current", "source": "opencode models opencode", "count": len(names)}
        else:
            executable = _resolve_cli_executable("opencode")
            if executable is None:
                discovered = set()
                unavailable_reason = "OpenCode CLI no está instalado"
                catalog = {"status": "unavailable", "source": "opencode CLI"}
            else:
                catalog = {"status": "unverified", "source": "opencode models opencode"}
    elif cli_kind == "codex" and channel == "subscription":
        codex_catalog = _codex_catalog_compatibility(config)
        catalog = codex_catalog
        if codex_catalog.get("status") == "current":
            discovered = set(codex_catalog.get("models") or [])
        elif codex_catalog.get("status") == "cli_update_required":
            discovered = set()
            unavailable_reason = (
                f"Requiere actualizar Codex CLI {codex_catalog.get('installed_version') or '?'} "
                f"al catálogo {codex_catalog.get('catalog_client_version') or '?'}"
            )
        elif codex_catalog.get("status") == "unavailable":
            discovered = set()
            unavailable_reason = "Codex CLI no está instalado"
    elif (
        channel == "api"
        and str(current_health.get("catalog_status") or "") == "current"
        and isinstance(current_health.get("catalog_models"), list)
    ):
        discovered = {
            _canonical_model_id(profile_id, str(item))
            for item in current_health.get("catalog_models", [])
            if str(item).strip()
        }
        catalog = {
            "status": str(current_health.get("catalog_status") or "current"),
            "source": str(current_health.get("catalog_source") or "provider API"),
            "count": len(discovered),
            "checked_at": current_health.get("catalog_checked_at"),
        }

    annotated: list[dict[str, Any]] = []
    for option in options:
        value = str(option.get("value") or "")
        state_entry = model_states.get(value) if isinstance(model_states.get(value), dict) else {}
        state = str(state_entry.get("status") or "")
        if state == "verified" or value in verified:
            available = True
            availability = "verified"
            selectable = True
            reason = str(state_entry.get("reason") or "Verificado por una run completada")
        elif state in {"unavailable", "rate_limited", "retired"} or value in unavailable:
            availability = state or "unavailable"
            available = availability == "rate_limited"
            selectable = False
            reason = str(state_entry.get("reason") or unavailable.get(value) or availability)
        elif discovered is not None:
            available = value in discovered
            availability = "catalogued" if available else "unavailable"
            requires_probe = bool(option.get("requires_probe"))
            selectable = available and channel != "api" and not requires_probe
            if available and (channel == "api" or requires_probe):
                reason = "Enumerado por el proveedor; falta probe estructurado del modelo"
            else:
                reason = "Enumerado por el runtime" if available else (unavailable_reason or "No está en el catálogo del runtime")
        else:
            # El ID comercial permanece visible, pero una API no promete
            # ejecución hasta discovery autenticado + probe del modelo exacto.
            available = channel == "api"
            availability = "catalogued" if available else "unverified"
            selectable = False if channel == "api" else available
            reason = (
                "ID catalogado; requiere discovery autenticado y probe estructurado"
                if available else "Disponibilidad aún no verificada"
            )
        annotated.append({
            **option,
            "available": available,
            "selectable": selectable,
            "availability": availability,
            "verification_status": availability,
            "availability_reason": reason,
        })
    return annotated, {key: value for key, value in catalog.items() if key != "models"}


def validate_model_selection(adapter_config: dict[str, Any]) -> None:
    """Reject a known catalog option that the selected runtime cannot execute."""
    profile_id = str(adapter_config.get("profile_id") or "").strip()
    model = str(adapter_config.get("model") or "").strip()
    if not profile_id or not model:
        return
    profile = _profile_by_id(profile_id)
    has_catalog = profile_id in MODEL_OPTIONS_BY_PROFILE or bool(
        isinstance((profile or {}).get("model_options"), list)
    )
    if not has_catalog:
        return
    options, _catalog = executable_model_options(profile_id, profile=profile)
    selected = next((item for item in options if str(item.get("value") or "") == model), None)
    if selected is None:
        raise ValueError(f"model {model!r} is not in profile {profile_id!r} catalog")
    if not model_is_selectable(selected):
        reason = str(selected.get("availability_reason") or "availability not verified")
        raise ValueError(f"model {model!r} is not executable for {profile_id!r}: {reason}")


def model_fallback_for_role(
    profile_id: str,
    failed_model: str,
    role: str,
    *,
    run_profile: str = "",
    criticality: str = "medium",
    data_class: str = "",
    required_capabilities: list[str] | None = None,
) -> dict[str, Any] | None:
    """Return the least-disruptive executable fallback in the same profile.

    Family continuity wins over tier continuity; role fit breaks ties. Manual-
    only options (for example Fable) are never proposed by recovery.
    """
    failed_value = str(failed_model or "").strip()
    ranked = model_options_for_role(profile_id, role, executable_only=True)
    profile = _profile_by_id(profile_id) or {"id": profile_id}
    candidates = [
        item for item in ranked
        if str(item.get("value") or "") != failed_value
        and model_is_selectable(item)
        and item.get("automatic", True) is not False
        and compatibility_decision(
            profile=profile,
            model=item,
            role=role,
            run_profile=run_profile,
            criticality=criticality,
            data_class=data_class,
            required_capabilities=required_capabilities or [],
            role_profile=ROLE_CAPABILITY_PROFILES.get(canonical_role(role), {}),
        ).get("allowed")
    ]
    if not candidates:
        return None
    static_options = MODEL_OPTIONS_BY_PROFILE.get(profile_id, [])
    failed = next(
        (item for item in static_options if str(item.get("value") or "") == failed_value),
        {},
    )
    failed_tier = str(failed.get("tier") or "")
    failed_family = _model_family(failed_value)
    indexed = list(enumerate(candidates))
    _index, selected = min(
        indexed,
        key=lambda pair: (
            _model_family(str(pair[1].get("value") or "")) != failed_family,
            bool(failed_tier) and str(pair[1].get("tier") or "") != failed_tier,
            pair[0],
        ),
    )
    selected_tier = str(selected.get("tier") or "")
    selected_family = _model_family(str(selected.get("value") or ""))
    return {
        **selected,
        "failed_model": failed_value,
        "failed_tier": failed_tier or None,
        "failed_family": failed_family or None,
        "changes_tier": bool(failed_tier and selected_tier != failed_tier),
        "changes_family": bool(failed_family and selected_family != failed_family),
    }


def _model_family(model: str) -> str:
    normalized = str(model or "").strip().lower()
    for family, prefixes in {
        "gpt": ("gpt-", "openai/gpt-"),
        "claude": ("claude-", "claude "),
        "gemini": ("gemini-", "gemini "),
        "qwen": ("qwen",),
        "gemma": ("gemma", "google/gemma"),
    }.items():
        if normalized.startswith(prefixes):
            return family
    match = re.match(r"[a-z0-9]+", normalized)
    return match.group(0) if match else normalized


def _antigravity_model_names(config: dict[str, Any]) -> list[str] | None:
    command = config.get("command") if isinstance(config.get("command"), list) else ["agy"]
    return _antigravity_model_names_cached(tuple(str(item) for item in command))


@lru_cache(maxsize=8)
def _antigravity_model_names_cached(command: tuple[str, ...]) -> list[str] | None:
    executable = _resolve_cli_executable(str((command or ("agy",))[0] or "agy"))
    if not executable:
        return None
    try:
        proc = subprocess.run(
            [executable, "models"],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def _opencode_model_names(config: dict[str, Any]) -> list[str] | None:
    command = config.get("command") if isinstance(config.get("command"), list) else ["opencode"]
    return _opencode_model_names_cached(tuple(str(item) for item in command))


@lru_cache(maxsize=4)
def _opencode_model_names_cached(command: tuple[str, ...]) -> list[str] | None:
    executable = _resolve_cli_executable(str((command or ("opencode",))[0] or "opencode"))
    if not executable:
        return None
    try:
        proc = subprocess.run(
            [executable, "models", "opencode"],
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip().startswith("opencode/")]


def _local_model_names(config: dict[str, Any]) -> list[str] | None:
    local_provider = str(config.get("local_provider") or "ollama").strip().lower()
    return _local_model_names_cached(local_provider)


@lru_cache(maxsize=4)
def _local_model_names_cached(local_provider: str) -> list[str] | None:
    import urllib.request

    if local_provider == "ollama":
        url, models_key, name_key = "http://localhost:11434/api/tags", "models", "name"
    else:
        url, models_key, name_key = "http://localhost:1234/v1/models", "data", "id"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return [str(item.get(name_key) or "") for item in payload.get(models_key, []) if str(item.get(name_key) or "")]


def _codex_catalog_compatibility(config: dict[str, Any]) -> dict[str, Any]:
    command = config.get("command") if isinstance(config.get("command"), list) else ["codex"]
    codex_root = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    cache_path = codex_root / "models_cache.json"
    try:
        cache_mtime = cache_path.stat().st_mtime_ns
    except OSError:
        cache_mtime = 0
    return _codex_catalog_compatibility_cached(
        tuple(str(item) for item in command), str(cache_path), cache_mtime
    )


@lru_cache(maxsize=8)
def _codex_catalog_compatibility_cached(
    command: tuple[str, ...], cache_path: str, cache_mtime: int
) -> dict[str, Any]:
    del cache_mtime  # only participates in the cache key
    executable = _resolve_cli_executable(str((command or ("codex",))[0] or "codex"))
    if not executable:
        return {"status": "unavailable", "source": "codex", "reason": "cli_not_found"}
    try:
        proc = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        version_match = re.search(r"(\d+\.\d+\.\d+)", (proc.stdout or "") + " " + (proc.stderr or ""))
        installed = version_match.group(1) if version_match else ""
    except Exception:
        installed = ""
    cache = _read_json(Path(cache_path))
    catalog_version = str(cache.get("client_version") or "")
    models = [
        str(item.get("slug") or "")
        for item in cache.get("models", [])
        if isinstance(item, dict) and str(item.get("slug") or "")
    ]
    base = {
        "source": "codex models_cache.json",
        "installed_version": installed or None,
        "catalog_client_version": catalog_version or None,
        "models": models,
    }
    if not installed or not catalog_version or not models:
        return {**base, "status": "unverified"}
    if _version_tuple(installed) < _version_tuple(catalog_version):
        return {**base, "status": "cli_update_required", "reason": "catalog_requires_newer_cli"}
    return {**base, "status": "current"}


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = [int(item) for item in re.findall(r"\d+", str(value))[:3]]
    padded = (parts + [0, 0, 0])[:3]
    return padded[0], padded[1], padded[2]


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
    "lead_executor": {
        "preferred_tier": "premium",
        "capabilities_needed": ["reasoning", "synthesis", "coding"],
        "requires_workspace": True,
        "prefers_cheaper": False,
        "note": "Ejecuta directamente en modo Solo Lead. Tier 1 por autoridad y alcance.",
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
        "note": "Implementa código. Requiere workspace write, por CLI o API con ops gobernadas. Tier 2.",
    },
    "software_engineer": {
        "preferred_tier": "standard",
        "capabilities_needed": ["coding", "reasoning"],
        "requires_workspace": True,
        "prefers_cheaper": True,
        "note": "Alias de engineer. Tier 2.",
    },
    "reviewer": {
        "preferred_tier": "standard",
        "capabilities_needed": ["synthesis", "reasoning"],
        "requires_workspace": False,
        "prefers_cheaper": True,
        "note": "Revisa código y hace análisis estático (también cubre QA). Tier 2. API adapter OK.",
    },
    "code_reviewer": {
        "preferred_tier": "standard",
        "capabilities_needed": ["synthesis", "reasoning", "coding"],
        "requires_workspace": False,
        "prefers_cheaper": True,
        "note": "Alias de reviewer. Tier 2.",
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
        "preferred_tier": "standard",
        "capabilities_needed": ["synthesis", "coding"],
        "requires_workspace": True,
        "prefers_cheaper": True,
        "note": "Verificación adversarial opcional. Tier 2; el test_runner determinista ejecuta los tests.",
    },
    "test_designer": {
        "preferred_tier": "standard",
        "capabilities_needed": ["reasoning", "coding"],
        "requires_workspace": True,
        "prefers_cheaper": True,
        "note": "Diseña aceptación independiente desde la spec. Tier 2.",
    },
    "worker": {
        "preferred_tier": "budget",
        "capabilities_needed": ["synthesis"],
        "requires_workspace": True,
        "prefers_cheaper": True,
        "note": "Tareas delegadas genéricas. Tier 3.",
    },
}


def model_options_for_role(
    profile_id: str,
    role: str,
    *,
    executable_only: bool = False,
) -> list[dict[str, Any]]:
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
    if executable_only:
        options, _catalog = executable_model_options(profile_id)
    else:
        options = MODEL_OPTIONS_BY_PROFILE.get(profile_id, [])
    role_key = canonical_role(role)
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
        is_recommended = role_key in {
            canonical_role(item) for item in (opt.get("best_for") or [])
        }

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
        automatic = opt.get("automatic", True) is not False
        role_score = cap_score + tier_adj + (20 if is_recommended else 0) - (100 if not automatic else 0)

        # Build a short fit reason for the UI
        fit_parts = []
        matched = [c for c in ["coding", "reasoning", "synthesis"] if c in caps and locals()[f"needs_{c}"]]
        if matched:
            fit_parts.append(" + ".join(matched))
        if tier == preferred_tier:
            tier_labels = {"premium": "Tier 1", "standard": "Tier 2", "budget": "Tier 3"}
            fit_parts.append(tier_labels.get(tier, tier))
        if not automatic:
            fit_parts.append("selección manual")
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
    opencode = _resolve_cli_executable("opencode")
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
            "id": "opencode",
            "label": "OpenCode CLI",
            "command": "opencode",
            "resolved_command": opencode,
            "available": opencode is not None,
            "login_supported": True,
            "login_command": _login_display_command(["opencode", "auth", "login", "--provider", "opencode"]),
            "alternate_login_commands": [_login_display_command(["opencode", "auth", "login"])],
            "login_hint": "Conecta OpenCode Zen una vez; AI Teams reutiliza esa sesión sin copiar la credencial.",
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
        "opencode": ["opencode", "auth", "login", "--provider", "opencode"],
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
    if os.name == "nt" and Path(text).suffix.lower() not in {".exe", ".cmd", ".bat"}:
        # ``where`` returns npm's extensionless POSIX shim before the .cmd
        # launcher. CreateProcess cannot execute that file directly on Windows.
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
