from __future__ import annotations

import json

from aiteam.adapters import openai_compatible_adapter
from aiteam.adapters.openai_compatible_adapter import OpenAICompatibleApiRuntime
from aiteam.adapters.registry import AdapterDescriptor
from aiteam.adapters.http_retry import ApiHttpError


def test_free_byok_runtime_uses_exact_endpoint_model_and_zero_marginal_cost(monkeypatch):
    captured = {}
    submit = {"status": "completed", "summary": "reviewed", "ops": []}

    def fake_post(url, body, *, headers, timeout):
        captured.update(url=url, body=body, headers=headers, timeout=timeout)
        return {
            "choices": [{"message": {"content": json.dumps(submit)}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
        }

    monkeypatch.setattr(openai_compatible_adapter, "_post_json", fake_post)
    runtime = OpenAICompatibleApiRuntime(
        AdapterDescriptor(adapter_type="openai_compatible_api", channel="api")
    ).with_config({
        "provider": "groq",
        "base_url": "https://api.groq.com/openai/v1/",
        "model": "openai/gpt-oss-120b",
        "api_key_env": "GROQ_API_KEY",
        "free_tier": True,
    })

    result = runtime.execute(
        {"issue_id": "issue:1"},
        {"GROQ_API_KEY": "secret", "AITEAM_AGENT_ROLE": "reviewer"},
    )

    assert result.status == "completed"
    assert result.output == "reviewed"
    assert result.usage["total_tokens"] == 150
    assert result.actual_cost_cents == 0
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["body"]["model"] == "openai/gpt-oss-120b"
    assert captured["body"]["response_format"]["type"] == "json_schema"
    assert captured["headers"] == {"Authorization": "Bearer secret"}


def test_free_byok_runtime_classifies_429_as_quota(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("HTTP 429: rate limit reached")

    monkeypatch.setattr(openai_compatible_adapter, "_post_json", fail)
    runtime = OpenAICompatibleApiRuntime(
        AdapterDescriptor(adapter_type="openai_compatible_api", channel="api")
    )

    result = runtime.execute({}, {"GROQ_API_KEY": "secret"})

    assert result.status == "failed"
    assert result.error_code == "api_usage_limit"


def test_groq_runtime_carries_header_quota_telemetry_into_usage(monkeypatch):
    limits = {
        "source": "provider_response_headers",
        "scope": "organization",
        "dimensions": [{
            "dimension": "rpd", "unit": "requests", "window": "day",
            "limit": 1000, "remaining": 999, "reset": "23h",
        }],
    }
    monkeypatch.setattr(
        openai_compatible_adapter,
        "_post_json",
        lambda *_args, **_kwargs: {
            "choices": [{"message": {"content": json.dumps({
                "status": "completed", "summary": "ok", "ops": [],
            })}}],
            "usage": {"total_tokens": 25},
            "_aiteam_rate_limits": limits,
        },
    )
    runtime = OpenAICompatibleApiRuntime(
        AdapterDescriptor(adapter_type="openai_compatible_api", channel="api")
    )

    result = runtime.execute({}, {"GROQ_API_KEY": "secret"})

    assert result.usage == {"total_tokens": 25, "_aiteam_rate_limits": limits}


def test_groq_429_carries_observed_headers_into_failed_usage(monkeypatch):
    limits = {
        "source": "provider_response_headers",
        "scope": "organization",
        "dimensions": [{
            "dimension": "tpm", "unit": "tokens", "window": "minute",
            "limit": 8000, "remaining": 0, "reset": "8s",
        }],
    }

    def fail(*_args, **_kwargs):
        raise ApiHttpError("HTTP 429: limited", rate_limits=limits)

    monkeypatch.setattr(openai_compatible_adapter, "_post_json", fail)
    runtime = OpenAICompatibleApiRuntime(
        AdapterDescriptor(adapter_type="openai_compatible_api", channel="api")
    )

    result = runtime.execute({}, {"GROQ_API_KEY": "secret"})

    assert result.error_code == "api_usage_limit"
    assert result.usage == {"_aiteam_rate_limits": limits}


def test_compatible_model_without_strict_support_uses_json_object_mode(monkeypatch):
    captured = {}

    def fake_post(_url, body, **_kwargs):
        captured.update(body)
        return {
            "choices": [{"message": {"content": json.dumps({
                "status": "completed", "summary": "ok", "ops": [],
            })}}],
            "usage": {},
        }

    monkeypatch.setattr(openai_compatible_adapter, "_post_json", fake_post)
    runtime = OpenAICompatibleApiRuntime(
        AdapterDescriptor(adapter_type="openai_compatible_api", channel="api")
    ).with_config({"model": "qwen/qwen3.6-27b", "strict_models": ["openai/gpt-oss-120b"]})

    result = runtime.execute({}, {"GROQ_API_KEY": "secret"})

    assert result.status == "completed"
    assert captured["response_format"] == {"type": "json_object"}


def test_json_object_model_repairs_invalid_contract_once_and_counts_usage(monkeypatch):
    calls = []

    def fake_post(_url, body, **_kwargs):
        calls.append(body)
        if len(calls) == 1:
            return {
                "choices": [{"message": {"content": json.dumps({
                    "status": "completed", "summary": "ok", "ops": [], "extra": True,
                })}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        return {
            "choices": [{"message": {"content": json.dumps({
                "status": "completed", "summary": "ok", "ops": [],
            })}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }

    monkeypatch.setattr(openai_compatible_adapter, "_post_json", fake_post)
    runtime = OpenAICompatibleApiRuntime(
        AdapterDescriptor(adapter_type="openai_compatible_api", channel="api")
    ).with_config({"model": "qwen/qwen3.6-27b", "strict_models": []})

    result = runtime.execute({}, {"GROQ_API_KEY": "secret"})

    assert result.status == "completed"
    assert len(calls) == 2
    assert calls[1]["response_format"] == {"type": "json_object"}
    assert '"extra": true' in calls[1]["messages"][1]["content"]
    assert result.usage == {"prompt_tokens": 17, "completion_tokens": 8, "total_tokens": 25}


def test_json_object_model_fails_closed_after_one_invalid_repair(monkeypatch):
    calls = []

    def fake_post(_url, body, **_kwargs):
        calls.append(body)
        return {
            "choices": [{"message": {"content": json.dumps({
                "status": "completed", "summary": "ok", "ops": [], "extra": True,
            })}}],
            "usage": {},
        }

    monkeypatch.setattr(openai_compatible_adapter, "_post_json", fake_post)
    runtime = OpenAICompatibleApiRuntime(
        AdapterDescriptor(adapter_type="openai_compatible_api", channel="api")
    ).with_config({"model": "qwen/qwen3.6-27b", "strict_models": []})

    result = runtime.execute({}, {"GROQ_API_KEY": "secret"})

    assert result.status == "failed"
    assert result.error_code == "tool_parse_error"
    assert "unexpected properties extra" in result.error
    assert len(calls) == 2


def test_strict_model_never_repairs_provider_schema_violation(monkeypatch):
    calls = []

    def fake_post(_url, body, **_kwargs):
        calls.append(body)
        return {
            "choices": [{"message": {"content": json.dumps({
                "status": "maybe", "summary": "invalid", "ops": [],
            })}}],
            "usage": {},
        }

    monkeypatch.setattr(openai_compatible_adapter, "_post_json", fake_post)
    runtime = OpenAICompatibleApiRuntime(
        AdapterDescriptor(adapter_type="openai_compatible_api", channel="api")
    ).with_config({"model": "openai/gpt-oss-120b"})

    result = runtime.execute({}, {"GROQ_API_KEY": "secret"})

    assert result.status == "failed"
    assert result.error_code == "tool_parse_error"
    assert len(calls) == 1


def test_json_object_repair_cannot_invent_executable_ops(monkeypatch):
    responses = iter([
        {"status": "completed", "summary": "ok", "ops": [], "extra": True},
        {
            "status": "completed",
            "summary": "ok",
            "ops": [{"type": "delete_file", "path": "important.txt"}],
        },
    ])

    def fake_post(_url, _body, **_kwargs):
        return {
            "choices": [{"message": {"content": json.dumps(next(responses))}}],
            "usage": {},
        }

    monkeypatch.setattr(openai_compatible_adapter, "_post_json", fake_post)
    runtime = OpenAICompatibleApiRuntime(
        AdapterDescriptor(adapter_type="openai_compatible_api", channel="api")
    ).with_config({"model": "qwen/qwen3.6-27b", "strict_models": []})

    result = runtime.execute({}, {"GROQ_API_KEY": "secret"})

    assert result.status == "failed"
    assert result.error_code == "tool_parse_error"
    assert result.error == "repair rejected: executable ops changed"


def test_json_object_repair_preserves_transport_failure_class(monkeypatch):
    calls = 0

    def fake_post(_url, _body, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("HTTP 429: rate limit reached during repair")
        return {
            "choices": [{"message": {"content": json.dumps({
                "status": "completed", "summary": "ok", "ops": [], "extra": True,
            })}}],
            "usage": {"total_tokens": 12},
        }

    monkeypatch.setattr(openai_compatible_adapter, "_post_json", fake_post)
    runtime = OpenAICompatibleApiRuntime(
        AdapterDescriptor(adapter_type="openai_compatible_api", channel="api")
    ).with_config({"model": "qwen/qwen3.6-27b", "strict_models": []})

    result = runtime.execute({}, {"GROQ_API_KEY": "secret"})

    assert result.status == "failed"
    assert result.error_code == "api_usage_limit"
    assert result.usage == {"total_tokens": 12}
    assert calls == 2


def test_compatible_runtime_marks_removed_model_unavailable(monkeypatch):
    monkeypatch.setattr(
        openai_compatible_adapter,
        "_post_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("HTTP 400: model not found")),
    )
    runtime = OpenAICompatibleApiRuntime(
        AdapterDescriptor(adapter_type="openai_compatible_api", channel="api")
    )

    result = runtime.execute({}, {"GROQ_API_KEY": "secret"})

    assert result.error_code == "model_unavailable"
