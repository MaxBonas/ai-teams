from __future__ import annotations

import os
import time
import json
import urllib.error
import urllib.request
from typing import Iterator

from aiteam.adapters.base import ModelAdapter, messages_to_prompt, normalize_messages
from aiteam.adapters.http_errors import is_non_retryable_quota_error
from aiteam.adapters.openai_payload import build_openai_compatible_body
from aiteam.sim_mode import sim_mode_enabled
from aiteam.types import AdapterResponse, ChannelType, StreamChunk


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
        self,
        prompt: str,
        messages: list[dict[str, str]] | None = None,
        tools=None,
    ) -> AdapterResponse:
        start = time.time()
        normalized_messages = normalize_messages(messages, prompt)
        prompt_text = messages_to_prompt(messages, prompt)
        if self._live_api_enabled():
            live = self._invoke_live(
                prompt=prompt_text, messages=normalized_messages, tools=tools
            )
            live.latency_ms = max(live.latency_ms, int((time.time() - start) * 1000))
            return live

        return AdapterResponse(
            success=False,
            content="",
            latency_ms=int((time.time() - start) * 1000),
            input_tokens=max(1, len(prompt_text) // 4),
            output_tokens=0,
            error="live_api_disabled",
        )

    def _simulated_response(
        self,
        prompt_text: str,
        *,
        start: float,
        live_error: str = "",
    ) -> AdapterResponse:
        input_tokens = max(1, len(prompt_text) // 4)
        first_line = (
            prompt_text.splitlines()[0][:80] if prompt_text.strip() else "tarea"
        )
        prompt_lower = prompt_text[:400].lower()

        # Detect role from prompt context
        if any(w in prompt_lower for w in ("reviewer", "revisor", "review")):
            role_label = "Reviewer"
            role_output = (
                "Revisión del código entregado:\n"
                "- La estructura general es correcta\n"
                "- Revisar manejo de errores en los casos límite\n"
                "- Añadir docstrings a las funciones públicas\n"
                "- Tests unitarios insuficientes — aumentar cobertura\n"
                "Veredicto: aprobado con observaciones menores."
            )
        elif any(w in prompt_lower for w in ("qa", "quality", "test", "prueba")):
            role_label = "QA"
            role_output = (
                "Resultados de QA:\n"
                "- Suite de tests ejecutada\n"
                "- Casos de prueba cubiertos: funcionalidad base, edge cases\n"
                "- Sin regresiones detectadas\n"
                "- Cobertura estimada: aceptable para la fase actual\n"
                "Estado: PASSED"
            )
        elif any(w in prompt_lower for w in ("researcher", "investigador", "research", "scout")):
            role_label = "Researcher"
            role_output = (
                "Investigación completada:\n"
                "- Contexto del dominio analizado\n"
                "- Alternativas técnicas evaluadas\n"
                "- Recomendación: proceder con el enfoque estándar\n"
                "- Referencias relevantes identificadas"
            )
        elif any(w in prompt_lower for w in ("engineer", "ingeniero", "build", "implement", "develop", "code")):
            role_label = "Engineer"
            role_output = (
                f"Implementación planificada para: {first_line!r}\n\n"
                "Pasos:\n"
                "1. Crear la estructura base del módulo\n"
                "2. Implementar la lógica principal\n"
                "3. Añadir manejo de errores\n"
                "4. Escribir tests unitarios\n"
                "5. Actualizar documentación\n\n"
                "Archivos que se crearían/modificarían:\n"
                "- src/ (implementación principal)\n"
                "- tests/ (cobertura de tests)\n"
                "- README.md (actualización)"
            )
        elif any(w in prompt_lower for w in ("lead", "plan", "orchestrat", "coordinat")):
            role_label = "Team Lead"
            role_output = (
                f"Plan generado para: {first_line!r}\n\n"
                "Fases propuestas:\n"
                "1. [build] Engineer implementa la funcionalidad\n"
                "2. [review] Reviewer valida el código\n"
                "3. [qa] QA verifica la calidad\n\n"
                "Criterio de éxito: entrega funcional con tests pasando."
            )
        else:
            role_label = "Agente"
            role_output = (
                f"Tarea procesada: {first_line!r}\n"
                "Análisis completado. Output disponible al activar un provider real."
            )

        if sim_mode_enabled():
            content = (
                f"[SIMULADO | {self.provider}:{self.model}:api] {role_label}\n\n"
                f"{role_output}\n\n"
                f"— Modo simulación. Para output real: AITEAM_ENABLE_LIVE_API=1 + {self.provider.upper()}_API_KEY en .env"
            )
        else:
            fallback_note = ""
            if live_error.strip():
                compact_error = " ".join(str(live_error).split())[:140]
                fallback_note = f"\nFallback simulado tras fallo live: {compact_error}"
            content = (
                f"[SIMULADO | {self.provider}:{self.model}:api] {role_label}\n\n"
                f"{role_output}"
                f"{fallback_note}\n\n"
                f"— Para resultados reales: AITEAM_ENABLE_LIVE_API=1 + {self.provider.upper()}_API_KEY en .env"
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
    def _allow_simulated_degrade(error: str | None) -> bool:
        return False

    @staticmethod
    def _live_api_enabled() -> bool:
        raw = os.getenv("AITEAM_ENABLE_LIVE_API", "0").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _live_retry_attempts() -> int:
        raw = os.getenv("AITEAM_LIVE_API_RETRY_ATTEMPTS", "2").strip()
        try:
            value = int(raw)
        except ValueError:
            return 2
        return max(0, min(value, 4))

    @staticmethod
    def _is_retryable_http_status(code: int) -> bool:
        return code in {408, 409, 425, 429, 500, 502, 503, 504}

    @staticmethod
    def _retry_delay_seconds(
        attempt_index: int, retry_after: str | None = None
    ) -> float:
        if retry_after:
            try:
                parsed = float(str(retry_after).strip())
            except ValueError:
                parsed = 0.0
            if parsed > 0:
                return max(0.2, min(parsed, 4.0))
        return min(0.4 * (2**attempt_index), 4.0)

    def _request_json_with_retries(
        self,
        *,
        request: urllib.request.Request,
        prompt: str,
        timeout: int,
    ) -> tuple[int, str] | AdapterResponse:
        started = time.time()
        input_tokens = max(1, len(prompt) // 4)
        max_retries = self._live_retry_attempts()
        for attempt_index in range(max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    status_code = int(getattr(response, "status", 200))
                    raw = response.read().decode("utf-8", errors="replace")
                return status_code, raw
            except urllib.error.HTTPError as exc:
                error_body = ""
                try:
                    error_body = exc.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                non_retryable_quota = (
                    int(exc.code or 0) == 429
                    and is_non_retryable_quota_error(error_body)
                )
                if (
                    not non_retryable_quota
                    and attempt_index < max_retries
                    and self._is_retryable_http_status(int(exc.code or 0))
                ):
                    retry_after = None
                    headers_obj = getattr(exc, "headers", None)
                    if headers_obj is not None:
                        retry_after = headers_obj.get("Retry-After")
                    time.sleep(
                        self._retry_delay_seconds(
                            attempt_index,
                            retry_after=str(retry_after or "").strip() or None,
                        )
                    )
                    continue
                return AdapterResponse(
                    success=False,
                    content="",
                    error=f"http_error:{exc.code}:{error_body}",
                    latency_ms=int((time.time() - started) * 1000),
                    input_tokens=input_tokens,
                    output_tokens=0,
                )
            except urllib.error.URLError as exc:
                if attempt_index < max_retries:
                    time.sleep(self._retry_delay_seconds(attempt_index))
                    continue
                return AdapterResponse(
                    success=False,
                    content="",
                    error=f"connection_error:{exc.reason}",
                    latency_ms=int((time.time() - started) * 1000),
                    input_tokens=input_tokens,
                    output_tokens=0,
                )
            except Exception as exc:
                if attempt_index < max_retries:
                    time.sleep(self._retry_delay_seconds(attempt_index))
                    continue
                return AdapterResponse(
                    success=False,
                    content="",
                    error=f"request_error:{exc}",
                    latency_ms=int((time.time() - started) * 1000),
                    input_tokens=input_tokens,
                    output_tokens=0,
                )
        return AdapterResponse(
            success=False,
            content="",
            error="request_exhausted",
            latency_ms=int((time.time() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=0,
        )

    def invoke_stream(
        self, prompt: str, messages: list[dict[str, str]] | None = None
    ) -> Iterator[str | StreamChunk]:
        """Streaming invoke — yields text chunks as they arrive from the provider."""
        normalized = normalize_messages(messages, prompt)
        if not self._live_api_enabled():
            # En modo mock, yield el contenido mock como un solo chunk
            response = self.invoke(prompt, messages=messages)
            if response.success and response.content:
                yield response.content
            return
        provider = self.provider.strip().lower()
        if provider == "openai":
            yield from self._stream_openai_compatible(
                url="https://api.openai.com/v1/chat/completions",
                api_key_env="OPENAI_API_KEY",
                messages=normalized,
            )
        elif provider == "groq":
            yield from self._stream_openai_compatible(
                url="https://api.groq.com/openai/v1/chat/completions",
                api_key_env="GROQ_API_KEY",
                messages=normalized,
            )
        elif provider == "anthropic":
            yield from self._stream_anthropic(messages=normalized)
        else:
            response = self.invoke(prompt, messages=messages)
            if response.success and response.content:
                yield response.content

    def _stream_openai_compatible(
        self, *, url: str, api_key_env: str, messages: list[dict]
    ) -> Iterator[str | StreamChunk]:
        """Parsea SSE de la API compatible con OpenAI y hace yield de chunks de texto."""
        api_key = os.getenv(api_key_env, "").strip()
        if not api_key:
            return
        body = build_openai_compatible_body(
            model=self.model,
            messages=messages,
            stream=True,
        )
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        request = urllib.request.Request(
            url, data=payload, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        parsed = json.loads(data)
                        delta = parsed["choices"][0]["delta"].get("content") or ""
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except (urllib.error.URLError, urllib.error.HTTPError):
            return

    def _stream_anthropic(self, *, messages: list[dict]) -> Iterator[str | StreamChunk]:
        """Parsea SSE de Anthropic y preserva thinking cuando el provider lo emite."""
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return
        body = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": messages,
            "stream": True,
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
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    try:
                        parsed = json.loads(data)
                        if parsed.get("type") == "content_block_start":
                            content_block = parsed.get("content_block", {})
                            if not isinstance(content_block, dict):
                                continue
                            block_type = str(content_block.get("type") or "").strip().lower()
                            if block_type == "thinking":
                                thinking = str(content_block.get("thinking") or "").strip()
                                if thinking:
                                    yield StreamChunk(
                                        text=thinking,
                                        chunk_type="thinking",
                                    )
                        if parsed.get("type") == "content_block_delta":
                            delta = parsed.get("delta", {})
                            if not isinstance(delta, dict):
                                continue
                            delta_type = str(delta.get("type") or "").strip().lower()
                            if delta_type == "thinking_delta":
                                thinking = str(
                                    delta.get("thinking") or delta.get("text") or ""
                                )
                                if thinking:
                                    yield StreamChunk(
                                        text=thinking,
                                        chunk_type="thinking",
                                    )
                                continue
                            text = delta.get("text") or ""
                            if text:
                                yield str(text)
                    except (json.JSONDecodeError, KeyError):
                        continue
        except (urllib.error.URLError, urllib.error.HTTPError):
            return

    def _invoke_live(
        self,
        prompt: str,
        messages: list[dict[str, str]] | None = None,
        tools=None,
    ) -> AdapterResponse:
        provider = self.provider.strip().lower()
        if provider == "openai":
            return self._invoke_openai_compatible(
                url="https://api.openai.com/v1/chat/completions",
                api_key_env="OPENAI_API_KEY",
                prompt=prompt,
                messages=messages,
                tools=tools,
            )
        if provider == "groq":
            return self._invoke_openai_compatible(
                url="https://api.groq.com/openai/v1/chat/completions",
                api_key_env="GROQ_API_KEY",
                prompt=prompt,
                messages=messages,
                tools=tools,
            )
        if provider == "anthropic":
            return self._invoke_anthropic(prompt=prompt, messages=messages, tools=tools)
        return AdapterResponse(
            success=False,
            content="",
            latency_ms=0,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=0,
            error=f"live_api_not_supported_for_provider:{provider}",
        )

    def _invoke_anthropic(
        self,
        *,
        prompt: str,
        messages: list[dict[str, str]] | None = None,
        tools=None,
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
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers=headers,
            method="POST",
        )
        started = time.time()
        result = self._request_json_with_retries(
            request=request,
            prompt=prompt,
            timeout=90,
        )
        if isinstance(result, AdapterResponse):
            return result
        _, raw = result

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
                    tool_calls_out.append(
                        ToolCall(
                            id=str(block.get("id", "")),
                            name=str(block.get("name", "")),
                            arguments=args,
                        )
                    )
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
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tool_calls=tool_calls_out,
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
        tools=None,
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

        body = build_openai_compatible_body(
            model=self.model,
            messages=normalize_messages(messages, prompt),
        )
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
        request = urllib.request.Request(
            url, data=payload, headers=headers, method="POST"
        )
        started = time.time()
        result = self._request_json_with_retries(
            request=request,
            prompt=prompt,
            timeout=45,
        )
        if isinstance(result, AdapterResponse):
            return result
        status_code, raw = result

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
                content = str(message.get("content", "") or "")

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

        # Parsear tool_calls de la respuesta
        from aiteam.types import ToolCall

        tool_calls_out = []
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            message = first.get("message", {}) if isinstance(first, dict) else {}
            raw_tcs = message.get("tool_calls", []) if isinstance(message, dict) else []
            for tc in raw_tcs or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls_out.append(
                    ToolCall(
                        id=str(tc.get("id", "")),
                        name=str(fn.get("name", "")),
                        arguments=args,
                    )
                )

        if tool_calls_out:
            return AdapterResponse(
                success=True,
                content=content or "",
                latency_ms=int((time.time() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                error=None,
                tool_calls=tool_calls_out,
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
