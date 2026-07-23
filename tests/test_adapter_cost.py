from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aiteam import pricing
from aiteam.adapters import gemini_adapter, openai_adapter
from aiteam.adapters.registry import AdapterDescriptor
from aiteam.db.migration import SCHEMA_PATH


# ── pricing table ─────────────────────────────────────────────────────────────

def test_price_prefers_specific_prefix() -> None:
    assert pricing.price_per_mtok("openai", "gpt-4.1-mini") == (40, 160)
    assert pricing.price_per_mtok("openai", "gpt-4.1") == (200, 800)


def test_current_model_family_prices_use_api_rates() -> None:
    assert pricing.price_per_mtok("openai", "gpt-5.6-sol") == (500, 3000)
    assert pricing.price_per_mtok("openai", "gpt-5.6-terra") == (250, 1500)
    assert pricing.price_per_mtok("openai", "gpt-5.6-luna") == (100, 600)
    assert pricing.price_per_mtok("anthropic", "claude-fable-5") == (1000, 5000)
    assert pricing.price_per_mtok("anthropic", "claude-opus-4-8") == (500, 2500)
    assert pricing.price_per_mtok("anthropic", "claude-sonnet-5") == (300, 1500)
    assert pricing.price_per_mtok("anthropic", "claude-haiku-4-5") == (100, 500)
    assert pricing.price_per_mtok("google", "gemini-3.1-pro-preview") == (200, 1200)
    assert pricing.price_per_mtok("google", "gemini-3.6-flash") == (150, 750)
    assert pricing.price_per_mtok("google", "gemini-3.5-flash-lite") == (30, 250)


def test_unknown_model_uses_provider_default() -> None:
    assert pricing.price_per_mtok("openai", "gpt-9-experimental") == (250, 1500)
    assert pricing.price_per_mtok("google", "gemini-9.9") == (150, 750)


def test_local_and_subscription_providers_are_free() -> None:
    assert pricing.price_per_mtok("ollama", "gemma4:e4b") == (0, 0)
    assert pricing.price_per_mtok("lmstudio", "gemma-3-4b-it") == (0, 0)
    assert pricing.price_per_mtok("codex-or-gemini-cli", "gpt-4.1") == (0, 0)
    assert pricing.price_per_mtok("codex-oss", "qwen2.5-coder:14b") == (0, 0)
    assert pricing.price_per_mtok("human", "operator") == (0, 0)
    assert pricing.price_per_mtok("openai-codex", "gpt-5.6-sol") == (0, 0)
    assert pricing.price_per_mtok("google-antigravity", "gemini-3.1-pro-high") == (0, 0)
    assert pricing.price_per_mtok("anthropic-claude", "claude-opus-4-8") == (0, 0)


def test_estimate_cost_cents() -> None:
    # 1M in * 200 + 0.5M out * 800 → 200 + 400 = 600 cents
    assert pricing.estimate_cost_cents("openai", "gpt-4.1", 1_000_000, 500_000) == 600
    # 2M in * 30 + 1M out * 250 → 60 + 250 = 310 cents
    assert pricing.estimate_cost_cents("google", "gemini-2.5-flash", 2_000_000, 1_000_000) == 310
    assert pricing.estimate_cost_cents("anthropic", "claude-sonnet-4-5", 1_000_000, 0) == 300


def test_long_prompt_price_tiers_are_applied() -> None:
    # Gemini Pro doubles input and raises output beyond 200K prompt tokens.
    assert pricing.estimate_cost_cents("google", "gemini-3.1-pro-preview", 300_000, 100_000) == 300
    # GPT-5.6 charges 2x input and 1.5x output beyond 272K input tokens.
    assert pricing.estimate_cost_cents("openai", "gpt-5.6-terra", 300_000, 100_000) == 375


def test_normalize_usage_shapes() -> None:
    assert pricing.normalize_usage({"input_tokens": 10, "output_tokens": 5}) == (10, 5)
    assert pricing.normalize_usage({"promptTokenCount": 7, "candidatesTokenCount": 3}) == (7, 3)

    class _SdkUsage:
        input_tokens = 11
        output_tokens = 4

    assert pricing.normalize_usage(_SdkUsage()) == (11, 4)
    assert pricing.normalize_usage(None) == (0, 0)
    assert pricing.normalize_usage({}) == (0, 0)


def test_estimate_cost_from_usage_handles_missing() -> None:
    assert pricing.estimate_cost_from_usage("openai", "gpt-4.1", None) == 0
    assert pricing.estimate_cost_from_usage("openai", "gpt-4.1", {}) == 0


# ── typical tokens ────────────────────────────────────────────────────────────

def _make_db_with_runs(db_path: Path, usages: list[dict[str, Any] | None]) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES ('a1', 'engineer', 'E', 'openai_api')"
        )
        for index, usage in enumerate(usages):
            conn.execute(
                "INSERT INTO runs (id, agent_id, status, invocation_source, usage_json) VALUES (?, 'a1', 'completed', 'test', ?)",
                (f"run:{index}", json.dumps(usage) if usage is not None else "{}"),
            )
        conn.commit()


def test_typical_tokens_averages_history(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _make_db_with_runs(db, [
        {"input_tokens": 6000, "output_tokens": 800},
        {"input_tokens": 10000, "output_tokens": 1200},
    ])

    assert pricing.typical_tokens_for_role(db, "engineer") == (8000, 1000)


def test_typical_tokens_falls_back_without_history(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _make_db_with_runs(db, [None])

    assert pricing.typical_tokens_for_role(db, "engineer") == (
        pricing.DEFAULT_TYPICAL_INPUT_TOKENS,
        pricing.DEFAULT_TYPICAL_OUTPUT_TOKENS,
    )
    assert pricing.typical_tokens_for_role(db, "reviewer") == (
        pricing.DEFAULT_TYPICAL_INPUT_TOKENS,
        pricing.DEFAULT_TYPICAL_OUTPUT_TOKENS,
    )


# ── adapter integration ───────────────────────────────────────────────────────

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


def test_gemini_free_profile_preserves_usage_but_zeroes_marginal_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    submit = {"status": "completed", "summary": "done", "ops": []}
    response = {
        "candidates": [{"content": {"parts": [{"text": json.dumps(submit)}]}}],
        "usageMetadata": {"promptTokenCount": 100_000, "candidatesTokenCount": 20_000},
    }
    monkeypatch.setattr(gemini_adapter, "_post_json", lambda *a, **k: response)
    runtime = gemini_adapter.GeminiApiRuntime(
        AdapterDescriptor(adapter_type="gemini_api", channel="api", provider="google")
    ).with_config({"model": "gemini-3.5-flash", "free_tier": True})

    result = runtime.execute({}, {"GEMINI_API_KEY": "test"})

    assert result.status == "completed"
    assert result.usage == response["usageMetadata"]
    assert result.actual_cost_cents == 0
