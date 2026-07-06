from __future__ import annotations

import json
from typing import Any

import pytest

from aiteam.adapters import gemini_adapter, openai_adapter
from aiteam.adapters.registry import AdapterDescriptor


def test_openai_cost_estimate_gpt41() -> None:
    usage = {"input_tokens": 1_000_000, "output_tokens": 500_000, "total_tokens": 1_500_000}
    # 1M in * 200 + 0.5M out * 800 → 200 + 400 = 600 cents
    assert openai_adapter._estimate_cost_cents("gpt-4.1", usage) == 600


def test_openai_cost_estimate_prefers_specific_prefix() -> None:
    usage = {"input_tokens": 1_000_000, "output_tokens": 0}
    assert openai_adapter._estimate_cost_cents("gpt-4.1-mini", usage) == 40
    assert openai_adapter._estimate_cost_cents("gpt-4.1", usage) == 200


def test_openai_cost_estimate_handles_missing_usage() -> None:
    assert openai_adapter._estimate_cost_cents("gpt-4.1", None) == 0
    assert openai_adapter._estimate_cost_cents("gpt-4.1", {}) == 0


def test_gemini_cost_estimate() -> None:
    usage = {"promptTokenCount": 2_000_000, "candidatesTokenCount": 1_000_000, "totalTokenCount": 3_000_000}
    # 2M in * 30 + 1M out * 250 → 60 + 250 = 310 cents
    assert gemini_adapter._estimate_cost_cents("gemini-2.5-flash", usage) == 310


def test_gemini_cost_estimate_unknown_model_uses_default() -> None:
    usage = {"promptTokenCount": 1_000_000, "candidatesTokenCount": 0}
    assert gemini_adapter._estimate_cost_cents("gemini-9.9-experimental", usage) == 30


def test_openai_execute_reports_actual_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    submit = {"status": "completed", "summary": "done", "ops": []}
    response = {
        "output_text": json.dumps(submit),
        "usage": {"input_tokens": 100_000, "output_tokens": 50_000, "total_tokens": 150_000},
    }
    monkeypatch.setattr(openai_adapter, "_post_json", lambda *a, **k: response)

    runtime = openai_adapter.OpenAIResponsesRuntime(
        AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai"),
        model="gpt-4.1",
    )
    result = runtime.execute({"id": "run:x", "issue_id": "i1"}, {"OPENAI_API_KEY": "sk-test"})

    assert result.status == "completed"
    # 0.1M * 200 + 0.05M * 800 → 20 + 40 = 60 cents
    assert result.actual_cost_cents == 60


def test_gemini_execute_reports_actual_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    submit = {"status": "completed", "summary": "done", "ops": []}
    response = {
        "candidates": [{"content": {"parts": [{"text": json.dumps(submit)}]}}],
        "usageMetadata": {"promptTokenCount": 100_000, "candidatesTokenCount": 20_000},
    }
    monkeypatch.setattr(gemini_adapter, "_post_json", lambda *a, **k: response)

    runtime = gemini_adapter.GeminiApiRuntime(
        AdapterDescriptor(adapter_type="gemini_api", channel="api", provider="google"),
        model="gemini-2.5-flash",
    )
    result = runtime.execute({"id": "run:x", "issue_id": "i1"}, {"GEMINI_API_KEY": "test"})

    assert result.status == "completed"
    # 0.1M * 30 + 0.02M * 250 → 3 + 5 = 8 cents
    assert result.actual_cost_cents == 8
