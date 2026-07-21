from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from aiteam.db.migration import SCHEMA_PATH
from aiteam.subscription_quota import (
    record_run_adapter_profile,
    subscription_quota_snapshot,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _init_db(db: Path) -> None:
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('role:lead', 'lead', 'Lead')")


def _run(
    db: Path,
    *,
    run_id: str,
    profile_id: str,
    provider: str,
    model: str,
    status: str = "completed",
    usage: dict | None = None,
    error_code: str | None = None,
    started_at: str = "2026-07-20T10:00:00+00:00",
    finished_at: str = "2026-07-20T10:02:00+00:00",
    quota_policy: dict | None = None,
    channel: str = "subscription",
) -> None:
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """
            INSERT INTO runs (
                id, agent_id, status, channel, provider, model, usage_json,
                error_code, started_at, finished_at, created_at
            ) VALUES (?, 'role:lead', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                status,
                "subscription" if channel == "free_gateway" else channel,
                provider,
                model,
                json.dumps(usage or {}),
                error_code,
                started_at,
                finished_at,
                finished_at,
            ),
        )
    record_run_adapter_profile(
        db,
        run_id=run_id,
        profile_id=profile_id,
        provider=provider,
        model=model,
        channel=channel,
        quota_policy=quota_policy,
    )


def test_codex_usage_forecast_requires_explicit_owner_limit(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db)
    policy = {"unit": "tokens", "limit": 1000, "window_hours": 168}
    _run(
        db,
        run_id="run-1",
        profile_id="codex-work",
        provider="openai-codex",
        model="gpt-5.6-terra",
        usage={"input_tokens": 60, "output_tokens": 40},
        started_at="2026-07-19T21:59:00+00:00",
        finished_at="2026-07-19T22:00:00+00:00",
        quota_policy=policy,
    )
    _run(
        db,
        run_id="run-2",
        profile_id="codex-work",
        provider="openai-codex",
        model="gpt-5.6-terra",
        usage={"total_tokens": 200},
        quota_policy=policy,
    )

    snapshot = subscription_quota_snapshot(db, profiles=[], now=NOW)[0]

    assert snapshot["profile_id"] == "codex-work"
    assert snapshot["tokens_observed"] == 300
    assert snapshot["token_usage_coverage"] == 1.0
    assert snapshot["forecast"] == {
        "status": "forecast_available",
        "source": "owner_config",
        "unit": "tokens",
        "limit": 1000,
        "consumed": 300,
        "remaining": 700,
        "utilization": 0.3,
        "estimated_runs_remaining": 4,
        "estimated_exhaustion_at": "2026-07-21T16:04:40+00:00",
    }
    assert snapshot["requires_attention"] is False


def test_antigravity_never_invents_tokens_but_can_use_owner_run_proxy(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db)
    policy = {"unit": "runs", "limit": 10, "window_hours": 168}
    _run(
        db,
        run_id="agy-1",
        profile_id="antigravity_subscription",
        provider="google-antigravity",
        model="gemini-3.5-flash-high",
        quota_policy=policy,
    )

    snapshot = subscription_quota_snapshot(db, profiles=[], now=NOW)[0]

    assert snapshot["tokens_observed"] is None
    assert snapshot["usage_observed_runs"] == 0
    assert snapshot["duration_seconds_observed"] == 120.0
    assert snapshot["state"] == "metered"
    assert snapshot["forecast"]["source"] == "owner_config"
    assert snapshot["forecast"]["unit"] == "runs"
    assert snapshot["forecast"]["remaining"] == 9


def test_free_gateway_uses_same_honest_pressure_telemetry(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db)
    _run(
        db,
        run_id="zen-1",
        profile_id="opencode_zen_free",
        provider="opencode-zen",
        model="opencode/north-mini-code-free",
        channel="free_gateway",
        quota_policy={"unit": "runs", "limit": 5, "window_hours": 24},
    )

    snapshot = subscription_quota_snapshot(db, profiles=[], now=NOW)[0]
    assert snapshot["profile_id"] == "opencode_zen_free"
    assert snapshot["runs"] == 1
    assert snapshot["forecast"]["remaining"] == 4
    assert snapshot["tokens_observed"] is None


def test_free_api_profile_keeps_api_channel_and_quota_pressure(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db)
    _run(
        db,
        run_id="groq-1",
        profile_id="groq_api_free",
        provider="groq",
        model="openai/gpt-oss-120b",
        channel="api",
        usage={"total_tokens": 250},
        quota_policy={"unit": "runs", "limit": 1000, "window_hours": 24},
    )

    snapshot = subscription_quota_snapshot(db, profiles=[], now=NOW)[0]
    assert snapshot["profile_id"] == "groq_api_free"
    assert snapshot["tokens_observed"] == 250
    assert snapshot["quota_kind"] == "api_rate_limit"
    assert snapshot["forecast"]["status"] == "capacity_unknown"
    assert snapshot["api_rate_limits"] == []
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute(
            "SELECT channel FROM run_adapter_profiles WHERE run_id='groq-1'"
        ).fetchone()[0] == "api"


def test_groq_api_uses_latest_header_limits_per_model_and_dimension(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db)
    usage = {
        "total_tokens": 250,
        "_aiteam_rate_limits": {
            "source": "provider_response_headers",
            "scope": "organization",
            "dimensions": [
                {
                    "dimension": "rpd", "unit": "requests", "window": "day",
                    "limit": 1000, "remaining": 900, "reset": "2h",
                },
                {
                    "dimension": "tpm", "unit": "tokens", "window": "minute",
                    "limit": 8000, "remaining": 6000, "reset": "7.6s",
                },
            ],
        },
    }
    _run(
        db,
        run_id="groq-observed",
        profile_id="groq_api_free",
        provider="groq",
        model="openai/gpt-oss-120b",
        channel="api",
        usage=usage,
    )

    snapshot = subscription_quota_snapshot(db, profiles=[], now=NOW)[0]

    assert snapshot["state"] == "api_metered"
    assert snapshot["quota_kind"] == "api_rate_limit"
    assert snapshot["forecast"]["status"] == "capacity_unknown"
    assert snapshot["api_rate_limits"] == [
        {
            "model": "openai/gpt-oss-120b", "dimension": "rpd",
            "unit": "requests", "window": "day", "limit": 1000,
            "remaining": 900, "utilization": 0.1, "reset": "2h",
            "scope": "organization", "source": "provider_response_headers",
            "observed_at": "2026-07-20T10:02:00+00:00",
        },
        {
            "model": "openai/gpt-oss-120b", "dimension": "tpm",
            "unit": "tokens", "window": "minute", "limit": 8000,
            "remaining": 6000, "utilization": 0.25, "reset": "7.6s",
            "scope": "organization", "source": "provider_response_headers",
            "observed_at": "2026-07-20T10:02:00+00:00",
        },
    ]


def test_observed_usage_limit_is_exhausted_until_a_later_success(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db)
    _run(
        db,
        run_id="limit-1",
        profile_id="codex_subscription",
        provider="openai-codex",
        model="gpt-5.6-sol",
        status="failed",
        error_code="subscription_cli_usage_limit",
        started_at="2026-07-20T09:00:00+00:00",
        finished_at="2026-07-20T09:00:10+00:00",
    )

    exhausted = subscription_quota_snapshot(db, profiles=[], now=NOW)[0]
    assert exhausted["state"] == "exhausted_observed"
    assert exhausted["requires_attention"] is True
    assert exhausted["forecast"]["status"] == "capacity_unknown"

    _run(
        db,
        run_id="recovered-1",
        profile_id="codex_subscription",
        provider="openai-codex",
        model="gpt-5.6-luna",
        usage={"total_tokens": 50},
        started_at="2026-07-20T11:00:00+00:00",
        finished_at="2026-07-20T11:01:00+00:00",
    )
    recovered = subscription_quota_snapshot(db, profiles=[], now=NOW)[0]
    assert recovered["state"] == "metered"
    assert recovered["requires_attention"] is False
    assert recovered["usage_limit_events"] == 1
    assert recovered["last_success_at"] > recovered["last_usage_limit_at"]


def test_invalid_or_missing_limit_never_creates_percentage_or_eta(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db)
    _run(
        db,
        run_id="run-1",
        profile_id="codex_subscription",
        provider="openai-codex",
        model="gpt-5.6-terra",
        usage={"total_tokens": 100},
        quota_policy={"unit": "tokens", "limit": 0, "window_hours": 168},
    )

    forecast = subscription_quota_snapshot(db, profiles=[], now=NOW)[0]["forecast"]
    assert forecast["status"] == "capacity_unknown"
    assert forecast["utilization"] is None
    assert forecast["estimated_exhaustion_at"] is None
