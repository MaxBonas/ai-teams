from __future__ import annotations

from aiteam.model_compatibility import compatibility_decision
from aiteam.user_config import MODEL_OPTIONS_BY_PROFILE, ROLE_CAPABILITY_PROFILES


def _model(profile_id: str, value: str) -> dict:
    return {
        **next(
            item for item in MODEL_OPTIONS_BY_PROFILE[profile_id]
            if item["value"] == value
        ),
        "available": True,
    }


def _decision(profile: dict, model: dict, role: str, **kwargs) -> dict:
    return compatibility_decision(
        profile=profile,
        model=model,
        role=role,
        role_profile=ROLE_CAPABILITY_PROFILES.get(role, {}),
        **kwargs,
    )


def test_paid_api_engineer_is_not_blocked_for_being_api() -> None:
    profile = {"id": "openai_api", "adapter_type": "openai_api", "channel": "api"}
    decision = _decision(
        profile, _model("openai_api", "gpt-5.6-terra"), "engineer",
        run_profile="full_team", data_class="confidential",
    )
    assert decision["allowed"] is True
    assert decision["effective"]["workspace_mode"] == "write"


def test_budget_model_cannot_become_lead_and_returns_same_profile_alternative() -> None:
    profile = {"id": "openai_api", "adapter_type": "openai_api", "channel": "api"}
    candidates = [
        {**item, "available": True} for item in MODEL_OPTIONS_BY_PROFILE["openai_api"]
    ]
    decision = compatibility_decision(
        profile=profile,
        model=_model("openai_api", "gpt-5.6-luna"),
        role="lead",
        run_profile="full_team",
        data_class="internal",
        role_profile=ROLE_CAPABILITY_PROFILES["lead"],
        candidate_models=candidates,
    )
    assert decision["code"] == "model_tier_insufficient"
    assert decision["alternatives"] == [{"value": "gpt-5.6-sol", "label": "GPT-5.6 Sol"}]


def test_zen_lead_is_read_only_and_cannot_execute_solo_lead() -> None:
    profile = {
        "id": "opencode_zen_free",
        "adapter_type": "subscription_cli",
        "provider": "opencode-zen",
        "data_policy": "non_confidential_only",
        "supported_roles": ["lead", "reviewer", "file_scout"],
        "config": {"cli_kind": "opencode", "read_only": True},
    }
    model = _model("opencode_zen_free", "opencode/nemotron-3-ultra-free")

    planning = _decision(
        profile, model, "lead", run_profile="full_team", data_class="public"
    )
    solo = _decision(
        profile, model, "lead", run_profile="solo_lead", data_class="public"
    )
    confidential = _decision(
        profile, model, "lead", run_profile="full_team", data_class="confidential"
    )

    assert planning["allowed"] is True
    assert solo["code"] == "workspace_write_required"
    assert confidential["code"] == "confidential_data_forbidden"


def test_free_model_allowlist_is_permission_not_best_for_hint() -> None:
    profile = {
        "id": "opencode_zen_free",
        "adapter_type": "subscription_cli",
        "data_policy": "non_confidential_only",
        "config": {"cli_kind": "opencode", "read_only": True},
    }
    deepseek = _model("opencode_zen_free", "opencode/deepseek-v4-flash-free")
    assert _decision(
        profile, deepseek, "lead", run_profile="full_team", data_class="public"
    )["code"] == "model_role_incompatible"
    assert _decision(
        profile, deepseek, "qa", run_profile="full_team", data_class="public"
    )["code"] == "model_role_incompatible"
    assert _decision(
        profile, deepseek, "test_designer", run_profile="full_team", data_class="public"
    )["code"] == "model_role_incompatible"


def test_api_profile_does_not_gain_external_mcp_from_provider_tools() -> None:
    profile = {
        "id": "gemini_api_free",
        "adapter_type": "gemini_api",
        "channel": "api",
        "data_policy": "provider_free_tier",
    }
    model = _model("gemini_api_free", "gemini-3.6-flash")
    decision = _decision(
        profile, model, "reviewer", data_class="public",
        required_capabilities=["external_mcp"],
    )
    assert decision["code"] == "external_mcp_unsupported"


def test_json_object_model_is_insufficient_when_schema_is_required() -> None:
    profile = {
        "id": "groq_api_free",
        "adapter_type": "openai_compatible_api",
        "channel": "api",
        "data_policy": "provider_free_tier",
        "config": {"strict_models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]},
    }
    qwen = _model("groq_api_free", "qwen/qwen3.6-27b")
    decision = _decision(
        profile, qwen, "context_curator", data_class="public",
        required_capabilities=["json_schema"],
    )
    assert decision["code"] == "structured_output_insufficient"


def test_deterministic_role_never_accepts_a_model() -> None:
    profile = {"id": "openai_api", "adapter_type": "openai_api"}
    decision = _decision(
        profile, _model("openai_api", "gpt-5.6-luna"), "test_runner",
        data_class="public",
    )
    assert decision["code"] == "deterministic_role"


def test_restricted_channel_requires_data_classification() -> None:
    profile = {
        "id": "gemini_api_free", "adapter_type": "gemini_api",
        "data_policy": "provider_free_tier",
    }
    decision = _decision(
        profile, _model("gemini_api_free", "gemini-3.6-flash"), "reviewer"
    )
    assert decision["code"] == "data_classification_required"
