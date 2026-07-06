from __future__ import annotations

import pytest

from aiteam.provider_governor import (
    DEGRADED_AFTER_CONSECUTIVE,
    ProviderGovernor,
    provider_for_url,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def governor() -> tuple[ProviderGovernor, FakeClock]:
    clock = FakeClock()
    return ProviderGovernor(clock=clock, sleeper=clock.sleep), clock


def test_acquire_without_state_returns_immediately(governor) -> None:
    gov, _ = governor
    assert gov.acquire("openai") == 0.0


def test_rate_limit_cooldown_blocks_next_acquire(governor) -> None:
    gov, clock = governor
    gov.record_rate_limit("openai", 10.0)

    waited = gov.acquire("openai")

    assert waited == pytest.approx(11.0)  # hint + 1s margin
    # A second acquire after the cooldown expired is free.
    assert gov.acquire("openai") == 0.0


def test_acquire_respects_max_wait(governor) -> None:
    gov, _ = governor
    gov.record_rate_limit("openai", 120.0)  # clamped to 60s internally

    waited = gov.acquire("openai", max_wait_seconds=5.0)

    assert waited == pytest.approx(5.0)


def test_consecutive_rate_limits_mark_degraded_and_success_clears(governor) -> None:
    gov, _ = governor
    for _ in range(DEGRADED_AFTER_CONSECUTIVE):
        gov.record_rate_limit("openai")
    assert gov.is_degraded("openai") is True
    assert gov.is_degraded("google") is False

    gov.record_success("openai")
    assert gov.is_degraded("openai") is False


def test_tpm_budget_paces_between_runs(governor, monkeypatch) -> None:
    gov, clock = governor
    monkeypatch.setenv("AITEAM_TPM_OPENAI", "30000")

    # Burn 24k tokens now — 85% headroom of 30k is 25.5k, so an 8k-token
    # estimate does not fit until the window entry expires.
    gov.record_usage("openai", 24000)
    waited = gov.acquire("openai", estimated_tokens=8000, max_wait_seconds=120.0)

    assert waited == pytest.approx(60.0, abs=1.0)
    assert gov.acquire("openai", estimated_tokens=8000) == 0.0


def test_tpm_budget_no_wait_with_headroom(governor, monkeypatch) -> None:
    gov, _ = governor
    monkeypatch.setenv("AITEAM_TPM_OPENAI", "30000")
    gov.record_usage("openai", 10000)

    assert gov.acquire("openai", estimated_tokens=8000) == 0.0


def test_snapshot_reports_state(governor) -> None:
    gov, _ = governor
    gov.record_usage("openai", 5000)
    gov.record_rate_limit("openai", 8.0)

    snap = gov.snapshot()

    assert snap["openai"]["tokens_last_minute"] == 5000
    assert snap["openai"]["consecutive_rate_limits"] == 1
    assert snap["openai"]["cooldown_remaining_seconds"] == pytest.approx(9.0)
    assert snap["openai"]["degraded"] is False


def test_provider_for_url() -> None:
    assert provider_for_url("https://api.openai.com/v1/responses") == "openai"
    assert provider_for_url("https://generativelanguage.googleapis.com/v1beta/models/x:generateContent") == "google"
    assert provider_for_url("https://api.anthropic.com/v1/messages") == "anthropic"


def test_http_retry_reports_rate_limits_to_governor(monkeypatch) -> None:
    import io
    import urllib.error
    from email.message import Message

    from aiteam.adapters import http_retry

    reported: list[tuple[str, float | None]] = []
    successes: list[str] = []
    usages: list[tuple[str, int]] = []

    monkeypatch.setattr(http_retry.GOVERNOR, "record_rate_limit", lambda p, h=None: reported.append((p, h)))
    monkeypatch.setattr(http_retry.GOVERNOR, "record_success", lambda p: successes.append(p))
    monkeypatch.setattr(http_retry.GOVERNOR, "record_usage", lambda p, t: usages.append((p, t)))
    monkeypatch.setattr(http_retry.time, "sleep", lambda s: None)

    headers = Message()
    outcomes = [
        urllib.error.HTTPError("https://api.openai.com/v1/responses", 429, "err", headers, io.BytesIO(b"limited")),
        _FakeResponse({"ok": True, "usage": {"total_tokens": 1234}}),
    ]

    def fake_urlopen(req, timeout):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(http_retry.urllib.request, "urlopen", fake_urlopen)

    result = http_retry.post_json("https://api.openai.com/v1/responses", {}, headers={}, timeout=5)

    assert result["ok"] is True
    assert reported and reported[0][0] == "openai"
    assert successes == ["openai"]
    assert usages == [("openai", 1234)]


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        import json

        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info) -> None:
        return None
