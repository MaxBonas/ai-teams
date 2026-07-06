from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message

import pytest

from aiteam.adapters import http_retry


def _http_error(code: int, body: str = "", retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://api.example.test", code, "error", headers, io.BytesIO(body.encode("utf-8"))
    )


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info) -> None:
        return None


def _install(monkeypatch: pytest.MonkeyPatch, outcomes: list) -> tuple[list, list]:
    calls: list = []
    sleeps: list[float] = []

    def fake_urlopen(req, timeout):
        calls.append(req)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResponse(outcome)

    monkeypatch.setattr(http_retry.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http_retry.time, "sleep", lambda s: sleeps.append(s))
    return calls, sleeps


def test_retries_429_honoring_body_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    body_429 = '{"error": {"message": "Rate limit reached. Please try again in 3.424s."}}'
    calls, sleeps = _install(monkeypatch, [_http_error(429, body_429), {"ok": True}])

    result = http_retry.post_json("https://api.example.test", {}, headers={}, timeout=5)

    assert result == {"ok": True}
    assert len(calls) == 2
    assert sleeps == [pytest.approx(3.924)]


def test_retries_429_honoring_retry_after_header(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, sleeps = _install(monkeypatch, [_http_error(429, "slow down", retry_after="7"), {"ok": 1}])

    result = http_retry.post_json("https://api.example.test", {}, headers={}, timeout=5)

    assert result == {"ok": 1}
    assert sleeps == [pytest.approx(7.5)]


def test_non_retryable_status_raises_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, sleeps = _install(monkeypatch, [_http_error(400, "bad request")])

    with pytest.raises(RuntimeError, match="HTTP 400"):
        http_retry.post_json("https://api.example.test", {}, headers={}, timeout=5)

    assert len(calls) == 1
    assert sleeps == []


def test_persistent_429_raises_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = [_http_error(429, "limited") for _ in range(http_retry.MAX_ATTEMPTS)]
    calls, sleeps = _install(monkeypatch, outcomes)

    with pytest.raises(RuntimeError, match="HTTP 429"):
        http_retry.post_json("https://api.example.test", {}, headers={}, timeout=5)

    assert len(calls) == http_retry.MAX_ATTEMPTS
    assert len(sleeps) == http_retry.MAX_ATTEMPTS - 1


def test_timeout_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, sleeps = _install(monkeypatch, [TimeoutError("read timed out"), {"ok": True}])

    result = http_retry.post_json("https://api.example.test", {}, headers={}, timeout=5)

    assert result == {"ok": True}
    assert len(calls) == 2


def test_server_error_5xx_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, _ = _install(monkeypatch, [_http_error(503, "overloaded"), {"ok": True}])

    result = http_retry.post_json("https://api.example.test", {}, headers={}, timeout=5)

    assert result == {"ok": True}
    assert len(calls) == 2
