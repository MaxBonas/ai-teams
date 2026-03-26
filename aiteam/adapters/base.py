from __future__ import annotations

from abc import ABC, abstractmethod

from aiteam.types import AdapterResponse, ChannelType


def normalize_messages(
    messages: list[dict[str, str]] | None, prompt: str
) -> list[dict[str, str]]:
    if messages:
        normalized: list[dict[str, str]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "user") or "user").strip() or "user"
            content = str(item.get("content", "") or "")
            if not content.strip():
                continue
            normalized.append({"role": role, "content": content})
        if normalized:
            return normalized
    return [{"role": "user", "content": prompt}]


def messages_to_prompt(messages: list[dict[str, str]] | None, prompt: str) -> str:
    normalized = normalize_messages(messages, prompt)
    parts: list[str] = []
    for item in normalized:
        role = str(item.get("role", "user") or "user").strip().upper()
        content = str(item.get("content", "") or "").strip()
        if content:
            parts.append(f"[{role}] {content}")
    return "\n\n".join(parts) if parts else prompt


class ModelAdapter(ABC):
    def __init__(
        self,
        name: str,
        provider: str,
        model: str,
        channel: ChannelType,
        capabilities: set[str] | None = None,
        cost_tier: int = 1,
        role_targets: set[str] | None = None,
        routing_priority: int = 100,
        requires_approval: bool = False,
    ) -> None:
        self.name = name
        self.provider = provider
        self.model = model
        self.channel = channel
        self.capabilities = capabilities or set()
        self.cost_tier = cost_tier
        self.role_targets = role_targets or set()
        self.routing_priority = routing_priority
        self.requires_approval = requires_approval

    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def invoke(
        self, prompt: str, messages: list[dict[str, str]] | None = None
    ) -> AdapterResponse:
        raise NotImplementedError
