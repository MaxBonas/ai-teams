"""Proyección read-only del inbox local de cambios de proveedor."""

from __future__ import annotations

import contextlib
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.db.provider_change_workflows import (
    get_provider_change_case,
    list_provider_change_cases,
)

SCHEMA_VERSION = "provider_change_inbox_v1"
_TERMINAL = frozenset({"accepted", "rejected", "reverted"})
_SEVERITY_ORDER = {"critical": 0, "error": 1, "warning": 2, "info": 3}


def build_provider_change_inbox(
    db_path: Path,
    *,
    now: datetime | str | None = None,
    include_snoozed: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    current = _as_datetime(now)
    cases = [
        get_provider_change_case(db_path, row["id"])
        for row in list_provider_change_cases(db_path, limit=limit)
    ]
    events = _read_events(db_path)
    notifications: list[dict[str, Any]] = []
    for case in cases:
        if case["status"] in _TERMINAL:
            continue
        event = events.get(str(case["event_id"]))
        if event is None or event["status"] == "resolved":
            continue
        effective_status = _effective_event_status(event, current)
        if effective_status == "snoozed" and not include_snoozed:
            continue
        notifications.append(_notification(case, event, effective_status, current))
    notifications.sort(
        key=lambda row: (
            _SEVERITY_ORDER.get(row["severity"], 4),
            -int(row["age"]["seconds"]),
            row["id"],
        )
    )
    severity_counts = Counter(row["severity"] for row in notifications)
    status_counts = Counter(row["event_status"] for row in notifications)
    attention = [
        row for row in notifications
        if row["event_status"] in {"open", "acknowledged"}
    ]
    groups = _groups(notifications)
    highest = attention[0] if attention else None
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "machine_local": True,
            "read_only": True,
            "external_delivery_enabled": False,
            "external_delivery_reason": "external_channels_not_configured",
            "commands_executed": False,
        },
        "counts": {
            "total": len(notifications),
            "attention": len(attention),
            "critical": severity_counts["critical"],
            "snoozed": status_counts["snoozed"],
            "by_severity": dict(sorted(severity_counts.items())),
            "by_status": dict(sorted(status_counts.items())),
        },
        "banner": {
            "visible": bool(attention),
            "tone": highest["severity"] if highest else "quiet",
            "title": (
                f"{len(attention)} cambio(s) de proveedor requieren atención"
                if attention
                else "Proveedores sin cambios pendientes"
            ),
            "summary": highest["summary"] if highest else None,
            "case_id": highest["id"] if highest else None,
        },
        "groups": groups,
        "notifications": notifications,
        "observed_at": current.isoformat(),
    }


def _notification(
    case: dict[str, Any],
    event: dict[str, Any],
    event_status: str,
    now: datetime,
) -> dict[str, Any]:
    seen = _as_datetime(event["first_seen_at"])
    age_seconds = max(0, int((now - seen).total_seconds()))
    impact = case.get("impact") or {}
    diff_summary = (case.get("diff") or {}).get("summary") or {}
    next_action = _next_action(str(case["status"]))
    evidence = _evidence_refs(case)
    return {
        "id": case["id"],
        "interaction_id": case["id"],
        "revision": case["revision"],
        "event_id": case["event_id"],
        "event_status": event_status,
        "workflow_status": case["status"],
        "severity": str(case.get("severity") or "info"),
        "title": case["title"],
        "summary": case["summary"],
        "provider": {
            "profile_id": event.get("profile_id"),
            "provider_id": event["provider_id"],
            "component_id": event["component_id"],
            "surface": event["surface"],
        },
        "change": {
            "kind": event["kind"],
            "dimension": event["dimension"],
            "decision": diff_summary.get("decision"),
            "occurrences": event["occurrence_count"],
        },
        "impact": {
            "profile_ids": impact.get("profile_ids") or [],
            "model_ids": impact.get("model_ids") or [],
            "roles": impact.get("roles") or [],
            "existing_assignment_policy": impact.get(
                "existing_assignment_policy"
            ),
            "new_selection_policy": impact.get("new_selection_policy"),
        },
        "age": {
            "seconds": age_seconds,
            "band": _age_band(age_seconds),
            "label": _age_label(age_seconds),
            "first_seen_at": event["first_seen_at"],
            "last_seen_at": event["last_seen_at"],
        },
        "recommendation": case.get("recommendation"),
        "guided_commands": case.get("guided_commands") or [],
        "risk": case.get("risk"),
        "rollback": case.get("rollback"),
        "evidence": evidence,
        "activity": case.get("history") or [],
        "next_action": next_action,
        "actions": {
            "acknowledge": event_status == "open",
            "snooze": event_status in {"open", "acknowledged"},
            "manage": True,
        },
        "snoozed_until": event.get("snoozed_until"),
    }


def _read_events(db_path: Path) -> dict[str, dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return {}
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with contextlib.closing(
        sqlite3.connect(uri, timeout=20.0, uri=True)
    ) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='provider_change_events'"
        ).fetchone()
        if exists is None:
            return {}
        return {
            str(row["id"]): dict(row)
            for row in conn.execute(
                "SELECT * FROM provider_change_events"
            ).fetchall()
        }


def _effective_event_status(event: dict[str, Any], now: datetime) -> str:
    if event["status"] != "snoozed" or not event.get("snoozed_until"):
        return str(event["status"])
    return (
        "open"
        if _as_datetime(event["snoozed_until"]) <= now
        else "snoozed"
    )


def _groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row["provider"]["provider_id"])
        grouped.setdefault(key, []).append(row)
    return [
        {
            "provider_id": provider_id,
            "count": len(items),
            "highest_severity": min(
                (item["severity"] for item in items),
                key=lambda value: _SEVERITY_ORDER.get(value, 4),
            ),
            "case_ids": [item["id"] for item in items],
        }
        for provider_id, items in sorted(grouped.items())
    ]


def _evidence_refs(case: dict[str, Any]) -> list[str]:
    refs: set[str] = set()
    for value in (
        case.get("validation"),
        case.get("outcome"),
        case.get("classification"),
    ):
        if not isinstance(value, dict):
            continue
        for key in ("evidence_receipts", "receipts"):
            raw = value.get(key)
            if isinstance(raw, list):
                refs.update(str(item) for item in raw if str(item))
    return sorted(refs)


def _next_action(status: str) -> dict[str, str]:
    actions = {
        "awaiting_confirmation": (
            "confirm",
            "Confirmar que el cambio es real",
        ),
        "awaiting_classification": (
            "classify",
            "Clasificar alcance e impacto exactos",
        ),
        "awaiting_approval": ("approve", "Aprobar la remediación manual"),
        "approved": ("record_application", "Registrar la aplicación externa"),
        "awaiting_validation": (
            "record_validation",
            "Registrar doctor y probe exactos",
        ),
        "validation_failed": (
            "record_application",
            "Corregir o revertir y repetir validación",
        ),
        "awaiting_recalibration": (
            "record_recalibration",
            "Adjuntar calibración proporcional",
        ),
        "ready_to_accept": ("accept", "Aceptar o revertir el cambio"),
    }
    action, label = actions.get(status, ("inspect", "Inspeccionar expediente"))
    return {"action": action, "label": label}


def _age_band(seconds: int) -> str:
    if seconds < 3600:
        return "new"
    if seconds < 86400:
        return "today"
    if seconds < 604800:
        return "week"
    return "old"


def _age_label(seconds: int) -> str:
    if seconds < 60:
        return "ahora"
    if seconds < 3600:
        return f"hace {seconds // 60} min"
    if seconds < 86400:
        return f"hace {seconds // 3600} h"
    return f"hace {seconds // 86400} d"


def _as_datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
