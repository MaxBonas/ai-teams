from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class AlertPolicy:
    min_success_rate_percent: float = 85.0
    max_api_dependency_percent: float = 40.0
    min_execution_count_for_alert: int = 5
    max_recurrent_failures: int = 3


@dataclass
class RouterPolicy:
    pro_first: bool = True
    max_subscription_attempts: int = 3
    max_api_attempts: int = 2
    api_fallback_enabled: bool = True
    complexity_threshold_for_api: str = "high"
    criticality_threshold_for_api: str = "high"
    preferred_subscription_providers: list[str] = field(
        default_factory=lambda: ["openai", "google", "anthropic"]
    )
    preferred_api_providers: list[str] = field(default_factory=lambda: ["openai", "groq"])
    daily_api_budget_usd: float = 10.0
    monthly_api_budget_usd: float = 200.0
    role_model_preferences: dict[str, list[str]] = field(
        default_factory=lambda: {
            "team_lead": ["gpt-5.3-codex", "claude-code", "gemini-3.1-pro"],
            "researcher": ["gemini-3.1-pro", "claude-code", "gpt-5.3-codex"],
            "engineer": [
                "gpt-5.3-codex",
                "claude-code",
                "gemini-3.1-pro",
                "gpt-4.1-mini",
                "llama-3.3-70b-versatile",
            ],
            "reviewer": [
                "claude-code",
                "gpt-5.3-codex",
                "gemini-3.1-pro",
                "llama-3.3-70b-versatile",
            ],
            "qa": ["claude-code", "gpt-4o-mini", "gemini-3.1-pro", "llama-3.3-70b-versatile"],
        }
    )
    role_provider_preferences: dict[str, list[str]] = field(
        default_factory=lambda: {
            "team_lead": ["openai", "google", "anthropic"],
            "researcher": ["google", "openai", "groq"],
            "engineer": ["openai", "google", "groq"],
            "reviewer": ["openai", "google", "groq"],
            "qa": ["openai", "google", "groq"],
        }
    )
    peer_consultation_diversity_required: bool = True
    enforce_role_model_preferences: bool = False
    strict_role_policy_environments: list[str] = field(default_factory=lambda: ["prod"])


def build_default_router_policy() -> RouterPolicy:
    policy = RouterPolicy()

    # Límites de intentos por canal — sobreescribibles via env sin romper tests
    sub_attempts_raw = os.getenv("AITEAM_MAX_SUBSCRIPTION_ATTEMPTS", "").strip()
    if sub_attempts_raw.isdigit():
        policy.max_subscription_attempts = max(1, int(sub_attempts_raw))

    api_attempts_raw = os.getenv("AITEAM_MAX_API_ATTEMPTS", "").strip()
    if api_attempts_raw.isdigit():
        policy.max_api_attempts = max(1, int(api_attempts_raw))

    enforce_raw = os.getenv("AITEAM_ENFORCE_ROLE_MODEL_PREFERENCES", "0").strip().lower()
    if enforce_raw in {"1", "true", "yes", "on"}:
        policy.enforce_role_model_preferences = True

    peer_diversity_raw = os.getenv(
        "AITEAM_PEER_CONSULTATION_DIVERSITY_REQUIRED",
        "",
    ).strip().lower()
    if peer_diversity_raw in {"1", "true", "yes", "on"}:
        policy.peer_consultation_diversity_required = True
    elif peer_diversity_raw in {"0", "false", "no", "off"}:
        policy.peer_consultation_diversity_required = False

    envs_raw = os.getenv("AITEAM_STRICT_ROLE_POLICY_ENVS", "").strip()
    if envs_raw:
        envs = [item.strip().lower() for item in envs_raw.split(",") if item.strip()]
        if envs:
            policy.strict_role_policy_environments = envs

    return policy
