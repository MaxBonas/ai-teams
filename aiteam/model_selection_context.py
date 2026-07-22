"""Evidencia local y read-only para la selección contextual de modelos."""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from aiteam.policies import daily_cost_cap_cents
from aiteam.subscription_quota import subscription_quota_snapshot


def contextual_model_selection(
    db_path: Path,
    *,
    role: str,
    issue_id: str = "",
    run_profile: str = "",
    criticality: str = "medium",
    data_class: str = "public",
    required_capabilities: Iterable[str] = (),
    profiles: Iterable[Mapping[str, Any]] | None = None,
    read_model: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose the canonical contextual projection for every product consumer.

    The API, Team, quorum and lifecycle paths must not rebuild quota, issue or
    compatibility context independently. Optional inputs exist for deterministic
    tests and callers that already loaded the catalog; production callers omit
    them and receive the current machine projection.
    """
    from aiteam.compatibility_service import issue_compatibility_context
    from aiteam.model_catalog_service import get_current_model_catalog
    from aiteam.model_selection import build_contextual_model_selection
    from aiteam.user_config import load_adapter_profiles, model_options

    profile_rows = [dict(item) for item in (profiles or load_adapter_profiles())]
    runtime_context = model_selection_runtime_context(db_path, profiles=profile_rows)
    issue_context = (
        issue_compatibility_context(db_path, issue_id)
        if issue_id and db_path.is_file()
        else {}
    )
    required = sorted({
        *(str(item).strip() for item in required_capabilities if str(item).strip()),
        *(
            str(item).strip()
            for item in issue_context.get("required_capabilities") or ()
            if str(item).strip()
        ),
    })
    current_read_model = dict(read_model) if read_model is not None else get_current_model_catalog(
        db_paths=(db_path,) if db_path.is_file() else ()
    )
    return build_contextual_model_selection(
        current_read_model,
        role=role,
        profiles=profile_rows,
        options_by_profile=model_options(),
        run_profile=str(issue_context.get("run_profile") or run_profile),
        criticality=str(issue_context.get("criticality") or criticality),
        data_class=str(issue_context.get("data_class") or data_class),
        required_capabilities=required,
        capacity_by_profile=runtime_context["capacity_by_profile"],
        budget_evidence=runtime_context["budget"],
    )


def model_selection_runtime_context(
    db_path: Path, *, profiles: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    profile_rows = [dict(item) for item in profiles]
    capacity_rows = subscription_quota_snapshot(db_path, profiles=profile_rows)
    capacity = {
        str(row.get("profile_id") or ""): {
            **row,
            "source": "subscription_quota_snapshot",
        }
        for row in capacity_rows
        if str(row.get("profile_id") or "")
    }
    cap = daily_cost_cap_cents()
    if cap <= 0:
        budget = {
            "status": "unbounded",
            "source": "daily_cost_cap_policy",
            "cap_cents": None,
            "spent_cents": None,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
    else:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        spent = _spent_today(db_path, day)
        budget = {
            "status": "limit_reached" if spent >= cap else "available",
            "source": "cost_events+daily_cost_cap_policy",
            "day": day,
            "cap_cents": cap,
            "spent_cents": spent,
            "remaining_cents": max(0, cap - spent),
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
    return {"capacity_by_profile": capacity, "budget": budget}


def _spent_today(db_path: Path, day: str) -> int:
    try:
        with contextlib.closing(sqlite3.connect(str(db_path), timeout=20.0)) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_cents), 0) FROM cost_events WHERE substr(created_at,1,10) = ?",
                (day,),
            ).fetchone()
            return int((row or [0])[0] or 0)
    except sqlite3.Error:
        return 0
