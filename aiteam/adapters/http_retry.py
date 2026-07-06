"""Shared JSON POST helper with retry/backoff for API adapters.

Rate limits (HTTP 429) and transient server errors (5xx) are retried in-place
so a momentary TPM limit does not fail the whole run.  The delay honors the
``Retry-After`` header or the "try again in Xs" hint that OpenAI embeds in the
error body, falling back to exponential backoff.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from aiteam.provider_governor import GOVERNOR, provider_for_url

RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}
MAX_ATTEMPTS = 4
MAX_TOTAL_SLEEP_SECONDS = 90.0
_RETRY_HINT_RE = re.compile(r"try again in ([0-9.]+)s", re.IGNORECASE)


def post_json(url: str, body: dict[str, Any], *, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    """POST *body* as JSON and return the parsed dict response.

    Retries retryable HTTP statuses and network timeouts up to MAX_ATTEMPTS,
    sleeping at most MAX_TOTAL_SLEEP_SECONDS overall.  Non-retryable errors
    raise ``RuntimeError`` with the status and response excerpt.
    """
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    provider = provider_for_url(url)
    slept = 0.0
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
                data = parsed if isinstance(parsed, dict) else {}
                GOVERNOR.record_success(provider)
                GOVERNOR.record_usage(provider, _extract_total_tokens(data))
                return data
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            error = RuntimeError(f"HTTP {exc.code}: {detail}")
            if exc.code not in RETRYABLE_STATUS:
                raise error from exc
            last_error = error
            delay = _retry_delay(exc.headers.get("Retry-After") if exc.headers else None, detail, attempt)
            if exc.code == 429:
                GOVERNOR.record_rate_limit(provider, delay)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            delay = _retry_delay(None, "", attempt)
        if attempt >= MAX_ATTEMPTS or slept + delay > MAX_TOTAL_SLEEP_SECONDS:
            break
        time.sleep(delay)
        slept += delay
    if isinstance(last_error, RuntimeError):
        raise last_error
    raise RuntimeError(str(last_error)) from last_error


def _retry_delay(retry_after_header: str | None, detail: str, attempt: int) -> float:
    if retry_after_header:
        try:
            return min(float(retry_after_header) + 0.5, 60.0)
        except ValueError:
            pass
    match = _RETRY_HINT_RE.search(detail)
    if match:
        try:
            return min(float(match.group(1)) + 0.5, 60.0)
        except ValueError:
            pass
    return min(2.0 ** attempt, 30.0)


def _extract_total_tokens(data: dict[str, Any]) -> int:
    usage = data.get("usage")
    if isinstance(usage, dict):
        try:
            total = int(usage.get("total_tokens") or 0)
        except (TypeError, ValueError):
            total = 0
        if total:
            return total
    meta = data.get("usageMetadata")
    if isinstance(meta, dict):
        try:
            return int(meta.get("totalTokenCount") or 0)
        except (TypeError, ValueError):
            return 0
    return 0
