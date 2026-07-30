"""Auditoría hermética del inbox local P0.N.5."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.db.provider_change_workflows import (  # noqa: E402
    ProviderChangeConflictError,
    reconcile_provider_change_cases,
    record_provider_change_notification_action,
)
from aiteam.db.provider_changes import reconcile_provider_snapshot  # noqa: E402
from aiteam.provider_change_detection import (  # noqa: E402
    build_provider_snapshot,
)
from aiteam.provider_change_intelligence import (  # noqa: E402
    build_provider_change_inventory,
)
from aiteam.provider_change_notifications import (  # noqa: E402
    build_provider_change_inbox,
)

NOW = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)


def build_report() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="aiteam-provider-notification-audit-"
    ) as raw_dir:
        db_path = Path(raw_dir) / "guided_setup.db"
        case = _seed_case(db_path)
        initial = build_provider_change_inbox(
            db_path,
            now=NOW + timedelta(hours=3),
        )
        notification = initial["notifications"][0]
        acknowledged = record_provider_change_notification_action(
            db_path,
            case["id"],
            action="acknowledge",
            expected_revision=case["revision"],
            actor="owner",
            now=NOW + timedelta(hours=3),
        )
        after_ack = build_provider_change_inbox(
            db_path,
            now=NOW + timedelta(hours=3),
        )
        snoozed = record_provider_change_notification_action(
            db_path,
            case["id"],
            action="snooze",
            expected_revision=acknowledged["revision"],
            actor="owner",
            snooze_hours=24,
            now=NOW + timedelta(hours=4),
        )
        hidden = build_provider_change_inbox(
            db_path,
            now=NOW + timedelta(hours=5),
        )
        visible = build_provider_change_inbox(
            db_path,
            now=NOW + timedelta(hours=5),
            include_snoozed=True,
        )
        expired = build_provider_change_inbox(
            db_path,
            now=NOW + timedelta(hours=29),
        )
        stale_rejected = False
        try:
            record_provider_change_notification_action(
                db_path,
                case["id"],
                action="manage",
                expected_revision=case["revision"],
                actor="owner",
            )
        except ProviderChangeConflictError:
            stale_rejected = True

        checks = {
            "local_scope_without_external_delivery": (
                initial["scope"]["machine_local"] is True
                and initial["scope"]["external_delivery_enabled"] is False
                and initial["scope"]["commands_executed"] is False
            ),
            "banner_and_count_match_attention": (
                initial["banner"]["visible"] is True
                and initial["counts"]["attention"] == 1
            ),
            "provider_group_is_deduplicated": (
                len(initial["groups"]) == 1
                and initial["groups"][0]["count"] == 1
            ),
            "notification_exposes_operational_context": (
                notification["interaction_id"] == case["id"]
                and notification["provider"]["surface"] == "model_catalog"
                and notification["next_action"]["action"] == "confirm"
            ),
            "acknowledge_is_revisioned_activity": (
                acknowledged["revision"] == case["revision"] + 1
                and after_ack["notifications"][0]["activity"][-1]["action"]
                == "notification_acknowledge"
            ),
            "snooze_is_revisioned_activity": (
                snoozed["revision"] == acknowledged["revision"] + 1
                and snoozed["history"][-1]["action"]
                == "notification_snooze"
            ),
            "snoozed_is_hidden_by_default": (
                hidden["counts"]["total"] == 0
                and hidden["banner"]["visible"] is False
            ),
            "snoozed_remains_explicitly_inspectable": (
                visible["notifications"][0]["event_status"] == "snoozed"
            ),
            "expired_snooze_reopens_projection": (
                expired["notifications"][0]["event_status"] == "open"
            ),
            "stale_revision_fails_closed": stale_rejected,
        }
    return {
        "schema_version": "provider_change_notification_audit_v1",
        "observed_at": NOW.isoformat(),
        "ok": all(checks.values()),
        "checks": checks,
        "counts": {
            "passed": sum(1 for value in checks.values() if value),
            "total": len(checks),
        },
        "scope": {
            "temporary_sqlite": True,
            "network_used": False,
            "secrets_read": False,
            "login_used": False,
            "inference_used": False,
            "commands_executed": False,
            "external_notifications_sent": False,
            "routing_changed": False,
        },
    }


def _seed_case(db_path: Path) -> dict[str, Any]:
    component = next(
        row
        for row in build_provider_change_inventory()["components"]
        if row["surface"] == "model_catalog"
        and row["profile_id"] == "codex_subscription"
    )
    reconcile_provider_snapshot(db_path, _snapshot(component, 1000, NOW))
    reconcile_provider_snapshot(
        db_path,
        _snapshot(component, 2000, NOW + timedelta(hours=1)),
    )
    cases = reconcile_provider_change_cases(
        db_path,
        now=NOW + timedelta(hours=2),
    )
    if len(cases) != 1:
        raise RuntimeError("notification audit fixture did not create one case")
    return cases[0]


def _snapshot(
    component: dict[str, Any],
    context: int,
    observed_at: datetime,
) -> dict[str, Any]:
    return build_provider_snapshot(
        component,
        {
            "status": "observed",
            "installed_version": "0.146.0-alpha.6",
            "latest_known_version": "0.146.0-alpha.6",
            "compatibility": {
                "installed": "compatible",
                "latest_known": "compatible",
            },
            "dimensions": {
                "model_id": [{
                    "id": "gpt-5.6-sol",
                    "aliases": [],
                    "context": context,
                    "tools": True,
                    "structured_output": True,
                    "price": "subscription",
                    "quota": "subscription",
                    "lifecycle": "active",
                }]
            },
        },
        observed_at=observed_at.isoformat(),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_report()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        print(rendered, end="")
    return 1 if args.strict and not report["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
