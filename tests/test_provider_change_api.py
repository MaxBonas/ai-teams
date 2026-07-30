from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import api.routers.provider_changes as provider_router
from aiteam.db.provider_changes import reconcile_provider_snapshot
from aiteam.provider_change_detection import build_provider_snapshot
from aiteam.provider_change_intelligence import (
    build_provider_change_inventory,
)
from api.main import app

NOW = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)


def _seed_trigger(db_path: Path) -> None:
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


def test_provider_change_api_reconciles_lists_and_checks_revision(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "guided_setup.db"
    _seed_trigger(db_path)
    monkeypatch.setattr(
        provider_router,
        "machine_provider_change_db_path",
        lambda: db_path,
    )
    client = TestClient(app)

    reconciled = client.post("/api/provider-changes/reconcile")
    assert reconciled.status_code == 200
    assert reconciled.json()["created"] == 1
    case = reconciled.json()["cases"][0]

    listed = client.get(
        "/api/provider-changes/cases",
        params={"status": "awaiting_confirmation"},
    )
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["cases"]] == [case["id"]]

    confirmed = client.post(
        f"/api/provider-changes/cases/{case['id']}/transition",
        json={
            "action": "confirm",
            "expected_revision": case["revision"],
            "payload": {},
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["case"]["status"] == "awaiting_classification"

    stale = client.post(
        f"/api/provider-changes/cases/{case['id']}/transition",
        json={
            "action": "classify",
            "expected_revision": case["revision"],
            "payload": {},
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "provider_change_case_revision_stale"


def test_provider_change_api_rejects_unknown_status(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_router,
        "machine_provider_change_db_path",
        lambda: tmp_path / "guided_setup.db",
    )

    response = TestClient(app).get(
        "/api/provider-changes/cases",
        params={"status": "invented"},
    )

    assert response.status_code == 422


def test_provider_change_inbox_actions_are_owner_gated(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "guided_setup.db"
    _seed_trigger(db_path)
    monkeypatch.setattr(
        provider_router,
        "machine_provider_change_db_path",
        lambda: db_path,
    )
    client = TestClient(app)
    case = client.post("/api/provider-changes/reconcile").json()["cases"][0]

    inbox = client.get("/api/provider-changes/inbox")
    assert inbox.status_code == 200
    assert inbox.json()["counts"]["attention"] == 1

    managed = client.post(
        f"/api/provider-changes/cases/{case['id']}/notification",
        json={
            "action": "manage",
            "expected_revision": case["revision"],
        },
    )
    assert managed.status_code == 200
    updated = managed.json()["case"]
    assert updated["revision"] == case["revision"] + 1
    assert updated["history"][-1]["action"] == "notification_manage"

    stale = client.post(
        f"/api/provider-changes/cases/{case['id']}/notification",
        json={
            "action": "snooze",
            "expected_revision": case["revision"],
            "snooze_hours": 24,
        },
    )
    assert stale.status_code == 409


def test_external_delivery_api_starts_disabled_and_never_returns_endpoint(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "guided_setup.db"
    monkeypatch.setattr(
        provider_router,
        "machine_provider_change_db_path",
        lambda: db_path,
    )
    stored: list[str] = []

    def fake_store_secret(**kwargs) -> str:
        stored.append(str(kwargs["secret"]))
        return "secret:provider-change-webhook:api-test"

    monkeypatch.setattr(
        provider_router,
        "store_secret",
        fake_store_secret,
    )
    client = TestClient(app)

    initial = client.get("/api/provider-changes/inbox")
    assert initial.status_code == 200
    assert initial.json()["scope"]["external_delivery_enabled"] is False

    created = client.post(
        "/api/provider-changes/delivery/destinations",
        json={
            "label": "Webhook developer",
            "endpoint_url": "https://hooks.example.test/private-token",
            "explicit_consent": True,
            "minimum_severity": "warning",
            "delivery_mode": "urgent_and_digest",
            "cooldown_sec": 3600,
        },
    )
    assert created.status_code == 200
    destination = created.json()["destination"]
    assert destination["endpoint_configured"] is True
    assert "endpoint_url" not in destination
    assert "secret_ref" not in destination

    enable = client.post(
        f"/api/provider-changes/delivery/destinations/{destination['id']}/enabled",
        json={"enabled": True, "expected_revision": destination["revision"]},
    )
    assert enable.status_code == 422
    assert "healthy destination required" in enable.json()["detail"]

    listed = client.get("/api/provider-changes/delivery/destinations")
    serialized = listed.text
    assert listed.status_code == 200
    assert "private-token" not in serialized
    assert "provider-change-webhook:api-test" not in serialized

    stale = client.put(
        f"/api/provider-changes/delivery/destinations/{destination['id']}",
        json={
            "label": "Webhook stale",
            "endpoint_url": "https://hooks.example.test/stale-token",
            "explicit_consent": True,
            "minimum_severity": "error",
            "delivery_mode": "urgent_only",
            "cooldown_sec": 60,
            "expected_revision": 99,
        },
    )
    assert stale.status_code == 409
    assert stored == ["https://hooks.example.test/private-token"]
