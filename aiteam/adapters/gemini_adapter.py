from __future__ import annotations

import os
import urllib.parse
from dataclasses import replace
from typing import Any

from aiteam.adapters.http_retry import post_json as _post_json
from aiteam.adapters.registry import AdapterDescriptor, ExecutionResult, StaticAdapterRuntime
from aiteam.pricing import estimate_cost_from_usage
from aiteam.adapters.work_contract import SUBMIT_WORK_SCHEMA, build_execution_contract, ops_to_actions, validate_submit_work


def _to_gemini_schema(node: Any) -> Any:
    """Sanitiza un JSON Schema estándar para el ``responseSchema`` de Gemini.

    Gemini acepta un subconjunto de OpenAPI 3.0, no JSON Schema completo:
    rechaza ``additionalProperties`` (error real en vivo: "Unknown name
    'additionalProperties'... Cannot find field"). SUBMIT_WORK_SCHEMA se
    comparte con el adapter OpenAI (que sí soporta esa keyword), así que se
    sanea aquí en vez de tener dos schemas paralelos que pueden divergir.
    """
    if isinstance(node, dict):
        return {
            key: _to_gemini_schema(value)
            for key, value in node.items()
            if key != "additionalProperties"
        }
    if isinstance(node, list):
        return [_to_gemini_schema(item) for item in node]
    return node


class GeminiApiRuntime:
    """Google Gemini API runtime using JSON response mode."""

    def __init__(
        self,
        descriptor: AdapterDescriptor,
        *,
        model: str = "gemini-3.6-flash",
        timeout: float = 120.0,
        free_tier: bool = False,
    ) -> None:
        self.descriptor = descriptor
        self._model = model
        self._timeout = timeout
        self._free_tier = free_tier

    def with_config(self, config: dict[str, Any]) -> "GeminiApiRuntime":
        return GeminiApiRuntime(
            replace(self.descriptor, provider=str(config.get("provider") or self.descriptor.provider)),
            model=str(config.get("model") or self._model),
            timeout=float(config.get("timeout_sec") or self._timeout),
            free_tier=bool(config.get("free_tier", self._free_tier)),
        )

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return StaticAdapterRuntime(self.descriptor).build_env(run_id=run_id, wake_context=wake_context)

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        api_key = env.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return ExecutionResult(status="failed", error="GEMINI_API_KEY not set", error_code="missing_api_key")
        model = env.get("AITEAM_GEMINI_MODEL") or self._model
        url_model = urllib.parse.quote(model, safe="")
        body = {
            "systemInstruction": {"parts": [{"text": _system_prompt(env)}]},
            "contents": [{"role": "user", "parts": [{"text": _user_prompt(env, run)}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _to_gemini_schema(SUBMIT_WORK_SCHEMA),
            },
        }
        try:
            data = _post_json(
                f"https://generativelanguage.googleapis.com/v1beta/models/{url_model}:generateContent",
                body,
                headers={"x-goog-api-key": api_key},
                timeout=self._timeout,
            )
        except Exception as exc:
            return ExecutionResult(status="failed", error=str(exc), error_code="api_error")

        raw_text = _gemini_output_text(data)
        try:
            work = validate_submit_work(raw_text)
        except ValueError as exc:
            return ExecutionResult(status="failed", output=raw_text[:2048] or None, error=str(exc), error_code="tool_parse_error")
        ops = work.get("ops") if isinstance(work.get("ops"), list) else []
        status = str(work.get("status") or "completed")
        usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else None
        return ExecutionResult(
            status=status if status in {"completed", "failed", "skipped"} else "completed",
            output=str(work.get("summary") or "") or None,
            usage=usage,
            actual_cost_cents=(0 if self._free_tier else estimate_cost_from_usage("google", model, usage)),
            actions=ops_to_actions([op for op in ops if isinstance(op, dict)]),
        )


def _system_prompt(env: dict[str, str]) -> str:
    skill = env.get("AITEAM_AGENT_SKILL", "").strip()
    role = env.get("AITEAM_AGENT_ROLE", "").strip() or "agent"
    return (skill or f"Eres un agente de AI Teams con rol {role}.") + build_execution_contract()


def _user_prompt(env: dict[str, str], run: dict[str, Any]) -> str:
    payload = env.get("AITEAM_WAKE_PAYLOAD_JSON", "").strip()
    return (
        "Responde con JSON submit_work valido.\n\n"
        f"Run: {env.get('AITEAM_RUN_ID', '')}\n"
        f"Issue: {env.get('AITEAM_TASK_ID', run.get('issue_id') or '')}\n\n"
        f"{payload or '{}'}"
    )


def _gemini_output_text(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for candidate in data.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content") or {}
        if not isinstance(content, dict):
            continue
        for part in content.get("parts") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
    return "\n".join(parts)
