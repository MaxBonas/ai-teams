from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aiteam.db.provider_changes import (
    claim_due_provider_checks,
    complete_provider_check,
    ensure_provider_change_schema,
    list_pending_provider_triggers,
    list_provider_events,
    provider_change_schedule_summary,
    provider_component_key,
    reconcile_provider_snapshot,
    register_provider_change_schedules,
    run_scheduled_provider_checks,
    transition_provider_event,
)
from aiteam.provider_change_detection import build_provider_snapshot
from aiteam.provider_change_intelligence import (
    build_provider_change_inventory,
)

NOW = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)
SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "aiteam" / "db" / "schema.sql"
)


def _component(surface: str) -> dict:
    return next(
        row
        for row in build_provider_change_inventory()["components"]
        if row["surface"] == surface
    )


def _snapshot(
    surface: str,
    *,
    observed_at: datetime = NOW,
    status: str = "observed",
    installed: str = "1.0.0",
    latest: str = "1.0.0",
    compatibility: dict | None = None,
    dimensions: dict | None = None,
) -> dict:
    observation = (
        {"status": status}
        if status != "observed"
        else {
            "status": status,
            "installed_version": installed,
            "latest_known_version": latest,
            "compatibility": compatibility or {},
            "dimensions": dimensions or {},
        }
    )
    return build_provider_snapshot(
        _component(surface),
        observation,
        observed_at=observed_at.isoformat(),
    )


def _model(
    model_id: str,
    *,
    context: int = 1000,
    lifecycle: str = "active",
) -> dict:
    return {
        "id": model_id,
        "aliases": [],
        "context": context,
        "tools": True,
        "structured_output": True,
        "price": "1",
        "quota": "standard",
        "lifecycle": lifecycle,
    }


def test_schema_contract_and_runtime_ensure_match(tmp_path: Path) -> None:
    db = tmp_path / "machine.db"
    ensure_provider_change_schema(db)
    expected = {
        "provider_change_snapshots",
        "provider_change_diffs",
        "provider_change_events",
        "provider_change_triggers",
        "provider_change_schedules",
    }
    with sqlite3.connect(db) as conn:
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

    assert expected <= runtime_tables
    assert expected <= schema_tables


def test_registration_is_idempotent_and_covers_inventory(
    tmp_path: Path,
) -> None:
    db = tmp_path / "machine.db"
    components = build_provider_change_inventory()["components"]

    first = register_provider_change_schedules(
        db, components, now=NOW, jitter_sec=0
    )
    second = register_provider_change_schedules(
        db, components, now=NOW, jitter_sec=0
    )
    summary = provider_change_schedule_summary(db, now=NOW)

    assert first["registered"] == 42
    assert second["registered"] == 42
    assert summary["counts"] == {
        "total": 42,
        "due": 42,
        "leased": 0,
        "backing_off": 0,
        "never_checked": 42,
    }


def test_summary_is_read_only_when_store_does_not_exist(
    tmp_path: Path,
) -> None:
    db = tmp_path / "absent.db"

    summary = provider_change_schedule_summary(db, now=NOW)

    assert summary["initialized"] is False
    assert summary["status"] == "unknown"
    assert summary["read_only"] is True
    assert not db.exists()


def test_claim_uses_lease_and_available_reader_filter(
    tmp_path: Path,
) -> None:
    db = tmp_path / "machine.db"
    components = build_provider_change_inventory()["components"][:3]
    register_provider_change_schedules(db, components, now=NOW, jitter_sec=0)
    allowed = {provider_component_key(components[1])}

    first = claim_due_provider_checks(
        db,
        now=NOW,
        limit=3,
        available_identity_keys=allowed,
    )
    second = claim_due_provider_checks(
        db,
        now=NOW,
        limit=3,
        available_identity_keys=allowed,
    )

    assert [row["identity_key"] for row in first] == sorted(allowed)
    assert second == []


def test_success_resets_backoff_and_failure_grows_exponentially(
    tmp_path: Path,
) -> None:
    db = tmp_path / "machine.db"
    component = _component("cli_package")
    key = provider_component_key(component)
    register_provider_change_schedules(
        db,
        [component],
        now=NOW,
        cadence_sec=3600,
        base_backoff_sec=60,
        max_backoff_sec=600,
        jitter_sec=0,
    )

    first = complete_provider_check(
        db, key, probe_status="offline", snapshot_sha256=None, now=NOW
    )
    second = complete_provider_check(
        db,
        key,
        probe_status="rate_limited",
        snapshot_sha256=None,
        now=NOW,
    )
    recovered = complete_provider_check(
        db,
        key,
        probe_status="observed",
        snapshot_sha256="a" * 64,
        now=NOW,
    )

    assert first["consecutive_failures"] == 1
    assert first["next_check_at"] == (NOW + timedelta(seconds=60)).isoformat()
    assert second["consecutive_failures"] == 2
    assert second["next_check_at"] == (NOW + timedelta(seconds=120)).isoformat()
    assert recovered["consecutive_failures"] == 0
    assert recovered["next_check_at"] == (
        NOW + timedelta(seconds=3600)
    ).isoformat()


def test_initial_snapshot_establishes_baseline_and_duplicate_is_noop(
    tmp_path: Path,
) -> None:
    db = tmp_path / "machine.db"
    snapshot = _snapshot("cli_package")

    first = reconcile_provider_snapshot(db, snapshot)
    duplicate = reconcile_provider_snapshot(db, snapshot)

    assert first["baseline_established"] is True
    assert first["events_created"] == 0
    assert duplicate["reason"] == "duplicate_snapshot"
    assert list_provider_events(db) == []


def test_material_model_change_creates_exact_trigger(
    tmp_path: Path,
) -> None:
    db = tmp_path / "machine.db"
    before = _snapshot(
        "model_catalog",
        dimensions={"model_id": [_model("model-a")]},
    )
    after = _snapshot(
        "model_catalog",
        observed_at=LATER,
        dimensions={"model_id": [_model("model-a", context=2000)]},
    )

    reconcile_provider_snapshot(db, before)
    result = reconcile_provider_snapshot(db, after)
    events = list_provider_events(db)
    triggers = list_pending_provider_triggers(db)

    assert result["events_created"] == 1
    assert result["triggers_created"] == 1
    assert events[0]["kind"] == "model_context_changed"
    assert triggers[0]["affected_scope"] == {
        "profile_id": after["identity"]["profile_id"],
        "component_id": after["identity"]["component_id"],
        "surface": "model_catalog",
        "dimension": "model:model-a:context",
        "model_ids": ["model-a"],
        "all_profiles": False,
        "all_models": False,
    }


def test_economic_change_does_not_create_revalidation_trigger(
    tmp_path: Path,
) -> None:
    db = tmp_path / "machine.db"
    old = _model("model-a")
    new = {**old, "price": "2"}
    reconcile_provider_snapshot(
        db,
        _snapshot("model_catalog", dimensions={"model_id": [old]}),
    )
    result = reconcile_provider_snapshot(
        db,
        _snapshot(
            "model_catalog",
            observed_at=LATER,
            dimensions={"model_id": [new]},
        ),
    )

    assert result["events_created"] == 1
    assert result["triggers_created"] == 0
    assert list_pending_provider_triggers(db) == []


def test_unavailable_events_dedupe_and_recovery_resolves(
    tmp_path: Path,
) -> None:
    db = tmp_path / "machine.db"
    first = _snapshot("cli_package", status="offline")
    second = _snapshot(
        "cli_package",
        status="offline",
        observed_at=NOW + timedelta(minutes=5),
    )
    recovered = _snapshot(
        "cli_package",
        observed_at=NOW + timedelta(minutes=10),
    )

    reconcile_provider_snapshot(db, first)
    reconcile_provider_snapshot(db, second)
    open_events = list_provider_events(db, statuses={"open"})
    reconcile_provider_snapshot(db, recovered)
    resolved = list_provider_events(db, statuses={"resolved"})

    assert len(open_events) == 1
    assert open_events[0]["occurrence_count"] == 2
    assert resolved[0]["kind"] == "observation_unavailable"


def test_event_acknowledge_snooze_expiry_and_resolve(
    tmp_path: Path,
) -> None:
    db = tmp_path / "machine.db"
    reconcile_provider_snapshot(
        db, _snapshot("cli_package", status="offline")
    )
    event = list_provider_events(db)[0]

    acknowledged = transition_provider_event(
        db, event["id"], status="acknowledged", now=NOW
    )
    snoozed = transition_provider_event(
        db,
        event["id"],
        status="snoozed",
        now=NOW,
        snoozed_until=NOW + timedelta(hours=1),
    )
    reopened = list_provider_events(
        db,
        statuses={"open"},
        now=NOW + timedelta(hours=2),
    )
    resolved = transition_provider_event(
        db, event["id"], status="resolved", now=LATER
    )

    assert acknowledged["acknowledged_at"] == NOW.isoformat()
    assert snoozed["status"] == "snoozed"
    assert reopened[0]["status"] == "open"
    assert resolved["resolved_at"] == LATER.isoformat()
    with pytest.raises(ValueError, match="terminal"):
        transition_provider_event(
            db, event["id"], status="open", now=LATER
        )


def test_resolved_material_event_reopens_and_requeues_trigger(
    tmp_path: Path,
) -> None:
    db = tmp_path / "machine.db"
    baseline = _snapshot("cli_package")
    changed = _snapshot(
        "cli_package",
        installed="1.1.0",
        latest="1.1.0",
        observed_at=LATER,
    )
    reset = _snapshot(
        "cli_package",
        observed_at=LATER + timedelta(hours=1),
    )
    repeated = _snapshot(
        "cli_package",
        installed="1.1.0",
        latest="1.1.0",
        observed_at=LATER + timedelta(hours=2),
    )
    reconcile_provider_snapshot(db, baseline)
    reconcile_provider_snapshot(db, changed)
    event = next(
        row
        for row in list_provider_events(db)
        if row["kind"] == "installed_upgraded"
    )
    transition_provider_event(
        db, event["id"], status="resolved", now=LATER
    )
    reconcile_provider_snapshot(db, reset)
    result = reconcile_provider_snapshot(db, repeated)
    reopened = next(
        row
        for row in list_provider_events(db)
        if row["id"] == event["id"]
    )

    assert result["events_reobserved"] >= 1
    assert result["triggers_created"] >= 1
    assert reopened["status"] == "open"


def test_scheduler_runs_only_registered_readers_and_persists(
    tmp_path: Path,
) -> None:
    db = tmp_path / "machine.db"
    components_list = build_provider_change_inventory()["components"][:2]
    components = {
        provider_component_key(row): row for row in components_list
    }
    chosen_key = sorted(components)[0]
    readers = {
        chosen_key: lambda: {
            "status": "observed",
            "installed_version": "1.0.0",
            "latest_known_version": "1.0.0",
        }
    }
    register_provider_change_schedules(
        db, components_list, now=NOW, jitter_sec=0
    )

    results = run_scheduled_provider_checks(
        db, components, readers, now=NOW, max_checks=5
    )
    summary = provider_change_schedule_summary(db, now=NOW)

    assert len(results) == 1
    assert results[0]["identity_key"] == chosen_key
    assert results[0]["probe_status"] == "observed"
    assert results[0]["reconciliation"]["baseline_established"] is True
    assert summary["counts"]["never_checked"] == 1


def test_invalid_reader_output_backs_off_without_leaking_payload(
    tmp_path: Path,
) -> None:
    db = tmp_path / "machine.db"
    component = _component("sdk_api")
    key = provider_component_key(component)
    register_provider_change_schedules(
        db,
        [component],
        now=NOW,
        base_backoff_sec=60,
        jitter_sec=0,
    )

    results = run_scheduled_provider_checks(
        db,
        {key: component},
        {key: lambda: {"status": "observed", "api_key": "hidden"}},
        now=NOW,
    )

    assert results[0]["probe_status"] == "failed"
    assert results[0]["snapshot_sha256"] is None
    assert results[0]["error_type"] == "ValueError"
    assert results[0]["consecutive_failures"] == 1
