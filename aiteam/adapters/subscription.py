from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from aiteam.adapters.base import ModelAdapter, messages_to_prompt, normalize_messages
from aiteam.types import AdapterResponse, ChannelType


# ── Provider API configs ──────────────────────────────────────

_PROVIDER_CONFIGS: dict[str, dict[str, str]] = {
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "key_env": "OPENAI_API_KEY",
        "format": "openai",
    },
    "anthropic": {
        "url": "https://api.anthropic.com/v1/messages",
        "key_env": "ANTHROPIC_API_KEY",
        "format": "anthropic",
    },
    "google": {
        "url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "key_env": "GOOGLE_API_KEY",
        "format": "google",
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "key_env": "GROQ_API_KEY",
        "format": "openai",
    },
}


class SubscriptionAdapter(ModelAdapter):
    """Adapter para canales incluidos en suscripcion (Pro-first).

    Cuando AITEAM_ENABLE_LIVE_API=1 y la API key del provider existe,
    invoca la API real. Si no, devuelve respuesta mock (backward compat para tests).
    """

    def __init__(
        self,
        name: str,
        provider: str,
        model: str,
        capabilities: set[str] | None = None,
        cost_tier: int = 0,
        role_targets: set[str] | None = None,
        routing_priority: int = 100,
        requires_approval: bool = False,
    ) -> None:
        super().__init__(
            name=name,
            provider=provider,
            model=model,
            channel=ChannelType.SUBSCRIPTION,
            capabilities=capabilities,
            cost_tier=cost_tier,
            role_targets=role_targets,
            routing_priority=routing_priority,
            requires_approval=requires_approval,
        )

    def available(self) -> bool:
        enabled_key = f"AITEAM_SUBSCRIPTION_{self.provider.upper()}_ENABLED"
        limit_key = f"AITEAM_SUBSCRIPTION_{self.provider.upper()}_LIMIT_REACHED"
        degraded_key = f"AITEAM_PROVIDER_{self.provider.upper()}_DEGRADED"

        enabled = os.getenv(enabled_key, "1").strip().lower()
        if enabled in {"0", "false", "no", "off"}:
            return False

        limit_reached = os.getenv(limit_key, "0").strip().lower()
        if limit_reached in {"1", "true", "yes", "on"}:
            return False

        degraded = os.getenv(degraded_key, "0").strip().lower()
        if degraded in {"1", "true", "yes", "on"}:
            return False

        return True

    def invoke(
        self, prompt: str, messages: list[dict[str, str]] | None = None, tools=None,
    ) -> AdapterResponse:
        start = time.time()
        normalized_messages = normalize_messages(messages, prompt)
        prompt_text = messages_to_prompt(messages, prompt)
        input_tokens = max(1, len(prompt_text) // 4)
        if "FORCE_API_FALLBACK" in prompt_text:
            return AdapterResponse(
                success=False,
                content="",
                latency_ms=int((time.time() - start) * 1000),
                input_tokens=input_tokens,
                output_tokens=0,
                error="forced_api_fallback",
            )

        # If live API is enabled and key exists, make real call
        if self._live_api_enabled():
            live = self._invoke_live(prompt_text, normalized_messages, tools=tools)
            live.latency_ms = max(live.latency_ms, int((time.time() - start) * 1000))
            return live

        # Mock fallback — solo activo cuando AITEAM_ENABLE_LIVE_API=0 (tests/demo sin clave).
        # En produccion real, configurar AITEAM_ENABLE_LIVE_API=1 y la API key del provider.
        first_line = (
            prompt_text.splitlines()[0][:80] if prompt_text.strip() else "tarea"
        )
        content = (
            f"[SIMULADO | {self.provider}:{self.model}] "
            f"Respuesta mock para: {first_line!r}. "
            f"Tarea procesada correctamente en modo simulacion. "
            f"Para resultados reales, configura AITEAM_ENABLE_LIVE_API=1 "
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
        self, prompt: str, messages: list[dict[str, str]] | None = None, tools=None,
    ) -> AdapterResponse:
        """Invoca la API real del provider."""
        provider_key = self.provider.strip().lower()
        config = _PROVIDER_CONFIGS.get(provider_key)
        if config is None:
            return AdapterResponse(
                success=False,
                content="",
                latency_ms=0,
                input_tokens=max(1, len(prompt) // 4),
                output_tokens=0,
                error=f"unsupported_provider:{provider_key}",
            )

        api_key = os.getenv(config["key_env"], "").strip()
        if not api_key:
            return AdapterResponse(
                success=False,
                content="",
                latency_ms=0,
                input_tokens=max(1, len(prompt) // 4),
                output_tokens=0,
                error=f"missing_api_key:{config['key_env']}",
            )

        fmt = config["format"]
        if fmt == "anthropic":
            return self._invoke_anthropic(api_key, prompt, messages, tools=tools)
        if fmt == "google":
            return self._invoke_google(api_key, prompt, config["url"], messages)
        # openai-compatible (openai, groq)
        return self._invoke_openai_compatible(config["url"], api_key, prompt, messages, tools=tools)

    def _invoke_openai_compatible(
        self,
        url: str,
        api_key: str,
        prompt: str,
        messages: list[dict[str, str]] | None = None,
        tools=None,
    ) -> AdapterResponse:
        """Invoca API compatible con OpenAI (OpenAI, Groq, etc.)."""
        body = {
            "model": self.model,
            "messages": normalize_messages(messages, prompt),
            "temperature": 0.2,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            body["tool_choice"] = "auto"
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        if not tools:
            return self._http_request(
                url, payload, headers, prompt, self._parse_openai_response
            )
        # With tools: use inline parsing to capture tool_calls
        return self._http_request(
            url, payload, headers, prompt, self._make_openai_tool_parser()
        )

    def _make_openai_tool_parser(self):
        """Returns a parser function that captures tool_calls from OpenAI responses."""
        def _parser(parsed: dict, prompt: str, latency_ms: int) -> AdapterResponse:
            from aiteam.types import ToolCall
            content = ""
            choices = parsed.get("choices", [])
            tool_calls_out = []
            if isinstance(choices, list) and choices:
                first = choices[0] if isinstance(choices[0], dict) else {}
                message = first.get("message", {}) if isinstance(first, dict) else {}
                if isinstance(message, dict):
                    content = str(message.get("content", "") or "")
                    raw_tcs = message.get("tool_calls", []) if isinstance(message, dict) else []
                    for tc in (raw_tcs or []):
                        if not isinstance(tc, dict):
                            continue
                        fn = tc.get("function", {})
                        try:
                            args = json.loads(fn.get("arguments", "{}") or "{}")
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                        tool_calls_out.append(ToolCall(
                            id=str(tc.get("id", "")),
                            name=str(fn.get("name", "")),
                            arguments=args,
                        ))

            usage = parsed.get("usage", {})
            usage_dict = usage if isinstance(usage, dict) else {}
            input_tokens = int(usage_dict.get("prompt_tokens", max(1, len(prompt) // 4)))
            output_tokens = int(
                usage_dict.get("completion_tokens", max(1, len(content) // 4 if content else 1))
            )

            if tool_calls_out:
                return AdapterResponse(
                    success=True,
                    content=content or "",
                    error=None,
                    latency_ms=latency_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    tool_calls=tool_calls_out,
                )
            if not content:
                return AdapterResponse(
                    success=False,
                    content="",
                    error="empty_response_content",
                    latency_ms=latency_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            return AdapterResponse(
                success=True,
                content=content,
                error=None,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return _parser

    def _invoke_anthropic(
        self, api_key: str, prompt: str, messages: list[dict[str, str]] | None = None,
        tools=None,
    ) -> AdapterResponse:
        """Invoca API de Anthropic (Messages API)."""
        body = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": normalize_messages(messages, prompt),
        }
        if tools:
            body["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        if not tools:
            return self._http_request(
                "https://api.anthropic.com/v1/messages",
                payload,
                headers,
                prompt,
                self._parse_anthropic_response,
            )
        # With tools: use inline parsing to capture tool_use blocks
        return self._http_request(
            "https://api.anthropic.com/v1/messages",
            payload,
            headers,
            prompt,
            self._make_anthropic_tool_parser(),
        )

    def _make_anthropic_tool_parser(self):
        """Returns a parser function that captures tool_use blocks from Anthropic responses."""
        def _parser(parsed: dict, prompt: str, latency_ms: int) -> AdapterResponse:
            from aiteam.types import ToolCall
            content_blocks = parsed.get("content", [])
            parts = []
            tool_calls_out = []
            if isinstance(content_blocks, list):
                for block in content_blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                    elif block.get("type") == "tool_use":
                        raw_input = block.get("input", {})
                        args = raw_input if isinstance(raw_input, dict) else {}
                        tool_calls_out.append(ToolCall(
                            id=str(block.get("id", "")),
                            name=str(block.get("name", "")),
                            arguments=args,
                        ))
            content = "\n".join(parts)

            usage = parsed.get("usage", {})
            usage_dict = usage if isinstance(usage, dict) else {}
            input_tokens = int(usage_dict.get("input_tokens", max(1, len(prompt) // 4)))
            output_tokens = int(
                usage_dict.get("output_tokens", max(1, len(content) // 4 if content else 1))
            )

            if tool_calls_out:
                return AdapterResponse(
                    success=True,
                    content=content or "",
                    error=None,
                    latency_ms=latency_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    tool_calls=tool_calls_out,
                )
            if not content:
                return AdapterResponse(
                    success=False,
                    content="",
                    error="empty_response_content",
                    latency_ms=latency_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            return AdapterResponse(
                success=True,
                content=content,
                error=None,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return _parser

    def _invoke_google(
        self,
        api_key: str,
        prompt: str,
        url_template: str,
        messages: list[dict[str, str]] | None = None,
    ) -> AdapterResponse:
        """Invoca Gemini API de Google con historial conversacional nativo.

        Gemini requiere contents[{role, parts}] donde role es "user" o "model"
        (no "assistant"). Los mensajes "system" van en system_instruction separado.
        El historial debe empezar con "user" y alternar sin turnos consecutivos del
        mismo rol.
        """
        url = url_template.replace("{model}", self.model) + f"?key={api_key}"
        normalized = normalize_messages(messages, prompt)

        # Separar system instructions del historial conversacional.
        system_parts: list[str] = []
        raw_turns: list[tuple[str, str]] = []  # (gemini_role, content)
        for msg in normalized:
            role = msg.get("role", "user")
            content = str(msg.get("content", "") or "").strip()
            if not content:
                continue
            if role == "system":
                system_parts.append(content)
            elif role == "assistant":
                raw_turns.append(("model", content))
            else:
                raw_turns.append(("user", content))

        # Fusionar turnos consecutivos del mismo rol (Gemini los rechaza).
        merged: list[tuple[str, str]] = []
        for gemini_role, content in raw_turns:
            if merged and merged[-1][0] == gemini_role:
                merged[-1] = (gemini_role, merged[-1][1] + "\n\n" + content)
            else:
                merged.append((gemini_role, content))

        # El historial debe empezar con "user".
        if merged and merged[0][0] == "model":
            merged.insert(0, ("user", "(contexto previo)"))

        # Si no hay historial, usar el prompt directamente.
        contents = (
            [{"role": r, "parts": [{"text": c}]} for r, c in merged]
            if merged
            else [{"role": "user", "parts": [{"text": prompt}]}]
        )

        body: dict = {
            "contents": contents,
            "generationConfig": {"temperature": 0.2},
        }
        if system_parts:
            body["system_instruction"] = {
                "parts": [{"text": "\n\n".join(system_parts)}]
            }

        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        return self._http_request(
            url, payload, headers, prompt, self._parse_google_response
        )

    def _http_request(
        self,
        url: str,
        payload: bytes,
        headers: dict[str, str],
        prompt: str,
        parser,
    ) -> AdapterResponse:
        """HTTP POST generico con manejo de errores."""
        started = time.time()
        input_tokens = max(1, len(prompt) // 4)
        try:
            request = urllib.request.Request(
                url, data=payload, headers=headers, method="POST"
            )
            with urllib.request.urlopen(request, timeout=90) as response:
                status_code = int(getattr(response, "status", 200))
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            return AdapterResponse(
                success=False,
                content="",
                error=f"http_error:{exc.code}:{error_body}",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=0,
            )
        except urllib.error.URLError as exc:
            return AdapterResponse(
                success=False,
                content="",
                error=f"connection_error:{exc.reason}",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=0,
            )
        except Exception as exc:
            return AdapterResponse(
                success=False,
                content="",
                error=f"request_error:{exc}",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=0,
            )

        if status_code < 200 or status_code >= 300:
            return AdapterResponse(
                success=False,
                content="",
                error=f"http_status:{status_code}",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=input_tokens,
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
                input_tokens=input_tokens,
                output_tokens=0,
            )

        return parser(parsed, prompt, int((time.time() - started) * 1000))

    @staticmethod
    def _parse_openai_response(
        parsed: dict, prompt: str, latency_ms: int
    ) -> AdapterResponse:
        """Parsea respuesta OpenAI/Groq."""
        content = ""
        choices = parsed.get("choices", [])
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            message = first.get("message", {})
            if isinstance(message, dict):
                content = str(message.get("content", ""))

        usage = parsed.get("usage", {})
        usage_dict = usage if isinstance(usage, dict) else {}
        input_tokens = int(usage_dict.get("prompt_tokens", max(1, len(prompt) // 4)))
        output_tokens = int(
            usage_dict.get(
                "completion_tokens", max(1, len(content) // 4 if content else 1)
            )
        )

        if not content:
            return AdapterResponse(
                success=False,
                content="",
                error="empty_response_content",
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return AdapterResponse(
            success=True,
            content=content,
            error=None,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @staticmethod
    def _parse_anthropic_response(
        parsed: dict, prompt: str, latency_ms: int
    ) -> AdapterResponse:
        """Parsea respuesta Anthropic Messages API."""
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
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return AdapterResponse(
            success=True,
            content=content,
            error=None,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @staticmethod
    def _parse_google_response(
        parsed: dict, prompt: str, latency_ms: int
    ) -> AdapterResponse:
        """Parsea respuesta Google Gemini API."""
        content = ""
        candidates = parsed.get("candidates", [])
        if isinstance(candidates, list) and candidates:
            first = candidates[0] if isinstance(candidates[0], dict) else {}
            content_obj = first.get("content", {})
            if isinstance(content_obj, dict):
                parts = content_obj.get("parts", [])
                if isinstance(parts, list):
                    texts = [
                        str(p.get("text", "")) for p in parts if isinstance(p, dict)
                    ]
                    content = "\n".join(texts)

        usage = parsed.get("usageMetadata", {})
        usage_dict = usage if isinstance(usage, dict) else {}
        input_tokens = int(usage_dict.get("promptTokenCount", max(1, len(prompt) // 4)))
        output_tokens = int(
            usage_dict.get(
                "candidatesTokenCount", max(1, len(content) // 4 if content else 1)
            )
        )

        if not content:
            return AdapterResponse(
                success=False,
                content="",
                error="empty_response_content",
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return AdapterResponse(
            success=True,
            content=content,
            error=None,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
