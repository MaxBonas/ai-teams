from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


LEGACY_UNSYNTHESIZED_CHAR_THRESHOLD = 8_000


@dataclass(frozen=True)
class ContextBudgetDecision:
    should_compact: bool
    policy: str
    estimated_input_tokens: int
    comfortable_input_tokens: int | None
    context_window_tokens: int | None
    reserved_output_tokens: int
    reserved_tool_tokens: int
    unsynthesized_chars: int
    base_payload_chars: int
    chars_per_token: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_context_budget(
    *,
    unsynthesized_chars: int,
    base_payload_chars: int,
    adapter_config: dict[str, Any] | None,
) -> ContextBudgetDecision:
    """Decide compaction from declared model capacity, with a safe legacy fallback."""
    config = adapter_config or {}
    chars_per_token = _bounded_float(config.get("chars_per_token"), default=4.0, low=1.0, high=8.0)
    estimated = math.ceil((max(0, base_payload_chars) + max(0, unsynthesized_chars)) / chars_per_token)
    window = _positive_int(config.get("context_window_tokens"))
    if window is None:
        return ContextBudgetDecision(
            should_compact=unsynthesized_chars >= LEGACY_UNSYNTHESIZED_CHAR_THRESHOLD,
            policy="legacy_char_threshold",
            estimated_input_tokens=estimated,
            comfortable_input_tokens=None,
            context_window_tokens=None,
            reserved_output_tokens=0,
            reserved_tool_tokens=0,
            unsynthesized_chars=max(0, unsynthesized_chars),
            base_payload_chars=max(0, base_payload_chars),
            chars_per_token=chars_per_token,
        )

    comfortable_ratio = _bounded_float(
        config.get("comfortable_context_ratio"), default=0.70, low=0.50, high=0.90
    )
    reserved_output = _positive_int(config.get("reserved_output_tokens")) or min(8_192, window // 5)
    reserved_tools = _positive_int(config.get("reserved_tool_tokens")) or max(1_024, window // 10)
    comfortable_input = max(1, math.floor(window * comfortable_ratio) - reserved_output - reserved_tools)
    # Compaction must have enough reclaimable thread material to justify an LLM run.
    should_compact = (
        unsynthesized_chars >= LEGACY_UNSYNTHESIZED_CHAR_THRESHOLD
        and estimated >= comfortable_input
    )
    return ContextBudgetDecision(
        should_compact=should_compact,
        policy="model_comfort_budget",
        estimated_input_tokens=estimated,
        comfortable_input_tokens=comfortable_input,
        context_window_tokens=window,
        reserved_output_tokens=reserved_output,
        reserved_tool_tokens=reserved_tools,
        unsynthesized_chars=max(0, unsynthesized_chars),
        base_payload_chars=max(0, base_payload_chars),
        chars_per_token=chars_per_token,
    )


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _bounded_float(value: Any, *, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return min(high, max(low, parsed))
