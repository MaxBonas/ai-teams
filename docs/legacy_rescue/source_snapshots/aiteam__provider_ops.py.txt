from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiteam.mailbox import Mailbox
from aiteam.observability import EventLogger
from aiteam.model_catalog import load_model_catalog, provider_smoke_details


def build_provider_ops_view(runtime_dir: Path) -> dict[str, Any]:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    catalog = load_model_catalog(runtime_dir.parent, runtime_dir)
    doctor = _read_json(runtime_dir / "provider_doctor.json")
    accounts = _read_json(runtime_dir / "provider_accounts.json")
    smoke = provider_smoke_details(runtime_dir)

    rows = []
    for adapter_name, profile in catalog.items():
        doctor_row = _find_doctor_row(doctor, adapter_name)
        smoke_row = smoke.get(adapter_name, {})
        healthy_doctor = bool(doctor_row.get("healthy", False)) if doctor_row else False
        healthy_smoke = bool(smoke_row.get("healthy", False)) if smoke_row else False
        operational = healthy_doctor and healthy_smoke
        rows.append(
            {
                "adapter_name": adapter_name,
                "provider": profile.provider,
                "model": profile.model,
                "tier": profile.tier,
                "intelligence_rank": profile.intelligence_rank,
                "coding_rank": profile.coding_rank,
                "reasoning_rank": profile.reasoning_rank,
                "trust_rank": profile.trust_rank,
                "doctor_healthy": healthy_doctor,
                "doctor_details": str(doctor_row.get("details", ""))
                if doctor_row
                else "missing",
                "smoke_healthy": healthy_smoke,
                "smoke_details": str(smoke_row.get("details", ""))
                if smoke_row
                else "missing",
                "operational": operational,
                "degraded": healthy_doctor and not healthy_smoke,
                "team_lead_eligible": profile.tier in {"senior_cloud", "advanced_api"}
                and operational,
                "notes": profile.notes,
            }
        )

    summary = {
        "operational_count": sum(1 for row in rows if row["operational"]),
        "degraded_count": sum(1 for row in rows if row["degraded"]),
        "team_lead_candidates": [
            row["adapter_name"]
            for row in sorted(
                rows,
                key=lambda item: (
                    -(
                        int(item["reasoning_rank"])
                        + int(item["coding_rank"])
                        + int(item["trust_rank"])
                    )
                ),
            )
            if row["team_lead_eligible"]
        ],
    }
    alerts = _provider_alerts(rows, summary)
    payload = {
        "summary": summary,
        "alerts": alerts,
        "providers": rows,
        "api_keys": doctor.get("api_keys", {}) if isinstance(doctor, dict) else {},
        "local_runtime": doctor.get("local_runtime", {})
        if isinstance(doctor, dict)
        else {},
        "accounts": accounts,
    }
    output = runtime_dir / "provider_ops.json"
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    return payload


def sync_provider_ops_alerts(runtime_dir: Path) -> dict[str, Any]:
    previous = _read_json(runtime_dir / "provider_ops.json")
    payload = build_provider_ops_view(runtime_dir)
    changes = _provider_state_changes(previous, payload)
    if not changes:
        return {"payload": payload, "changes": [], "emitted": False}

    event_logger = EventLogger(runtime_dir)
    mailbox = Mailbox(runtime_dir / "mailbox.jsonl")
    for change in changes:
        event_logger.emit("provider_ops_alert", change)

    body_lines = ["Cambios detectados en providers/modelos:"]
    for change in changes:
        body_lines.append(
            f"- {change['adapter_name']}: {change['change_type']} ({change['from_state']} -> {change['to_state']})"
        )
    mailbox.send(
        sender="provider-ops-bot",
        recipient="team_lead",
        subject="Provider ops alert",
        body="\n".join(body_lines),
    )
    return {"payload": payload, "changes": changes, "emitted": True}


def provider_ops_status(runtime_dir: Path | None) -> dict[str, dict[str, Any]]:
    if runtime_dir is None:
        return {}
    path = runtime_dir / "provider_ops.json"
    if not path.exists():
        return {}
    payload = _read_json(path)
    rows = payload.get("providers", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return {}
    return {
        str(item.get("adapter_name", "")): dict(item)
        for item in rows
        if isinstance(item, dict) and str(item.get("adapter_name", "")).strip()
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _find_doctor_row(
    payload: dict[str, Any], adapter_name: str
) -> dict[str, Any] | None:
    rows = payload.get("providers", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return None
    for item in rows:
        if isinstance(item, dict) and str(item.get("name", "")).strip() == adapter_name:
            return item
    return None


def _provider_alerts(rows: list[dict[str, Any]], summary: dict[str, Any]) -> list[str]:
    alerts: list[str] = []
    if not summary.get("team_lead_candidates"):
        alerts.append("No healthy Team Lead candidates available")

    degraded_senior = [
        row["adapter_name"]
        for row in rows
        if row.get("tier") == "senior_cloud" and row.get("degraded")
    ]
    if degraded_senior:
        alerts.append("Senior cloud degraded: " + ", ".join(sorted(degraded_senior)))

    budget_operational = [
        row["adapter_name"]
        for row in rows
        if row.get("tier") == "budget_api" and row.get("operational")
    ]
    senior_operational = [
        row["adapter_name"]
        for row in rows
        if row.get("tier") == "senior_cloud" and row.get("operational")
    ]
    if budget_operational and len(senior_operational) <= 1:
        alerts.append(
            "System may rely on budget_api fallback: "
            + ", ".join(sorted(budget_operational))
        )

    return alerts


def _provider_state_changes(
    previous: dict[str, Any], current: dict[str, Any]
) -> list[dict[str, Any]]:
    prev_rows = {
        str(item.get("adapter_name", "")): item
        for item in previous.get("providers", [])
        if isinstance(item, dict) and str(item.get("adapter_name", "")).strip()
    }
    cur_rows = {
        str(item.get("adapter_name", "")): item
        for item in current.get("providers", [])
        if isinstance(item, dict) and str(item.get("adapter_name", "")).strip()
    }
    changes: list[dict[str, Any]] = []
    for name, current_row in cur_rows.items():
        previous_row = prev_rows.get(name)
        if previous_row is None:
            continue
        prev_state = _row_state(previous_row)
        cur_state = _row_state(current_row)
        if prev_state == cur_state:
            continue
        changes.append(
            {
                "adapter_name": name,
                "provider": current_row.get("provider", ""),
                "tier": current_row.get("tier", ""),
                "change_type": "provider_state_changed",
                "from_state": prev_state,
                "to_state": cur_state,
                "doctor_details": current_row.get("doctor_details", ""),
                "smoke_details": current_row.get("smoke_details", ""),
            }
        )
    return changes


def _row_state(row: dict[str, Any]) -> str:
    if bool(row.get("operational", False)):
        return "operational"
    if bool(row.get("degraded", False)):
        return "degraded"
    return "offline"
