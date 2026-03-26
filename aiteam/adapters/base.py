from __future__ import annotations

from abc import ABC, abstractmethod

from aiteam.types import AdapterResponse, ChannelType


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
    def invoke(self, prompt: str) -> AdapterResponse:
        raise NotImplementedError
