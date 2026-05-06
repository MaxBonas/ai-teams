from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from aiteam.adapters.registry import AdapterDescriptor, ExecutionResult, StaticAdapterRuntime
from aiteam.adapters.work_contract import SUBMIT_WORK_SCHEMA, build_execution_contract, ops_to_actions, parse_submit_work


class GeminiApiRuntime:
    """Google Gemini API runtime using JSON response mode."""

    def __init__(self, descriptor: AdapterDescriptor, *, model: str = "gemini-2.5-flash", timeout: float = 120.0) -> None:
        self.descriptor = descriptor
        self._model = model
        self._timeout = timeout

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
                "responseSchema": SUBMIT_WORK_SCHEMA,
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
            work = parse_submit_work(raw_text)
        except ValueError as exc:
            return ExecutionResult(status="failed", output=raw_text[:2048] or None, error=str(exc), error_code="tool_parse_error")
        ops = work.get("ops") if isinstance(work.get("ops"), list) else []
        status = str(work.get("status") or "completed")
        usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else None
        return ExecutionResult(
            status=status if status in {"completed", "failed", "skipped"} else "completed",
            output=str(work.get("summary") or "") or None,
            usage=usage,
            actual_cost_cents=0,
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


def _post_json(url: str, body: dict[str, Any], *, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            return parsed if isinstance(parsed, dict) else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


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
