from __future__ import annotations

import json
import re
from typing import Any


_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{10,}", re.IGNORECASE),
)


def _redact(text: str) -> str:
    output = str(text or "")
    for pattern in _SECRET_PATTERNS:
        output = pattern.sub(
            lambda match: (
                match.group(0).split()[0] + " ***"
                if match.group(0).lower().startswith("bearer ")
                else "sk-***"
            ),
            output,
        )
    return output


def _one_line(text: object, *, limit: int = 240) -> str:
    compact = " ".join(_redact(str(text or "")).split()).strip()
    if len(compact) <= limit:
        return compact
    if limit <= 3:
        return compact[:limit]
    return compact[: limit - 3] + "..."


def _json_error_fields(body: str) -> dict[str, str]:
    try:
        parsed: Any = json.loads(str(body or ""))
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    error = parsed.get("error", parsed)
    if isinstance(error, str):
        return {"message": error}
    if not isinstance(error, dict):
        return {}
    fields: dict[str, str] = {}
    for key in ("type", "code", "message"):
        value = error.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            fields[key] = text
    return fields


def compact_provider_error(error: str | None, *, limit: int = 160) -> str:
    text = str(error or "").strip()
    if not text:
        return ""
    text = _redact(text)
    if text.startswith("http_error:"):
        head, sep, body = text.partition(":")
        status, sep2, error_body = body.partition(":")
        if sep and sep2:
            fields = _json_error_fields(error_body)
            parts = [f"{head}:{status}"]
            for key in ("type", "code", "message"):
                value = _one_line(fields.get(key, ""), limit=120)
                if value:
                    parts.append(f"{key}={value}")
            if len(parts) == 1:
                body_text = _one_line(error_body, limit=120)
                if body_text:
                    return _one_line(
                        f"{head}:{status}:{body_text}",
                        limit=limit,
                    ).replace(" ", "_")
            return _one_line(";".join(parts), limit=limit).replace(" ", "_")
    return _one_line(text, limit=limit).replace(" ", "_")


def is_non_retryable_quota_error(error_body: str) -> bool:
    text = _one_line(error_body, limit=1000).lower()
    if not text:
        return False
    if "rate_limit" in text or "rate limit" in text or "rate_limited" in text:
        return False
    quota_markers = {
        "insufficient_quota",
        "quota_exceeded",
        "quota exceeded",
        "exceeded your current quota",
        "billing_hard_limit",
        "billing hard limit",
        "insufficient credits",
        "credit balance",
    }
    return any(marker in text for marker in quota_markers)
