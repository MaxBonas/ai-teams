from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.utils as utils
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.orientation_measurement import (
    end_orientation_session,
    erase_orientation_measurement,
    measurement_state,
    orientation_summary,
    record_orientation_event,
    set_measurement_consent,
)
from api.main import app


def _database(tmp_path: Path) -> Path:
    db = tmp_path / "runtime" / "aiteam.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return db


def test_events_require_explicit_consent_and_closed_vocabulary(tmp_path: Path) -> None:
    db = _database(tmp_path)

    assert measurement_state(db)["enabled"] is False
    with pytest.raises(PermissionError, match="not_consented"):
        record_orientation_event(db, flow="inbox", event="flow_started")

    first = set_measurement_consent(db, enabled=True)
    second = set_measurement_consent(db, enabled=True)
    assert first["current_session_id"] == second["current_session_id"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM orientation_sessions").fetchone()[0] == 1

    event = record_orientation_event(db, flow="profile_selection", event="profile_selected", profile="solo_lead")
    assert event["profile"] == "solo_lead"
    with pytest.raises(ValueError, match="profile_required"):
        record_orientation_event(db, flow="profile_selection", event="profile_selected")
    with pytest.raises(ValueError, match="flow_not_allowed"):
        record_orientation_event(db, flow="arbitrary", event="flow_started")
    with pytest.raises(ValueError, match="event_flow_mismatch"):
        record_orientation_event(db, flow="inbox", event="guidance_viewed")


def test_revoke_end_and_erase_are_auditable(tmp_path: Path) -> None:
    db = _database(tmp_path)
    set_measurement_consent(db, enabled=True)
    record_orientation_event(db, flow="inbox", event="flow_completed")

    ended = end_orientation_session(db, status="abandoned")
    assert ended["status"] == "abandoned"
    with pytest.raises(PermissionError, match="not_consented"):
        record_orientation_event(db, flow="inbox", event="ui_error")

    new_session = set_measurement_consent(db, enabled=True)
    revoked = set_measurement_consent(db, enabled=False)
    assert revoked["enabled"] is False
    with sqlite3.connect(db) as conn:
        status = conn.execute(
            "SELECT status FROM orientation_sessions WHERE id = ?",
            (new_session["current_session_id"],),
        ).fetchone()[0]
    assert status == "revoked"

    deleted = erase_orientation_measurement(db)
    assert deleted == {"deleted_events": 1, "deleted_sessions": 2, "enabled": False}
    assert orientation_summary(db)["event_count"] == 0


def test_summary_exposes_counts_and_denies_product_conclusions(tmp_path: Path) -> None:
    db = _database(tmp_path)
    set_measurement_consent(db, enabled=True)
    record_orientation_event(db, flow="accepted_plan_to_task", event="flow_started")
    record_orientation_event(db, flow="accepted_plan_to_task", event="flow_completed")

    summary = orientation_summary(db)

    assert summary["event_count"] == 2
    assert summary["flows"]["accepted_plan_to_task"] == {
        "flow_completed": 1,
        "flow_started": 1,
    }
    assert summary["privacy"] == {
        "storage": "local_project_sqlite",
        "external_transmission": False,
        "free_text_collected": False,
        "issue_or_workspace_ids_collected": False,
        "event_allowlist": [
            "flow_abandoned",
            "flow_completed",
            "flow_started",
            "guidance_viewed",
            "profile_selected",
            "ui_error",
        ],
    }
    assert summary["interpretation"]["conclusion_allowed"] is False
    assert "clarity" in summary["interpretation"]["constructs_not_measured"]


def test_orientation_api_rejects_unconsented_and_extra_content(tmp_path: Path) -> None:
    utils.set_current_workspace(tmp_path)
    _database(tmp_path)
    client = TestClient(app, raise_server_exceptions=True)

    denied = client.post(
        "/api/orientation-measurement/events",
        json={"flow": "inbox", "event": "flow_started"},
    )
    assert denied.status_code == 409

    assert client.post(
        "/api/orientation-measurement/consent", json={"enabled": True}
    ).status_code == 200
    extra = client.post(
        "/api/orientation-measurement/events",
        json={"flow": "inbox", "event": "flow_started", "title": "private task"},
    )
    assert extra.status_code == 422

    accepted = client.post(
        "/api/orientation-measurement/events",
        json={"flow": "inbox", "event": "flow_completed"},
    )
    assert accepted.status_code == 200
    summary = client.get("/api/orientation-measurement")
    assert summary.status_code == 200
    assert summary.json()["event_count"] == 1
    erased = client.delete("/api/orientation-measurement")
    assert erased.json()["deleted_events"] == 1
