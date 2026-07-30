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
