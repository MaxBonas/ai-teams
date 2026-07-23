"""Evidencia multidimensional de tier por modelo y canal.

El tier resume capacidad/responsabilidad. Economía y velocidad se conservan
como ejes separados para no confundir coste marginal cero con cuota ilimitada,
ni precio bajo con aptitud para un rol.
"""
from __future__ import annotations

from typing import Any

from aiteam.pricing import price_per_mtok


TIER_POLICY_VERSION = "capability_economy_speed_v1"
VALID_TIERS = frozenset({"premium", "standard", "budget"})

_SPEED_EVIDENCE: dict[str, tuple[str, str, int | None]] = {
    "gpt-5.6-sol": ("quality_first", "openai_official_family_positioning", None),
    "gpt-5.6-terra": ("balanced", "openai_official_family_positioning", None),
    "gpt-5.6-luna": ("fast", "openai_official_family_positioning", None),
    "claude-fable-5": ("slow", "anthropic_official_comparison", None),
    "claude-opus-4-8": ("moderate", "anthropic_official_comparison", None),
    "claude-sonnet-5": ("fast", "anthropic_official_comparison", None),
    "claude-haiku-4-5": ("fastest", "anthropic_official_comparison", None),
    "gemini-3.1-pro-preview": ("quality_first", "google_official_positioning", None),
    "gemini-3.6-flash": ("balanced_fast", "google_official_positioning", None),
    "gemini-3.5-flash-lite": ("fastest", "google_official_positioning", None),
    "openai/gpt-oss-120b": ("very_fast", "groq_official_tokens_per_second", 500),
    "openai/gpt-oss-20b": ("very_fast", "groq_official_tokens_per_second", 1000),
    "qwen/qwen3.6-27b": ("very_fast", "groq_official_tokens_per_second", 500),
    "gemini-3.6-flash-medium": ("fast", "aiteam_durable_review_v4", None),
    "gemini-3.5-flash-high": ("moderate", "aiteam_durable_review_v4", None),
}


def annotate_model_tier(
    profile_id: str,
    profile: dict[str, Any],
    option: dict[str, Any],
) -> dict[str, Any]:
    """Añade provenance de capacidad, economía y velocidad a una opción."""
    annotated = dict(option)
    tier = str(option.get("tier") or "").strip().lower()
    model = str(option.get("value") or "").strip()
    channel = str(profile.get("channel") or "").strip().lower()
    provider = str(profile.get("provider") or "").strip().lower()
    config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
    free_tier = bool(config.get("free_tier"))

    speed_class, speed_source, tokens_per_second = _SPEED_EVIDENCE.get(
        model,
        ("unknown", "requires_channel_specific_measurement", None),
    )
    economy = _economy_evidence(
        profile_id=profile_id,
        channel=channel,
        provider=provider,
        model=model,
        free_tier=free_tier,
    )
    annotated.update(
        {
            "tier_policy_version": TIER_POLICY_VERSION,
            "capability_band": tier,
            "capability_basis": "declared_caps_roles_and_local_calibration",
            "economy": economy,
            "speed_class": speed_class,
            "speed_source": speed_source,
            "observed_tokens_per_second": tokens_per_second,
        }
    )
    return annotated


def audit_model_tier_matrix(
    profiles: list[dict[str, Any]],
    options_by_profile: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []
    profile_by_id = {str(profile.get("id") or ""): profile for profile in profiles}
    for profile_id, options in options_by_profile.items():
        profile = profile_by_id.get(profile_id, {"id": profile_id})
        for option in options:
            row = annotate_model_tier(profile_id, profile, option)
            model = str(row.get("value") or "")
            if row.get("tier") not in VALID_TIERS:
                failures.append({"profile_id": profile_id, "model": model, "reason": "tier_invalid"})
            if not row.get("caps") or not isinstance(row.get("best_for"), list):
                failures.append({"profile_id": profile_id, "model": model, "reason": "capability_evidence_missing"})
            economy = row.get("economy") if isinstance(row.get("economy"), dict) else {}
            if not economy.get("cost_class") or not economy.get("measurement_basis"):
                failures.append({"profile_id": profile_id, "model": model, "reason": "economy_evidence_missing"})
            if not row.get("speed_class") or not row.get("speed_source"):
                failures.append({"profile_id": profile_id, "model": model, "reason": "speed_evidence_missing"})
            rows.append(
                {
                    "profile_id": profile_id,
                    "model": model,
                    "tier": row.get("tier"),
                    "capability_band": row.get("capability_band"),
                    "economy": economy,
                    "speed_class": row.get("speed_class"),
                    "speed_source": row.get("speed_source"),
                }
            )
    return {
        "schema_version": 1,
        "policy_version": TIER_POLICY_VERSION,
        "models_audited": len(rows),
        "rows": rows,
        "failures": failures,
        "ok": not failures,
    }


def _economy_evidence(
    *, profile_id: str, channel: str, provider: str, model: str, free_tier: bool
) -> dict[str, Any]:
    if channel == "api" and free_tier:
        return {
            "cost_class": "quota_limited_free_api",
            "measurement_basis": "provider_usage_and_rate_limit_headers",
            "input_cents_per_mtok": 0,
            "output_cents_per_mtok": 0,
            "quota_unlimited": False,
        }
    if channel == "api":
        normalized_provider = {
            "google": "google",
            "anthropic": "anthropic",
            "openai": "openai",
        }.get(provider, provider)
        input_price, output_price = price_per_mtok(normalized_provider, model)
        return {
            "cost_class": "metered_api",
            "measurement_basis": "official_price_per_token",
            "input_cents_per_mtok": input_price,
            "output_cents_per_mtok": output_price,
            "quota_unlimited": False,
        }
    if channel == "subscription":
        token_visible = profile_id == "codex_subscription"
        return {
            "cost_class": "flat_subscription_quota_limited",
            "measurement_basis": (
                "tokens_runs_duration_and_rate_limits"
                if token_visible
                else "runs_duration_and_rate_limits_tokens_unavailable"
            ),
            "input_cents_per_mtok": 0,
            "output_cents_per_mtok": 0,
            "quota_unlimited": False,
        }
    if channel == "free_gateway":
        return {
            "cost_class": "temporary_free_gateway",
            "measurement_basis": "provider_usage_when_available_and_runtime_limits",
            "input_cents_per_mtok": 0,
            "output_cents_per_mtok": 0,
            "quota_unlimited": False,
        }
    if channel == "local":
        return {
            "cost_class": "zero_external_cost_local_compute",
            "measurement_basis": (
                "zero_external_tokens_and_quota_plus_installed_model_health_"
                "latency_and_host_resources"
            ),
            "input_cents_per_mtok": 0,
            "output_cents_per_mtok": 0,
            "quota_unlimited": True,
            "external_token_consumption": 0,
            "external_quota_pressure": 0,
            "host_resource_cost_separate": True,
        }
    return {
        "cost_class": "unknown",
        "measurement_basis": "explicit_measurement_required",
        "input_cents_per_mtok": None,
        "output_cents_per_mtok": None,
        "quota_unlimited": False,
    }
