from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any

from aiteam.adapters.http_retry import post_json as _post_json
from aiteam.adapters.registry import AdapterDescriptor, ExecutionResult, StaticAdapterRuntime
from aiteam.adapters.work_contract import (
    OPENAI_SUBMIT_WORK_SCHEMA,
    build_execution_contract,
    ops_to_actions,
    parse_submit_work,
    validate_submit_work,
)
from aiteam.pricing import estimate_cost_from_usage


@dataclass
class OpenAICompatibleApiRuntime:
    """Governed chat-completions runtime for BYOK providers.

    Provider identity, endpoint, key environment and free/paid accounting are
    fixed by the selected adapter profile. The API model never receives direct
    filesystem or MCP authority; it can only return the neutral work contract.
    """

    descriptor: AdapterDescriptor
    model: str = "openai/gpt-oss-120b"
    base_url: str = "https://api.groq.com/openai/v1"
    api_key_env: str = "GROQ_API_KEY"
    provider: str = "groq"
    free_tier: bool = True
    strict_models: tuple[str, ...] = ("openai/gpt-oss-120b", "openai/gpt-oss-20b")
    timeout: float = 120.0

    def with_config(self, config: dict[str, Any]) -> "OpenAICompatibleApiRuntime":
        provider = str(config.get("provider") or self.provider).strip() or self.provider
        return replace(
            self,
            descriptor=replace(self.descriptor, provider=provider),
            model=str(config.get("model") or self.model).strip() or self.model,
            base_url=str(config.get("base_url") or self.base_url).strip().rstrip("/"),
            api_key_env=str(config.get("api_key_env") or self.api_key_env).strip() or self.api_key_env,
            provider=provider,
            free_tier=bool(config.get("free_tier", self.free_tier)),
            strict_models=tuple(
                str(item) for item in config.get("strict_models", self.strict_models)
                if str(item).strip()
            ),
            timeout=float(config.get("timeout_sec") or self.timeout),
        )

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return StaticAdapterRuntime(self.descriptor).build_env(run_id=run_id, wake_context=wake_context)

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        api_key = env.get(self.api_key_env) or os.environ.get(self.api_key_env, "")
        if not api_key:
            return ExecutionResult(
                status="failed",
                error=f"{self.api_key_env} not set",
                error_code="missing_api_key",
            )
        model = str(env.get("AITEAM_MODEL") or self.model)
        role = env.get("AITEAM_AGENT_ROLE", "").strip() or "agent"
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": _system_prompt(env)},
                {"role": "user", "content": _user_prompt(env, run)},
            ],
        }
        if model in self.strict_models:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "submit_work",
                    "strict": True,
                    "schema": OPENAI_SUBMIT_WORK_SCHEMA,
                },
            }
        else:
            # Some compatible providers/models only support JSON Object Mode.
            # parse_submit_work remains the authoritative contract validator.
            body["response_format"] = {"type": "json_object"}
        try:
            data = _post_json(
                f"{self.base_url}/chat/completions",
                body,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=self.timeout,
            )
        except Exception as exc:
            return ExecutionResult(
                status="failed",
                error=str(exc),
                error_code=_api_error_code(exc),
                usage=_error_rate_limit_usage(exc),
            )

        raw_text = _chat_output_text(data)
        usage = _usage_with_rate_limits(data)
        try:
            work = validate_submit_work(raw_text)
        except ValueError as exc:
            if model in self.strict_models:
                return _contract_failure(raw_text, exc, usage)
            repair_body = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Repair exactly one JSON object to match the submit_work contract. "
                            "Do not add new work, prose, Markdown or operations."
                            + build_execution_contract(role)
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Validation error: {str(exc)[:500]}\n\n"
                            f"Invalid object:\n{raw_text[:12000]}"
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
            }
            try:
                repair_data = _post_json(
                    f"{self.base_url}/chat/completions",
                    repair_body,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=self.timeout,
                )
            except Exception as repair_exc:
                return ExecutionResult(
                    status="failed",
                    output=raw_text[:2048] or None,
                    error=str(repair_exc),
                    error_code=_api_error_code(repair_exc),
                    usage=_merge_usage(usage, _error_rate_limit_usage(repair_exc)),
                )
            repair_text = _chat_output_text(repair_data)
            repair_usage = _usage_with_rate_limits(repair_data)
            usage = _merge_usage(usage, repair_usage)
            try:
                work = validate_submit_work(repair_text)
                _assert_repair_preserves_authority(raw_text, work)
            except ValueError as repair_exc:
                return _contract_failure(raw_text, repair_exc, usage)
        ops = work.get("ops") if isinstance(work.get("ops"), list) else []
        status = str(work.get("status") or "completed")
        return ExecutionResult(
            status=status if status in {"completed", "failed", "skipped"} else "completed",
            output=str(work.get("summary") or "") or None,
            usage=usage,
            actual_cost_cents=(
                0 if self.free_tier else estimate_cost_from_usage(self.provider, model, usage)
            ),
            actions=ops_to_actions([op for op in ops if isinstance(op, dict)]),
        )


def _system_prompt(env: dict[str, str]) -> str:
    skill = env.get("AITEAM_AGENT_SKILL", "").strip()
    role = env.get("AITEAM_AGENT_ROLE", "").strip() or "agent"
    return (skill or f"Eres un agente de AI Teams con rol {role}.") + build_execution_contract(role)


def _user_prompt(env: dict[str, str], run: dict[str, Any]) -> str:
    payload = env.get("AITEAM_WAKE_PAYLOAD_JSON", "").strip()
    return (
        "Responde con JSON submit_work válido.\n\n"
        f"Run: {env.get('AITEAM_RUN_ID', '')}\n"
        f"Issue: {env.get('AITEAM_TASK_ID', run.get('issue_id') or '')}\n\n"
        f"{payload or '{}'}"
    )


def _chat_output_text(data: dict[str, Any]) -> str:
    for choice in data.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def _contract_failure(
    raw_text: str, error: Exception, usage: dict[str, Any] | None
) -> ExecutionResult:
    return ExecutionResult(
        status="failed",
        output=raw_text[:2048] or None,
        error=str(error),
        error_code="tool_parse_error",
        usage=usage,
    )


def _merge_usage(
    first: dict[str, Any] | None, second: dict[str, Any] | None
) -> dict[str, Any] | None:
    if not first and not second:
        return None
    merged: dict[str, Any] = dict(first or {})
    for key, value in (second or {}).items():
        if key == "_aiteam_rate_limits" and isinstance(value, dict):
            merged[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            previous = merged.get(key)
            merged[key] = (previous if isinstance(previous, (int, float)) else 0) + value
        elif key not in merged:
            merged[key] = value
    return merged


def _usage_with_rate_limits(data: dict[str, Any]) -> dict[str, Any] | None:
    usage = dict(data.get("usage")) if isinstance(data.get("usage"), dict) else {}
    rate_limits = data.get("_aiteam_rate_limits")
    if isinstance(rate_limits, dict) and rate_limits:
        usage["_aiteam_rate_limits"] = rate_limits
    return usage or None


def _error_rate_limit_usage(error: Exception) -> dict[str, Any] | None:
    rate_limits = getattr(error, "rate_limits", None)
    return {"_aiteam_rate_limits": rate_limits} if isinstance(rate_limits, dict) and rate_limits else None


def _api_error_code(error: Exception) -> str:
    lowered = str(error).lower()
    if "http 429" in lowered or "rate limit" in lowered:
        return "api_usage_limit"
    if any(
        marker in lowered
        for marker in ("model not found", "unknown model", "model_decommissioned")
    ):
        return "model_unavailable"
    return "api_error"


def _assert_repair_preserves_authority(
    original_text: str, repaired: dict[str, Any]
) -> None:
    """A format repair may not create or mutate executable operations."""
    try:
        original = parse_submit_work(original_text)
    except ValueError as exc:
        raise ValueError("repair rejected: original authority is not recoverable") from exc
    original_ops = original.get("ops")
    original_status = original.get("status")
    if not isinstance(original_ops, list):
        raise ValueError("repair rejected: original ops are not a list")
    if original_status not in {"completed", "failed", "skipped"}:
        raise ValueError("repair rejected: original status is invalid")
    if repaired.get("ops") != original_ops:
        raise ValueError("repair rejected: executable ops changed")
    if repaired.get("status") != original_status:
        raise ValueError("repair rejected: execution status changed")
