from __future__ import annotations

import contextlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiteam.user_config import load_adapter_profiles


_UNITS = {"tokens", "runs", "seconds"}
_TERMINAL = {"completed", "failed", "cancelled", "lost", "skipped"}


def record_run_adapter_profile(
    db_path: Path,
    *,
    run_id: str,
    profile_id: str,
    provider: str | None,
    model: str | None,
    channel: str | None,
    quota_policy: Any = None,
) -> None:
    """Freeze the adapter profile actually selected for a run.

    Agent configuration is mutable, so it is not valid historical provenance.
    The optional quota policy is snapshotted for the same reason. Invalid or
    incomplete policies become an empty object and can never create a forecast.
    """
    clean_profile_id = str(profile_id or "").strip()
    if not clean_profile_id:
        return
    normalized_channel = "subscription" if channel == "free_gateway" else (
        channel if channel in {"subscription", "api", "local"} else None
    )
    policy = _normalize_policy(quota_policy)
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO run_adapter_profiles (
                run_id, profile_id, provider, model, channel, quota_policy_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                profile_id = excluded.profile_id,
                provider = excluded.provider,
                model = excluded.model,
                channel = excluded.channel,
                quota_policy_json = excluded.quota_policy_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                run_id,
                clean_profile_id,
                provider,
                model,
                normalized_channel,
                json.dumps(policy, ensure_ascii=False, sort_keys=True),
            ),
        )


def subscription_quota_snapshot(
    db_path: Path,
    *,
    profiles: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    observation_window_hours: int = 168,
) -> list[dict[str, Any]]:
    """Return honest per-profile subscription pressure and optional forecasts.

    Runs, duration and observed limit errors are always telemetry. Tokens are
    only summed when the adapter emitted them. A remaining percentage or ETA is
    produced only from an explicit owner policy in ``config.subscription_quota``
    (unit + limit + window_hours); vendor capacity is never inferred.
    """
    current = _utc(now or datetime.now(timezone.utc))
    configured = profiles if profiles is not None else load_adapter_profiles()
    profile_map: dict[str, dict[str, Any]] = {}
    for item in configured:
        profile_id = str(item.get("id") or "").strip()
        config = item.get("config") if isinstance(item.get("config"), dict) else {}
        if profile_id and (
            str(item.get("channel") or "") in {"subscription", "free_gateway"}
            or bool(config.get("quota_tracking"))
        ):
            profile_map[profile_id] = item
    rows = _load_rows(db_path)
    grouped: dict[str, list[dict[str, Any]]] = {profile_id: [] for profile_id in profile_map}
    for row in rows:
        profile_id = str(row.get("profile_id") or "")
        if profile_id and (not profile_map or profile_id in profile_map):
            grouped.setdefault(profile_id, []).append(row)

    snapshots: list[dict[str, Any]] = []
    for profile_id, all_rows in grouped.items():
        profile = profile_map.get(profile_id, {})
        config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
        observed_channel = str(profile.get("channel") or "").strip()
        if observed_channel == "free_gateway":
            observed_channel = "subscription"
        if not observed_channel:
            observed_channel = next(
                (str(row.get("channel") or "") for row in reversed(all_rows) if row.get("channel")),
                "subscription",
            )
        policy = (
            _normalize_policy(config.get("subscription_quota"))
            if observed_channel == "subscription"
            else {}
        )
        if not policy and observed_channel == "subscription":
            for row in reversed(all_rows):
                policy = _normalize_policy(row.get("quota_policy"))
                if policy:
                    break
        window_hours = int(policy.get("window_hours") or observation_window_hours)
        cutoff = current - timedelta(hours=max(1, window_hours))
        active_rows = [row for row in all_rows if (_row_time(row) or current) >= cutoff]
        snapshots.append(
            _profile_snapshot(
                profile_id=profile_id,
                label=str(profile.get("label") or profile_id),
                rows=active_rows,
                policy=policy,
                channel=observed_channel,
                now=current,
                window_hours=window_hours,
            )
        )
    snapshots.sort(key=lambda item: (not bool(item["requires_attention"]), item["profile_id"]))
    return snapshots


def _profile_snapshot(
    *,
    profile_id: str,
    label: str,
    rows: list[dict[str, Any]],
    policy: dict[str, Any],
    channel: str,
    now: datetime,
    window_hours: int,
) -> dict[str, Any]:
    completed = [row for row in rows if row.get("status") == "completed"]
    chargeable = [
        row for row in rows
        if row.get("status") in _TERMINAL
        and row.get("error_code") not in {"subscription_cli_usage_limit", "api_usage_limit"}
    ]
    token_values = [_tokens(row.get("usage")) for row in completed]
    token_values = [value for value in token_values if value is not None]
    duration_values = [_duration_seconds(row) for row in chargeable]
    duration_values = [value for value in duration_values if value is not None]
    limit_times = [
        _row_time(row) for row in rows
        if row.get("error_code") in {"subscription_cli_usage_limit", "api_usage_limit"}
    ]
    limit_times = [value for value in limit_times if value is not None]
    success_times = [_row_time(row) for row in completed]
    success_times = [value for value in success_times if value is not None]
    last_limit = max(limit_times) if limit_times else None
    last_success = max(success_times) if success_times else None
    observed_exhausted = bool(last_limit and (not last_success or last_limit > last_success))
    token_coverage = (len(token_values) / len(completed)) if completed else None
    api_rate_limits = _latest_api_rate_limits(rows) if channel == "api" else []

    forecast: dict[str, Any] = {
        "status": "capacity_unknown",
        "source": None,
        "unit": None,
        "limit": None,
        "consumed": None,
        "remaining": None,
        "utilization": None,
        "estimated_runs_remaining": None,
        "estimated_exhaustion_at": None,
    }
    configured_attention = False
    if policy and channel == "subscription":
        unit = str(policy["unit"])
        if unit == "tokens":
            consumed = sum(token_values)
            complete_coverage = bool(completed) and len(token_values) == len(completed)
        elif unit == "runs":
            consumed = len(chargeable)
            complete_coverage = True
        else:
            consumed = round(sum(duration_values), 3)
            complete_coverage = len(duration_values) == len(chargeable)
        limit = float(policy["limit"])
        forecast.update({
            "source": "owner_config",
            "unit": unit,
            "limit": _clean_number(limit),
            "consumed": _clean_number(consumed),
        })
        if not complete_coverage:
            forecast["status"] = "insufficient_usage_coverage"
        else:
            remaining = max(0.0, limit - float(consumed))
            average = (float(consumed) / len(chargeable)) if chargeable else 0.0
            estimated_runs = math.floor(remaining / average) if average > 0 else None
            forecast.update({
                "status": "limit_reached" if remaining <= 0 else "forecast_available",
                "remaining": _clean_number(remaining),
                "utilization": round(float(consumed) / limit, 4),
                "estimated_runs_remaining": estimated_runs,
            })
            times = sorted(value for value in (_row_time(row) for row in chargeable) if value)
            if remaining > 0 and float(consumed) > 0 and len(times) >= 2:
                span_hours = (times[-1] - times[0]).total_seconds() / 3600
                if span_hours >= 1:
                    units_per_hour = float(consumed) / span_hours
                    eta = now + timedelta(hours=remaining / units_per_hour)
                    forecast["estimated_exhaustion_at"] = eta.isoformat()
            if remaining <= 0 or (estimated_runs is not None and estimated_runs <= 1):
                configured_attention = True

    api_limit_reached = any(item.get("remaining") == 0 for item in api_rate_limits)
    if observed_exhausted:
        state = "exhausted_observed"
    elif channel == "api" and api_limit_reached:
        state = "limit_reached"
    elif channel == "api" and api_rate_limits:
        state = "api_metered"
    elif forecast["status"] == "limit_reached":
        state = "limit_reached"
    elif configured_attention:
        state = "at_risk"
    elif forecast["status"] == "forecast_available":
        state = "metered"
    elif rows and token_values:
        state = "metered"
    elif rows:
        state = "unmetered"
    else:
        state = "no_data"

    providers = sorted({str(row.get("provider") or "") for row in rows if row.get("provider")})
    models = sorted({str(row.get("model") or "") for row in rows if row.get("model")})
    return {
        "profile_id": profile_id,
        "label": label,
        "state": state,
        "quota_kind": "api_rate_limit" if channel == "api" else "subscription_pressure",
        "channel": channel,
        "requires_attention": observed_exhausted or configured_attention or api_limit_reached,
        "window_hours": window_hours,
        "runs": len(rows),
        "completed_runs": len(completed),
        "usage_observed_runs": len(token_values),
        "token_usage_coverage": round(token_coverage, 4) if token_coverage is not None else None,
        "tokens_observed": sum(token_values) if token_values else None,
        "duration_seconds_observed": round(sum(duration_values), 3) if duration_values else None,
        "usage_limit_events": len(limit_times),
        "last_usage_limit_at": last_limit.isoformat() if last_limit else None,
        "last_success_at": last_success.isoformat() if last_success else None,
        "providers": providers,
        "models": models,
        "api_rate_limits": api_rate_limits,
        "forecast": forecast,
    }


def _load_rows(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with contextlib.closing(
            sqlite3.connect(uri, timeout=20.0, isolation_level=None, uri=True)
        ) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 20000")
            rows = conn.execute(
                """
                SELECT p.profile_id, p.provider, p.model, p.channel, p.quota_policy_json,
                       r.status, r.error_code, r.usage_json, r.started_at,
                       r.finished_at, r.created_at
                FROM run_adapter_profiles p
                JOIN runs r ON r.id = p.run_id
                WHERE p.channel IN ('subscription', 'api')
                ORDER BY r.created_at, r.id
                """
            ).fetchall()
    except sqlite3.Error:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["usage"] = _json_object(item.pop("usage_json", "{}"))
        item["quota_policy"] = _json_object(item.pop("quota_policy_json", "{}"))
        out.append(item)
    return out


def _normalize_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    unit = str(value.get("unit") or "").strip().lower()
    try:
        limit = float(value.get("limit") or 0)
        window_hours = int(value.get("window_hours") or 0)
    except (TypeError, ValueError):
        return {}
    if unit not in _UNITS or not math.isfinite(limit) or limit <= 0 or window_hours <= 0:
        return {}
    return {"unit": unit, "limit": _clean_number(limit), "window_hours": window_hours}


def _latest_api_rate_limits(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        observed = usage.get("_aiteam_rate_limits")
        if not isinstance(observed, dict):
            continue
        source = str(observed.get("source") or "provider_response_headers")
        scope = str(observed.get("scope") or "provider")
        model = str(row.get("model") or "")
        observed_at = _row_time(row)
        for dimension in observed.get("dimensions") or []:
            if not isinstance(dimension, dict):
                continue
            name = str(dimension.get("dimension") or "").lower()
            if name not in {"rpm", "rpd", "tpm", "tpd", "itpm", "otpm"}:
                continue
            limit = _nonnegative_number(dimension.get("limit"))
            remaining = _nonnegative_number(dimension.get("remaining"))
            utilization = None
            if limit and remaining is not None:
                utilization = round(max(0.0, min(1.0, (limit - remaining) / limit)), 4)
            latest[(model, name)] = {
                "model": model or None,
                "dimension": name,
                "unit": str(dimension.get("unit") or ""),
                "window": str(dimension.get("window") or ""),
                "limit": _clean_number(limit) if limit is not None else None,
                "remaining": _clean_number(remaining) if remaining is not None else None,
                "utilization": utilization,
                "reset": dimension.get("reset"),
                "scope": scope,
                "source": source,
                "observed_at": observed_at.isoformat() if observed_at else None,
            }
    return sorted(latest.values(), key=lambda item: (str(item["model"]), item["dimension"]))


def _nonnegative_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _tokens(usage: Any) -> int | None:
    if not isinstance(usage, dict):
        return None
    total = _positive_int(usage.get("total_tokens"))
    if total is not None:
        return total
    input_tokens = _positive_int(usage.get("input_tokens"))
    if input_tokens is None:
        input_tokens = _positive_int(usage.get("prompt_tokens"))
    output_tokens = _positive_int(usage.get("output_tokens"))
    if output_tokens is None:
        output_tokens = _positive_int(usage.get("completion_tokens"))
    if input_tokens is None and output_tokens is None:
        return None
    return int(input_tokens or 0) + int(output_tokens or 0)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _duration_seconds(row: dict[str, Any]) -> float | None:
    started = _parse_time(row.get("started_at"))
    finished = _parse_time(row.get("finished_at"))
    if not started or not finished or finished < started:
        return None
    return (finished - started).total_seconds()


def _row_time(row: dict[str, Any]) -> datetime | None:
    return _parse_time(row.get("finished_at")) or _parse_time(row.get("created_at"))


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean_number(value: float | int) -> float | int:
    numeric = float(value)
    return int(numeric) if numeric.is_integer() else round(numeric, 3)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
