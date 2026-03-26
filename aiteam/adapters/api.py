from __future__ import annotations

import os
import time
import json
import urllib.error
import urllib.request

from aiteam.adapters.base import ModelAdapter, messages_to_prompt, normalize_messages
from aiteam.types import AdapterResponse, ChannelType


class ApiAdapter(ModelAdapter):
    """Adapter base para providers via API."""

    def __init__(
        self,
        name: str,
        provider: str,
        model: str,
        capabilities: set[str] | None = None,
        cost_tier: int = 2,
        require_key: bool = False,
        role_targets: set[str] | None = None,
        routing_priority: int = 100,
        requires_approval: bool = False,
    ) -> None:
        super().__init__(
            name=name,
            provider=provider,
            model=model,
            channel=ChannelType.API,
            capabilities=capabilities,
            cost_tier=cost_tier,
            role_targets=role_targets,
            routing_priority=routing_priority,
            requires_approval=requires_approval,
        )
        self.require_key = require_key

    def available(self) -> bool:
        degraded_key = f"AITEAM_PROVIDER_{self.provider.upper()}_DEGRADED"
        degraded = os.getenv(degraded_key, "0").strip().lower()
        if degraded in {"1", "true", "yes", "on"}:
            return False

        enforce_keys = os.getenv("AITEAM_REQUIRE_API_KEYS", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not self.require_key and not enforce_keys:
            return True
        key_name = f"{self.provider.upper()}_API_KEY"
        return bool(os.getenv(key_name))

    def invoke(
        self, prompt: str, messages: list[dict[str, str]] | None = None
    ) -> AdapterResponse:
        start = time.time()
        normalized_messages = normalize_messages(messages, prompt)
        prompt_text = messages_to_prompt(messages, prompt)
        if self._live_api_enabled():
            live = self._invoke_live(prompt=prompt_text, messages=normalized_messages)
            live.latency_ms = max(live.latency_ms, int((time.time() - start) * 1000))
            return live

        input_tokens = max(1, len(prompt_text) // 4)
        first_line = (
            prompt_text.splitlines()[0][:80] if prompt_text.strip() else "tarea"
        )
        content = (
            f"[SIMULADO | {self.provider}:{self.model}:api] "
            f"Respuesta mock para: {first_line!r}. "
            f"Para llamadas reales, configura AITEAM_ENABLE_LIVE_API=1 "
            f"y {self.provider.upper()}_API_KEY en .env."
        )
        return AdapterResponse(
            success=True,
            content=content,
            latency_ms=int((time.time() - start) * 1000),
            input_tokens=input_tokens,
            output_tokens=max(1, len(content) // 4),
            error=None,
        )

    @staticmethod
    def _live_api_enabled() -> bool:
        raw = os.getenv("AITEAM_ENABLE_LIVE_API", "0").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _invoke_live(
        self, prompt: str, messages: list[dict[str, str]] | None = None
    ) -> AdapterResponse:
        provider = self.provider.strip().lower()
        if provider == "openai":
            return self._invoke_openai_compatible(
                url="https://api.openai.com/v1/chat/completions",
                api_key_env="OPENAI_API_KEY",
                prompt=prompt,
                messages=messages,
            )
        if provider == "groq":
            return self._invoke_openai_compatible(
                url="https://api.groq.com/openai/v1/chat/completions",
                api_key_env="GROQ_API_KEY",
                prompt=prompt,
                messages=messages,
            )
        if provider == "anthropic":
            return self._invoke_anthropic(prompt=prompt, messages=messages)
        return AdapterResponse(
            success=False,
            content="",
            latency_ms=0,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=0,
            error=f"live_api_not_supported_for_provider:{provider}",
        )

    def _invoke_anthropic(
        self, *, prompt: str, messages: list[dict[str, str]] | None = None
    ) -> AdapterResponse:
        """Invoca Anthropic Messages API."""
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return AdapterResponse(
                success=False,
                content="",
                latency_ms=0,
                input_tokens=max(1, len(prompt) // 4),
                output_tokens=0,
                error="missing_api_key:ANTHROPIC_API_KEY",
            )

        body = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": normalize_messages(messages, prompt),
        }
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers=headers,
            method="POST",
        )
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return AdapterResponse(
                success=False,
                content="",
                error=f"http_error:{exc.code}",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=max(1, len(prompt) // 4),
                output_tokens=0,
            )
        except urllib.error.URLError as exc:
            return AdapterResponse(
                success=False,
                content="",
                error=f"connection_error:{exc.reason}",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=max(1, len(prompt) // 4),
                output_tokens=0,
            )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return AdapterResponse(
                success=False,
                content="",
                error="invalid_json_response",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=max(1, len(prompt) // 4),
                output_tokens=0,
            )

        content_blocks = parsed.get("content", [])
        parts = []
        if isinstance(content_blocks, list):
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
        content = "\n".join(parts)

        usage = parsed.get("usage", {})
        usage_dict = usage if isinstance(usage, dict) else {}
        input_tokens = int(usage_dict.get("input_tokens", max(1, len(prompt) // 4)))
        output_tokens = int(
            usage_dict.get("output_tokens", max(1, len(content) // 4 if content else 1))
        )

        if not content:
            return AdapterResponse(
                success=False,
                content="",
                error="empty_response_content",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return AdapterResponse(
            success=True,
            content=content,
            error=None,
            latency_ms=int((time.time() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _invoke_openai_compatible(
        self,
        *,
        url: str,
        api_key_env: str,
        prompt: str,
        messages: list[dict[str, str]] | None = None,
    ) -> AdapterResponse:
        api_key = os.getenv(api_key_env, "").strip()
        if not api_key:
            return AdapterResponse(
                success=False,
                content="",
                latency_ms=0,
                input_tokens=max(1, len(prompt) // 4),
                output_tokens=0,
                error=f"missing_api_key:{api_key_env}",
            )

        body = {
            "model": self.model,
            "messages": normalize_messages(messages, prompt),
            "temperature": 0.2,
        }
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        request = urllib.request.Request(
            url, data=payload, headers=headers, method="POST"
        )
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                status_code = int(getattr(response, "status", 200))
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return AdapterResponse(
                success=False,
                content="",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=max(1, len(prompt) // 4),
                output_tokens=0,
                error=f"http_error:{exc.code}",
            )
        except urllib.error.URLError as exc:
            return AdapterResponse(
                success=False,
                content="",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=max(1, len(prompt) // 4),
                output_tokens=0,
                error=f"connection_error:{exc.reason}",
            )

        if status_code < 200 or status_code >= 300:
            return AdapterResponse(
                success=False,
                content="",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=max(1, len(prompt) // 4),
                output_tokens=0,
                error=f"http_status:{status_code}",
            )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return AdapterResponse(
                success=False,
                content="",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=max(1, len(prompt) // 4),
                output_tokens=0,
                error="invalid_json_response",
            )

        content = ""
        choices = parsed.get("choices", []) if isinstance(parsed, dict) else []
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            message = first.get("message", {}) if isinstance(first, dict) else {}
            if isinstance(message, dict):
                content = str(message.get("content", ""))

        usage = parsed.get("usage", {}) if isinstance(parsed, dict) else {}
        usage_dict = usage if isinstance(usage, dict) else {}
        input_tokens = int(
            usage_dict.get("prompt_tokens", max(1, len(prompt) // 4))
            or max(1, len(prompt) // 4)
        )
        output_tokens = int(
            usage_dict.get(
                "completion_tokens", max(1, len(content) // 4 if content else 1)
            )
            or 1
        )

        if not content:
            return AdapterResponse(
                success=False,
                content="",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                error="empty_response_content",
            )

        return AdapterResponse(
            success=True,
            content=content,
            latency_ms=int((time.time() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error=None,
        )
