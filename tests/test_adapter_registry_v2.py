from aiteam.adapters import (
    AdapterDescriptor,
    AdapterRegistry,
    ClaudeSubscriptionCliRuntime,
    GeminiApiRuntime,
    OpenAIResponsesRuntime,
    StaticAdapterRuntime,
    build_default_registry,
)


def test_default_adapter_registry_is_static_and_auditable() -> None:
    registry = build_default_registry()

    descriptors = registry.descriptors()

    types = [descriptor.adapter_type for descriptor in descriptors]
    assert "lead_builtin" in types
    assert "role_builtin" in types
    assert "manual" in types
    assert "anthropic_api" in types
    assert "anthropic_sonnet" in types
    assert "openai_api" in types
    assert "gemini_api" in types
    assert "subscription_cli" in types
    assert registry.require("lead_builtin").descriptor.channel == "local"
    assert registry.require("role_builtin").descriptor.channel == "local"
    assert registry.require("anthropic_api").descriptor.provider == "anthropic"
    assert registry.require("anthropic_sonnet").descriptor.model == "claude-sonnet-4-5"
    assert registry.require("openai_api").descriptor.channel == "api"
    assert isinstance(registry.require("openai_api"), OpenAIResponsesRuntime)
    assert isinstance(registry.require("gemini_api"), GeminiApiRuntime)
    assert registry.require("subscription_cli").descriptor.cost_tier == 1
    assert isinstance(registry.require("subscription_cli"), ClaudeSubscriptionCliRuntime)


def test_adapter_registry_rejects_duplicate_adapter_type() -> None:
    runtime = StaticAdapterRuntime(
        AdapterDescriptor(
            adapter_type="manual",
            channel="manual",
            provider="human",
            model="operator",
            cost_tier=0,
        )
    )

    try:
        AdapterRegistry([runtime, runtime])
    except ValueError as exc:
        assert "Duplicate adapter_type" in str(exc)
    else:
        raise AssertionError("duplicate adapter_type was accepted")
