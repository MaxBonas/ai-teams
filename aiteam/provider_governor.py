"""Process-wide pacing and health state for API providers.

Runs execute sequentially in the heartbeat loop, so a single in-process
governor is enough to prevent consecutive runs from burning a provider's
tokens-per-minute budget and to keep 429 cooldowns visible *across* runs
(the in-run retry in ``http_retry`` only protects a single request).

Usage:
  - ``GOVERNOR.acquire(provider)`` before an API call — sleeps while the
    provider is cooling down or (if a TPM budget is configured) while the
    sliding-window token usage leaves no headroom.
  - ``GOVERNOR.record_rate_limit(provider, hint)`` on every 429 observed.
  - ``GOVERNOR.record_success(provider)`` / ``record_usage(provider, tokens)``
    after successful calls.

TPM budgets are opt-in via env: ``AITEAM_TPM_OPENAI=30000`` etc.  Without a
budget the governor is purely reactive (cooldowns learned from 429s).
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Callable

WINDOW_SECONDS = 60.0
DEFAULT_MAX_WAIT_SECONDS = 45.0
DEGRADED_AFTER_CONSECUTIVE = 3
DEFAULT_ESTIMATED_TOKENS = 8000
_TPM_HEADROOM = 0.85  # aim to stay below 85% of the configured budget


class _ProviderState:
    __slots__ = ("usage_window", "cooldown_until", "consecutive_rate_limits", "total_rate_limits", "last_rate_limit_at")

    def __init__(self) -> None:
        self.usage_window: deque[tuple[float, int]] = deque()
        self.cooldown_until = 0.0
        self.consecutive_rate_limits = 0
        self.total_rate_limits = 0
        self.last_rate_limit_at = 0.0


class ProviderGovernor:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleeper
        self._lock = threading.Lock()
        self._providers: dict[str, _ProviderState] = {}

    # ── Pacing ────────────────────────────────────────────────────────────

    def acquire(
        self,
        provider: str,
        *,
        estimated_tokens: int | None = None,
        max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
    ) -> float:
        """Block until *provider* has headroom.  Returns seconds waited."""
        key = _normalize(provider)
        if not key:
            return 0.0
        waited = 0.0
        while True:
            with self._lock:
                state = self._providers.setdefault(key, _ProviderState())
                now = self._clock()
                delay = max(
                    state.cooldown_until - now,
                    self._tpm_wait(key, state, now, estimated_tokens),
                )
            if delay <= 0:
                return waited
            remaining_budget = max_wait_seconds - waited
            if remaining_budget <= 0:
                return waited
            step = min(delay, remaining_budget)
            self._sleep(step)
            waited += step

    def _tpm_wait(self, key: str, state: _ProviderState, now: float, estimated_tokens: int | None) -> float:
        limit = _tpm_limit(key)
        if limit <= 0:
            return 0.0
        while state.usage_window and now - state.usage_window[0][0] > WINDOW_SECONDS:
            state.usage_window.popleft()
        used = sum(tokens for _, tokens in state.usage_window)
        needed = estimated_tokens if estimated_tokens is not None else DEFAULT_ESTIMATED_TOKENS
        budget = limit * _TPM_HEADROOM
        if used + needed <= budget:
            return 0.0
        # Wait until enough of the oldest window entries expire to free headroom.
        freed = 0
        for stamp, tokens in state.usage_window:
            freed += tokens
            if used - freed + needed <= budget:
                return max(0.0, stamp + WINDOW_SECONDS - now)
        return max(0.0, state.usage_window[0][0] + WINDOW_SECONDS - now) if state.usage_window else 0.0

    # ── Feedback ──────────────────────────────────────────────────────────

    def record_usage(self, provider: str, total_tokens: int) -> None:
        key = _normalize(provider)
        if not key or total_tokens <= 0:
            return
        with self._lock:
            state = self._providers.setdefault(key, _ProviderState())
            state.usage_window.append((self._clock(), int(total_tokens)))

    def record_rate_limit(self, provider: str, hint_seconds: float | None = None) -> None:
        key = _normalize(provider)
        if not key:
            return
        with self._lock:
            state = self._providers.setdefault(key, _ProviderState())
            now = self._clock()
            state.consecutive_rate_limits += 1
            state.total_rate_limits += 1
            state.last_rate_limit_at = now
            if hint_seconds is not None and hint_seconds > 0:
                delay = min(float(hint_seconds) + 1.0, 60.0)
            else:
                delay = min(2.0 ** state.consecutive_rate_limits, 60.0)
            state.cooldown_until = max(state.cooldown_until, now + delay)

    def record_success(self, provider: str) -> None:
        key = _normalize(provider)
        if not key:
            return
        with self._lock:
            state = self._providers.setdefault(key, _ProviderState())
            state.consecutive_rate_limits = 0

    # ── Introspection ─────────────────────────────────────────────────────

    def is_degraded(self, provider: str) -> bool:
        key = _normalize(provider)
        if not key:
            return False
        with self._lock:
            state = self._providers.get(key)
            if state is None:
                return False
            return state.consecutive_rate_limits >= DEGRADED_AFTER_CONSECUTIVE

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            now = self._clock()
            out: dict[str, dict[str, Any]] = {}
            for key, state in self._providers.items():
                while state.usage_window and now - state.usage_window[0][0] > WINDOW_SECONDS:
                    state.usage_window.popleft()
                out[key] = {
                    "cooldown_remaining_seconds": round(max(0.0, state.cooldown_until - now), 2),
                    "tokens_last_minute": sum(t for _, t in state.usage_window),
                    "tpm_limit": _tpm_limit(key) or None,
                    "consecutive_rate_limits": state.consecutive_rate_limits,
                    "total_rate_limits": state.total_rate_limits,
                    "degraded": state.consecutive_rate_limits >= DEGRADED_AFTER_CONSECUTIVE,
                }
            return out

    def reset(self) -> None:
        """Testing helper — drop all provider state."""
        with self._lock:
            self._providers.clear()


def _normalize(provider: str) -> str:
    return str(provider or "").strip().lower()


def _tpm_limit(provider: str) -> int:
    raw = os.environ.get(f"AITEAM_TPM_{provider.upper()}", "").strip()
    try:
        return int(raw) if raw else 0
    except ValueError:
        return 0


def provider_for_url(url: str) -> str:
    """Best-effort provider key from an API endpoint URL."""
    host = str(url or "").split("//", 1)[-1].split("/", 1)[0].lower()
    if "openai" in host:
        return "openai"
    if "googleapis" in host or "google" in host:
        return "google"
    if "anthropic" in host:
        return "anthropic"
    if "groq" in host:
        return "groq"
    return host


GOVERNOR = ProviderGovernor()
