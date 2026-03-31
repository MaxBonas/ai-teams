from __future__ import annotations

from collections.abc import Iterator

from aiteam.adapters.base import ModelAdapter
from aiteam.types import AdapterResponse, ChannelType


class FakeSuccessAdapter(ModelAdapter):
    """Adapter de pruebas: siempre responde con exito y contenido estable."""

    def __init__(
        self,
        name: str = "fake",
        provider: str = "fake",
        model: str = "fake-1",
        capabilities: set[str] | None = None,
        response_content: str = "[FAKE] mock response",
        channel: ChannelType = ChannelType.SUBSCRIPTION,
        **kwargs,
    ) -> None:
        super().__init__(
            name=name,
            provider=provider,
            model=model,
            channel=channel,
            capabilities=capabilities
            or {"coding", "reasoning", "review", "analysis"},
            **kwargs,
        )
        self.response_content = response_content

    def available(self) -> bool:
        return True

    def invoke(
        self,
        prompt: str,
        messages: list[dict[str, str]] | None = None,
        tools=None,
    ) -> AdapterResponse:
        return AdapterResponse(
            success=True,
            content=self.response_content,
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )

    def invoke_stream(
        self,
        prompt: str,
        messages: list[dict[str, str]] | None = None,
        on_chunk=None,
        tools=None,
    ) -> Iterator[str]:
        if callable(on_chunk):
            on_chunk(self.response_content, "output")
        if self.response_content:
            yield self.response_content
