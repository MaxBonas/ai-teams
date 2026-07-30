"""Auditoría hermética de entrega externa opt-in P0.N.5.4."""

from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.db.provider_change_workflows import (
    reconcile_provider_change_cases,
)
from aiteam.db.provider_changes import reconcile_provider_snapshot
from aiteam.provider_change_delivery import (
    ProviderChangeDeliveryConflictError,
    configure_destination,
    deliver_provider_change_outbox,
    delivery_summary,
    set_destination_enabled,
    sync_provider_change_outbox,
    test_destination,
)
from aiteam.provider_change_detection import (
    build_provider_snapshot,
)
from aiteam.provider_change_intelligence import (
    build_provider_change_inventory,
)

NOW = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
ENDPOINT = "https://hooks.example.test/private-token"
SECRET_REF = "secret:provider-change-webhook:audit"


def build_report() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="aiteam-provider-delivery-audit-"
    ) as raw_dir:
        db_path = Path(raw_dir) / "guided_setup.db"
        case = _seed_case(db_path)
        initial = delivery_summary(db_path)
        destination = configure_destination(
            db_path,
            destination_id="audit-destination",
            label="Developer",
            endpoint_secret_ref=SECRET_REF,
            minimum_severity="info",
            delivery_mode="urgent_and_digest",
            cooldown_sec=3600,
            explicit_consent=True,
            now=NOW,
        )
        blocked_without_health = False
        try:
            set_destination_enabled(
                db_path,
                destination["id"],
                enabled=True,
                expected_revision=destination["revision"],
                secret_resolver=lambda _ref: ENDPOINT,
                now=NOW,
            )
        except ValueError:
            blocked_without_health = True
        health_payloads: list[dict[str, Any]] = []
        destination = test_destination(
            db_path,
            destination["id"],
            expected_revision=destination["revision"],
            sender=lambda _endpoint, payload: (
                health_payloads.append(payload) is None,
                204,
                None,
            ),
            secret_resolver=lambda _ref: ENDPOINT,
            now=NOW + timedelta(minutes=1),
        )
        destination = set_destination_enabled(
            db_path,
            destination["id"],
            enabled=True,
            expected_revision=destination["revision"],
            secret_resolver=lambda _ref: ENDPOINT,
            now=NOW + timedelta(minutes=2),
        )
        first_sync = sync_provider_change_outbox(
            db_path, now=NOW + timedelta(hours=3)
        )
        second_sync = sync_provider_change_outbox(
            db_path, now=NOW + timedelta(hours=3)
        )
        deliveries: list[dict[str, Any]] = []
        result = deliver_provider_change_outbox(
            db_path,
            sender=lambda _endpoint, payload: (
                deliveries.append(payload) is None,
                202,
                None,
            ),
            secret_resolver=lambda _ref: ENDPOINT,
            now=NOW + timedelta(hours=3),
        )
        stale_rejected = False
        try:
            set_destination_enabled(
                db_path,
                destination["id"],
                enabled=False,
                expected_revision=destination["revision"] - 1,
                secret_resolver=lambda _ref: ENDPOINT,
                now=NOW,
            )
        except ProviderChangeDeliveryConflictError:
            stale_rejected = True

        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            raw_destination = dict(conn.execute(
                """
                SELECT * FROM provider_change_notification_destinations
                WHERE id = 'audit-destination'
                """
            ).fetchone())
            outbox = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM provider_change_notification_outbox"
                ).fetchall()
            ]
            receipts = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM provider_change_notification_receipts"
                ).fetchall()
            ]
        serialized_storage = json.dumps(
            {"destination": raw_destination, "outbox": outbox, "receipts": receipts},
            sort_keys=True,
        )
        delivery_payload = deliveries[0]
        checks = {
            "zero_destinations_enabled_by_default": (
                initial["external_delivery_enabled"] is False
                and initial["destinations"] == []
            ),
            "database_keeps_only_endpoint_reference": (
                raw_destination["endpoint_secret_ref"] == SECRET_REF
                and ENDPOINT not in serialized_storage
            ),
            "explicit_consent_is_recorded": (
                bool(raw_destination["explicit_consent"])
                and raw_destination["consented_at"] is not None
            ),
            "activation_requires_health": blocked_without_health,
            "health_probe_is_explicit_and_receipted": (
                health_payloads[0]["kind"] == "health_check"
                and any(row["status"] == "health_passed" for row in receipts)
            ),
            "healthy_destination_can_be_enabled": destination["enabled"] is True,
            "fingerprint_is_deduplicated": (
                first_sync == {"inserted": 1, "suppressed": 0}
                and second_sync == {"inserted": 0, "suppressed": 0}
                and len(outbox) == 1
            ),
            "payload_is_redacted_and_actionable": (
                delivery_payload["items"][0]["case_id"] == case["id"]
                and "endpoint" not in json.dumps(delivery_payload)
                and "secret" not in json.dumps(delivery_payload)
            ),
            "digest_delivery_completes": (
                result == {"delivered": 1, "failed": 0, "deferred": 0}
                and outbox[0]["delivery_class"] == "digest"
            ),
            "delivery_receipt_has_hash_without_body": any(
                row["status"] == "delivered"
                and len(row["content_sha256"]) == 64
                and "response_body" not in row
                for row in receipts
            ),
            "stale_revision_fails_closed": stale_rejected,
            "external_delivery_never_changes_routing": (
                delivery_payload["items"][0]["cockpit_path"]
                == "/config?section=provider-changes"
            ),
        }
    return {
        "schema_version": "provider_change_delivery_audit_v1",
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
            "fake_transport_used": True,
            "real_secrets_read": False,
            "inference_used": False,
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
    for context, observed_at in (
        (1000, NOW),
        (2000, NOW + timedelta(hours=1)),
    ):
        reconcile_provider_snapshot(
            db_path,
            build_provider_snapshot(
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
            ),
        )
    cases = reconcile_provider_change_cases(
        db_path,
        now=NOW + timedelta(hours=2),
    )
    if len(cases) != 1:
        raise RuntimeError("delivery audit fixture did not create one case")
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "UPDATE provider_change_cases SET severity = 'warning' WHERE id = ?",
            (cases[0]["id"],),
        )
        conn.execute(
            "UPDATE provider_change_events SET severity = 'warning' WHERE id = ?",
            (cases[0]["event_id"],),
        )
        conn.commit()
    return cases[0]


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
