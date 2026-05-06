from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from aiteam.adapters.registry import AdapterDescriptor, ExecutionResult, StaticAdapterRuntime
from aiteam.adapters.work_contract import OPENAI_SUBMIT_WORK_SCHEMA, build_execution_contract, ops_to_actions, parse_submit_work


class OpenAIResponsesRuntime:
    """OpenAI Responses API runtime with structured submit_work output."""

    def __init__(self, descriptor: AdapterDescriptor, *, model: str = "gpt-4.1", timeout: float = 120.0) -> None:
        self.descriptor = descriptor
        self._model = model
        self._timeout = timeout

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return StaticAdapterRuntime(self.descriptor).build_env(run_id=run_id, wake_context=wake_context)

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        api_key = env.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return ExecutionResult(status="failed", error="OPENAI_API_KEY not set", error_code="missing_api_key")
        model = env.get("AITEAM_OPENAI_MODEL") or self._model
        body = {
            "model": model,
            "input": [
                {"role": "system", "content": _system_prompt(env)},
                {"role": "user", "content": _user_prompt(env, run)},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "submit_work",
                    "schema": OPENAI_SUBMIT_WORK_SCHEMA,
                    "strict": True,
                }
            },
        }
        try:
            data = _post_json(
                "https://api.openai.com/v1/responses",
                body,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=self._timeout,
            )
        except Exception as exc:
            return ExecutionResult(status="failed", error=str(exc), error_code="api_error")

        raw_text = _openai_output_text(data)
        try:
            work = parse_submit_work(raw_text)
        except ValueError as exc:
            return ExecutionResult(status="failed", output=raw_text[:2048] or None, error=str(exc), error_code="tool_parse_error")
        ops = work.get("ops") if isinstance(work.get("ops"), list) else []
        status = str(work.get("status") or "completed")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
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


def _openai_output_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    parts: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "\n".join(parts)
