from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class AdapterDescriptor:
    adapter_type: str
    channel: str
    provider: str = ""
    model: str = ""
    cost_tier: int = 1
    fallback_order: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class ExecutionResult:
    """Result of executing a run through an adapter."""
    status: str  # completed | failed | skipped
    output: str | None = None
    exit_code: int | None = None
    error: str | None = None
    error_code: str | None = None
    usage: dict[str, Any] | None = None
    actual_cost_cents: int = 0
    actions: dict[str, Any] | None = None


class AdapterRuntime(Protocol):
    descriptor: AdapterDescriptor

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        ...

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        ...


@dataclass
class StaticAdapterRuntime:
    """No-op adapter — acts as manual/placeholder. Real execution requires SubprocessAdapterRuntime."""
    descriptor: AdapterDescriptor

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        import os
        issue_id = str(wake_context.get("issue_id", "") or "")
        reason = str(wake_context.get("reason", "") or "")
        comment_id = str(wake_context.get("comment_id", "") or "")
        agent_role = str(wake_context.get("agent_role", "") or "")
        agent_skill = str(wake_context.get("agent_skill", "") or "")
        wake_payload_json = str(wake_context.get("wake_payload_json", "") or "")
        api_url = os.environ.get("AITEAM_API_URL", "http://localhost:8000")
        interaction_id = str(wake_context.get("interaction_id", "") or "")
        interaction_action = str(wake_context.get("interaction_action", "") or "")
        interaction_kind = str(wake_context.get("interaction_kind", "") or "")
        return {
            "AITEAM_RUN_ID": run_id,
            "AITEAM_TASK_ID": issue_id,
            "AITEAM_WAKE_REASON": reason,
            "AITEAM_WAKE_COMMENT_ID": comment_id,
            "AITEAM_AGENT_ROLE": agent_role,
            "AITEAM_AGENT_SKILL": agent_skill,
            "AITEAM_WAKE_PAYLOAD_JSON": wake_payload_json,
            "AITEAM_API_URL": api_url,
            "AITEAM_INTERACTION_ID": interaction_id,
            "AITEAM_INTERACTION_ACTION": interaction_action,
            "AITEAM_INTERACTION_KIND": interaction_kind,
        }

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="skipped",
            output=f"static adapter ({self.descriptor.adapter_type}): no automated execution",
        )


class AdapterRegistry:
    def __init__(self, runtimes: list[AdapterRuntime] | None = None) -> None:
        self._items: dict[str, AdapterRuntime] = {}
        for runtime in runtimes or []:
            self.register(runtime)

    def register(self, runtime: AdapterRuntime) -> None:
        adapter_type = runtime.descriptor.adapter_type.strip()
        if not adapter_type:
            raise ValueError("adapter_type is required")
        if adapter_type in self._items:
            raise ValueError(f"Duplicate adapter_type: {adapter_type}")
        self._items[adapter_type] = runtime

    def get(self, adapter_type: str) -> AdapterRuntime | None:
        return self._items.get(adapter_type)

    def require(self, adapter_type: str) -> AdapterRuntime:
        runtime = self.get(adapter_type)
        if runtime is None:
            raise KeyError(f"unknown adapter_type: {adapter_type}")
        return runtime

    def descriptors(self) -> list[AdapterDescriptor]:
        return [runtime.descriptor for runtime in self._items.values()]


def build_default_registry() -> AdapterRegistry:
    from aiteam.adapters.anthropic_adapter import AnthropicApiRuntime  # noqa: PLC0415
    from aiteam.adapters.gemini_adapter import GeminiApiRuntime  # noqa: PLC0415
    from aiteam.adapters.openai_adapter import OpenAIResponsesRuntime  # noqa: PLC0415
    from aiteam.adapters.openai_compatible_adapter import OpenAICompatibleApiRuntime  # noqa: PLC0415
    from aiteam.adapters.subscription_cli_adapter import ClaudeSubscriptionCliRuntime  # noqa: PLC0415
    registry = AdapterRegistry()
    registry.register(
        StaticAdapterRuntime(
            AdapterDescriptor(
                adapter_type="lead_builtin",
                channel="local",
                provider="aiteams",
                model="structured-lead-intake",
                cost_tier=0,
            )
        )
    )
    registry.register(
        StaticAdapterRuntime(
            AdapterDescriptor(
                adapter_type="role_builtin",
                channel="local",
                provider="aiteams",
                model="structured-role-runtime",
                cost_tier=0,
            )
        )
    )
    registry.register(
        StaticAdapterRuntime(
            AdapterDescriptor(
                adapter_type="manual",
                channel="manual",
                provider="human",
                model="operator",
                cost_tier=0,
            )
        )
    )
    registry.register(
        AnthropicApiRuntime(
            AdapterDescriptor(
                adapter_type="anthropic_api",
                channel="api",
                provider="anthropic",
                model="claude-opus-4-8",
                cost_tier=3,
            ),
            model="claude-opus-4-8",
        )
    )
    registry.register(
        AnthropicApiRuntime(
            AdapterDescriptor(
                adapter_type="anthropic_sonnet",
                channel="api",
                provider="anthropic",
                model="claude-sonnet-5",
                cost_tier=2,
            ),
            model="claude-sonnet-5",
        )
    )
    registry.register(
        OpenAIResponsesRuntime(
            AdapterDescriptor(
                adapter_type="openai_api",
                channel="api",
                provider="openai",
                model="gpt-5.6-terra",
                cost_tier=2,
            ),
            model="gpt-5.6-terra",
        )
    )
    registry.register(
        GeminiApiRuntime(
            AdapterDescriptor(
                adapter_type="gemini_api",
                channel="api",
                provider="google",
                model="gemini-3.5-flash",
                cost_tier=2,
            ),
            model="gemini-3.5-flash",
        )
    )
    registry.register(
        OpenAICompatibleApiRuntime(
            AdapterDescriptor(
                adapter_type="openai_compatible_api",
                channel="api",
                provider="openai-compatible",
                model="configured",
                cost_tier=0,
            )
        )
    )
    registry.register(
        ClaudeSubscriptionCliRuntime(
            AdapterDescriptor(
                adapter_type="subscription_cli",
                channel="subscription",
                provider="claude-code",
                model="configured",
                cost_tier=1,
            )
        )
    )
    return registry
