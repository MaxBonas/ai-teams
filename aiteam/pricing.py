"""Single source of truth for provider/model pricing and cost estimation.

All amounts are **cents per 1M tokens** as ``(input, output)`` pairs. Update
here when providers change pricing — adapters and the hiring economics all
read from this table.

Channels without marginal per-token cost return ``(0, 0)``:
- local providers (``ollama``, ``lmstudio``) — free compute,
- subscription CLIs (codex / gemini-cli / claude) — flat plan, no per-call cost.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

_LOCAL_PROVIDERS = frozenset({"ollama", "lmstudio", "local"})

# Most-specific prefixes first — lookup is by prefix match in insertion order.
PRICE_TABLE: dict[str, dict[str, tuple[int, int]]] = {
    "openai": {
        "gpt-4.1-nano": (10, 40),
        "gpt-4.1-mini": (40, 160),
        "gpt-4.1":      (200, 800),
        "gpt-4o-mini":  (15, 60),
        "gpt-4o":       (250, 1000),
        "o4-mini":      (110, 440),
        "o3":           (200, 800),
    },
    "google": {
        "gemini-2.0-flash-lite": (8, 30),
        "gemini-2.0-flash":      (10, 40),
        "gemini-2.5-flash-lite": (10, 40),
        "gemini-2.5-flash":      (30, 250),
        "gemini-2.5-pro":        (125, 1000),
    },
    "anthropic": {
        "claude-opus-4-5":   (1500, 7500),
        "claude-sonnet-4-5": (300, 1500),
        "claude-haiku-4-5":  (80, 400),
        "claude-3-5-sonnet-20241022": (300, 1500),
        "claude-3-haiku-20240307":    (25, 125),
    },
}

# Fallback model when the exact model is unknown for a priced provider.
_DEFAULT_MODEL_KEY = {
    "openai": "gpt-4.1",
    "google": "gemini-2.5-flash",
    "anthropic": "claude-sonnet-4-5",
}

# Conservative defaults when a role has no run history yet.
DEFAULT_TYPICAL_INPUT_TOKENS = 8000
DEFAULT_TYPICAL_OUTPUT_TOKENS = 1000


def price_per_mtok(provider: str, model: str) -> tuple[int, int]:
    """Return (input, output) cents per 1M tokens for *provider*/*model*.

    Unknown providers (local, subscription CLIs, custom) price at (0, 0).
    """
    provider_key = _normalize_provider(provider)
    table = PRICE_TABLE.get(provider_key)
    if not table:
        return (0, 0)
    model_key = str(model or "").strip().lower()
    for prefix, price in table.items():
        if model_key.startswith(prefix):
            return price
    return table[_DEFAULT_MODEL_KEY[provider_key]]


def estimate_cost_cents(provider: str, model: str, input_tokens: int, output_tokens: int) -> int:
    in_price, out_price = price_per_mtok(provider, model)
    try:
        cents = (int(input_tokens) * in_price + int(output_tokens) * out_price) // 1_000_000
    except (TypeError, ValueError):
        return 0
    return max(0, cents)


def estimate_cost_from_usage(provider: str, model: str, usage: Any) -> int:
    input_tokens, output_tokens = normalize_usage(usage)
    return estimate_cost_cents(provider, model, input_tokens, output_tokens)


def normalize_usage(usage: Any) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from any provider usage shape.

    Handles OpenAI Responses (``input_tokens``/``output_tokens``), Gemini
    (``promptTokenCount``/``candidatesTokenCount``) and Anthropic SDK objects
    (``input_tokens``/``output_tokens`` attributes).
    """
    if usage is None:
        return (0, 0)
    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens", usage.get("promptTokenCount", 0))
        output_tokens = usage.get("output_tokens", usage.get("candidatesTokenCount", 0))
    else:
        input_tokens = getattr(usage, "input_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", 0)
    try:
        return (max(0, int(input_tokens or 0)), max(0, int(output_tokens or 0)))
    except (TypeError, ValueError):
        return (0, 0)


def typical_tokens_for_role(db_path: Path, role: str, *, window: int = 20) -> tuple[int, int]:
    """Average (input, output) tokens of the role's recent completed runs.

    Reads ``runs.usage_json`` joined with the agent's role. Falls back to
    conservative defaults when there is no usable history — better a stable
    approximation than invented precision on fresh projects.
    """
    role_key = str(role or "").strip().lower()
    if not role_key:
        return (DEFAULT_TYPICAL_INPUT_TOKENS, DEFAULT_TYPICAL_OUTPUT_TOKENS)
    samples: list[tuple[int, int]] = []
    try:
        with contextlib.closing(sqlite3.connect(str(db_path), timeout=20.0)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT r.usage_json
                FROM runs r
                JOIN agents a ON a.id = r.agent_id
                WHERE LOWER(a.role) = ?
                  AND r.status = 'completed'
                  AND r.usage_json IS NOT NULL
                  AND r.usage_json NOT IN ('', '{}')
                ORDER BY r.created_at DESC
                LIMIT ?
                """,
                (role_key, max(1, int(window))),
            ).fetchall()
        for row in rows:
            try:
                usage = json.loads(row["usage_json"])
            except (TypeError, ValueError):
                continue
            input_tokens, output_tokens = normalize_usage(usage)
            if input_tokens or output_tokens:
                samples.append((input_tokens, output_tokens))
    except sqlite3.Error:
        return (DEFAULT_TYPICAL_INPUT_TOKENS, DEFAULT_TYPICAL_OUTPUT_TOKENS)
    if not samples:
        return (DEFAULT_TYPICAL_INPUT_TOKENS, DEFAULT_TYPICAL_OUTPUT_TOKENS)
    total_in = sum(s[0] for s in samples)
    total_out = sum(s[1] for s in samples)
    return (total_in // len(samples), total_out // len(samples))


def _normalize_provider(provider: str) -> str:
    """Map a provider string to a PRICE_TABLE key, or "" for zero-cost.

    Strict allowlist: subscription providers like ``codex-or-gemini-cli``
    must NOT fuzzy-match into a priced provider — their marginal cost is 0.
    """
    key = str(provider or "").strip().lower()
    if key == "openai":
        return "openai"
    if key in {"google", "google-gemini"}:
        return "google"
    if key == "anthropic":
        return "anthropic"
    return ""
