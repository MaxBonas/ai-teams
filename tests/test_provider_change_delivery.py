from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aiteam.db.provider_change_workflows import reconcile_provider_change_cases
from aiteam.db.provider_changes import reconcile_provider_snapshot
from aiteam.provider_change_delivery import (
    ProviderChangeDeliveryConflictError,
    configure_destination,
    deliver_provider_change_outbox,
    delivery_summary,
    ensure_provider_change_delivery_schema,
    set_destination_enabled,
    sync_provider_change_outbox,
    validate_webhook_endpoint,
)
from aiteam.provider_change_delivery import (
    test_destination as probe_destination,
)
from aiteam.provider_change_detection import build_provider_snapshot
from aiteam.provider_change_intelligence import (
    build_provider_change_inventory,
)

NOW = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
ENDPOINT = "https://hooks.example.test/aiteams"
SECRET_REF = "secret:provider-change-webhook:primary"
SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "aiteam" / "db" / "schema.sql"
)


def _seed_case(db_path: Path) -> dict:
    component = next(
        row
        for row in build_provider_change_inventory()["components"]
        if row["surface"] == "model_catalog"
        and row["profile_id"] == "codex_subscription"
    )

    def snapshot(context: int, observed_at: datetime) -> dict:
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
                    "model_id": [
                        {
                            "id": "gpt-5.6-sol",
                            "aliases": [],
                            "context": context,
                            "tools": True,
                            "structured_output": True,
                            "price": "subscription",
                            "quota": "subscription",
                            "lifecycle": "active",
                        }
                    ]
                },
            },
            observed_at=observed_at.isoformat(),
        )

    reconcile_provider_snapshot(db_path, snapshot(1000, NOW))
    reconcile_provider_snapshot(
        db_path,
        snapshot(2000, NOW + timedelta(hours=1)),
    )
    return reconcile_provider_change_cases(
        db_path,
        now=NOW + timedelta(hours=2),
    )[0]


def _configured(db_path: Path, *, consent: bool = True) -> dict:
    return configure_destination(
        db_path,
        destination_id="destination-1",
        label="Webhook del developer",
        endpoint_secret_ref=SECRET_REF,
        minimum_severity="info",
        delivery_mode="urgent_and_digest",
        cooldown_sec=3600,
        explicit_consent=consent,
        now=NOW,
    )


def _healthy_and_enabled(db_path: Path) -> dict:
    destination = _configured(db_path)
    destination = probe_destination(
        db_path,
        destination["id"],
        expected_revision=destination["revision"],
        sender=lambda endpoint, payload: (
            endpoint == ENDPOINT and payload["kind"] == "health_check",
            204,
            None,
        ),
        secret_resolver=lambda ref: ENDPOINT if ref == SECRET_REF else None,
        now=NOW + timedelta(minutes=1),
    )
    return set_destination_enabled(
        db_path,
        destination["id"],
        enabled=True,
        expected_revision=destination["revision"],
        secret_resolver=lambda ref: ENDPOINT if ref == SECRET_REF else None,
        now=NOW + timedelta(minutes=2),
    )


def test_schema_contract_and_runtime_ensure_match(tmp_path: Path) -> None:
    names = {
        "provider_change_notification_destinations",
        "provider_change_notification_outbox",
        "provider_change_notification_receipts",
    }
    runtime_db = tmp_path / "runtime.db"
    ensure_provider_change_delivery_schema(runtime_db)
    with sqlite3.connect(runtime_db) as conn:
        runtime_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    schema_db = tmp_path / "schema.db"
    with sqlite3.connect(schema_db) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        schema_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert names <= runtime_tables
    assert names <= schema_tables


def test_delivery_is_off_by_default_and_endpoint_contract_is_strict(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "guided_setup.db"

    summary = delivery_summary(db_path)

    assert summary["external_delivery_enabled"] is False
    assert summary["destinations"] == []
    assert validate_webhook_endpoint(ENDPOINT) == ENDPOINT
    for invalid in (
        "http://hooks.example.test/aiteams",
        "https://user:token@hooks.example.test/aiteams",
        "https://hooks.example.test/aiteams#token",
    ):
        with pytest.raises(ValueError):
            validate_webhook_endpoint(invalid)


def test_destination_needs_consent_health_and_current_revision(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "guided_setup.db"
    destination = _configured(db_path, consent=False)

    with pytest.raises(ValueError, match="explicit consent"):
        set_destination_enabled(
            db_path,
            destination["id"],
            enabled=True,
            expected_revision=destination["revision"],
            secret_resolver=lambda _ref: ENDPOINT,
            now=NOW,
        )

    consented = configure_destination(
        db_path,
        destination_id=destination["id"],
        expected_revision=destination["revision"],
        label=destination["label"],
        endpoint_secret_ref=SECRET_REF,
        minimum_severity="info",
        delivery_mode="urgent_and_digest",
        cooldown_sec=3600,
        explicit_consent=True,
        now=NOW,
    )
    with pytest.raises(ValueError, match="healthy"):
        set_destination_enabled(
            db_path,
            consented["id"],
            enabled=True,
            expected_revision=consented["revision"],
            secret_resolver=lambda _ref: ENDPOINT,
            now=NOW,
        )
    with pytest.raises(ProviderChangeDeliveryConflictError):
        probe_destination(
            db_path,
            consented["id"],
            expected_revision=destination["revision"],
            sender=lambda _endpoint, _payload: (True, 204, None),
            secret_resolver=lambda _ref: ENDPOINT,
            now=NOW,
        )


def test_outbox_deduplicates_fingerprint_and_receipt_is_redacted(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "guided_setup.db"
    case = _seed_case(db_path)
    destination = _healthy_and_enabled(db_path)
    sent: list[dict] = []

    first = sync_provider_change_outbox(db_path, now=NOW + timedelta(hours=3))
    second = sync_provider_change_outbox(db_path, now=NOW + timedelta(hours=3))
    delivered = deliver_provider_change_outbox(
        db_path,
        sender=lambda endpoint, payload: (
            sent.append({"endpoint": endpoint, "payload": payload}) is None,
            202,
            None,
        ),
        secret_resolver=lambda ref: ENDPOINT if ref == SECRET_REF else None,
        now=NOW + timedelta(hours=3),
    )

    assert first == {"inserted": 1, "suppressed": 0}
    assert second == {"inserted": 0, "suppressed": 0}
    assert delivered == {"delivered": 1, "failed": 0, "deferred": 0}
    assert sent[0]["payload"]["items"][0]["case_id"] == case["id"]
    assert destination["enabled"] is True

    with sqlite3.connect(db_path) as conn:
        outbox_count = conn.execute(
            "SELECT COUNT(*) FROM provider_change_notification_outbox"
        ).fetchone()[0]
        receipt = conn.execute(
            """
            SELECT outbox_ids_json, content_sha256, error_code
            FROM provider_change_notification_receipts
            WHERE status = 'delivered'
            """
        ).fetchone()
        stored = "\n".join(
            str(row[0])
            for row in conn.execute(
                """
                SELECT endpoint_secret_ref
                FROM provider_change_notification_destinations
                """
            )
        )
    assert outbox_count == 1
    assert receipt is not None and len(receipt[1]) == 64
    assert receipt[2] is None
    assert ENDPOINT not in stored
    assert SECRET_REF in stored


def test_failed_delivery_retries_then_disables_destination(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "guided_setup.db"
    _seed_case(db_path)
    _healthy_and_enabled(db_path)
    sync_provider_change_outbox(db_path, now=NOW + timedelta(hours=3))
    sender = lambda _endpoint, _payload: (False, 503, "provider body secret")

    for minute in (0, 2, 5):
        deliver_provider_change_outbox(
            db_path,
            sender=sender,
            secret_resolver=lambda _ref: ENDPOINT,
            now=NOW + timedelta(hours=3, minutes=minute),
        )

    summary = delivery_summary(db_path)
    destination = summary["destinations"][0]
    assert destination["enabled"] is False
    assert destination["health_status"] == "unhealthy"
    assert summary["outbox"] == {"failed": 1}
    with sqlite3.connect(db_path) as conn:
        error_codes = [
            row[0]
            for row in conn.execute(
                """
                SELECT error_code
                FROM provider_change_notification_receipts
                WHERE status = 'failed'
                ORDER BY created_at
                """
            )
        ]
    assert error_codes == ["delivery_error"] * 3
