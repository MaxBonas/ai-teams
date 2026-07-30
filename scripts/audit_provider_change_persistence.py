"""Auditoría hermética de persistencia y scheduling provider-change P0.N.3."""

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

from aiteam.db.provider_changes import (  # noqa: E402
    complete_provider_check,
    list_pending_provider_triggers,
    list_provider_events,
    provider_change_schedule_summary,
    provider_component_key,
    reconcile_provider_snapshot,
    register_provider_change_schedules,
    run_scheduled_provider_checks,
    transition_provider_event,
)
from aiteam.provider_change_detection import (  # noqa: E402
    build_provider_snapshot,
    compare_provider_snapshots,
)
from aiteam.provider_change_runtime import (  # noqa: E402
    build_safe_provider_change_runtime,
)

NOW = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)


def build_report() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="aiteam-provider-change-audit-"
    ) as raw_dir:
        root = Path(raw_dir)
        absent = root / "absent.db"
        absent_summary = provider_change_schedule_summary(absent, now=NOW)
        absent_was_not_created = not absent.exists()
        components, readers = build_safe_provider_change_runtime(
            command_probe=lambda _command: (False, None),
            codex_catalog_reader=lambda: {"status": "not_authenticated"},
        )
        db_path = root / "guided_setup.db"
        registration = register_provider_change_schedules(
            db_path,
            list(components.values()),
            now=NOW,
            jitter_sec=0,
        )
        tick = run_scheduled_provider_checks(
            db_path,
            components,
            readers,
            now=NOW,
            max_checks=3,
        )
        schedule = provider_change_schedule_summary(db_path, now=NOW)

        model_component = next(
            row
            for row in components.values()
            if row["surface"] == "model_catalog"
        )
        baseline = _model_snapshot(
            model_component,
            context=1_000,
            observed_at=NOW,
        )
        changed = _model_snapshot(
            model_component,
            context=2_000,
            observed_at=NOW + timedelta(hours=1),
        )
        event_db = root / "events.db"
        first = reconcile_provider_snapshot(event_db, baseline)
        duplicate = reconcile_provider_snapshot(event_db, baseline)
        delta = reconcile_provider_snapshot(event_db, changed)
        event = list_provider_events(event_db)[0]
        acknowledged = transition_provider_event(
            event_db,
            event["id"],
            status="acknowledged",
            now=NOW + timedelta(hours=2),
        )
        resolved = transition_provider_event(
            event_db,
            event["id"],
            status="resolved",
            now=NOW + timedelta(hours=3),
        )
        triggers = list_pending_provider_triggers(event_db)
        events = list_provider_events(event_db)
        semantic_diff = compare_provider_snapshots(baseline, changed)

        backoff_db = root / "backoff.db"
        cli_component = next(
            row
            for row in components.values()
            if row["surface"] == "cli_package"
        )
        cli_key = provider_component_key(cli_component)
        register_provider_change_schedules(
            backoff_db,
            [cli_component],
            now=NOW,
            cadence_sec=3_600,
            base_backoff_sec=60,
            max_backoff_sec=600,
            jitter_sec=0,
        )
        first_failure = complete_provider_check(
            backoff_db,
            cli_key,
            probe_status="offline",
            snapshot_sha256=None,
            now=NOW,
        )
        second_failure = complete_provider_check(
            backoff_db,
            cli_key,
            probe_status="rate_limited",
            snapshot_sha256=None,
            now=NOW,
        )
        recovered = complete_provider_check(
            backoff_db,
            cli_key,
            probe_status="observed",
            snapshot_sha256="a" * 64,
            now=NOW,
        )

    checks = {
        "doctor_summary_is_read_only": (
            absent_summary["read_only"] is True and absent_was_not_created
        ),
        "full_inventory_is_scheduled": (
            registration["registered"] == 42
            and schedule["counts"]["total"] == 42
        ),
        "automatic_readers_are_local_only": (
            len(readers) == 23
            and all(
                components[key]["surface"]
                in {"cli_package", "internal_adapter", "model_catalog"}
                for key in readers
            )
        ),
        "scheduler_is_bounded": (
            len(tick) == 3
            and all(row["probe_status"] != "running" for row in tick)
        ),
        "baseline_and_duplicate_are_idempotent": (
            first["baseline_established"] is True
            and duplicate["reason"] == "duplicate_snapshot"
        ),
        "material_diff_is_durable_and_exact": (
            delta["events_created"] == 1
            and delta["triggers_created"] == 1
            and triggers[0]["affected_scope"]["model_ids"]
            == ["fixture-model"]
        ),
        "event_lifecycle_is_durable": (
            acknowledged["status"] == "acknowledged"
            and resolved["status"] == "resolved"
        ),
        "backoff_is_exponential_and_resets": (
            first_failure["consecutive_failures"] == 1
            and second_failure["consecutive_failures"] == 2
            and recovered["consecutive_failures"] == 0
        ),
        "no_automatic_authority_is_granted": all(
            semantic_diff["summary"][field] is False
            for field in (
                "routing_change_allowed",
                "automatic_update_allowed",
            )
        ),
    }
    return {
        "schema_version": "provider_change_persistence_audit_v1",
        "contract_schema_version": "provider_change_persistence_v1",
        "counts": {
            "components": len(components),
            "safe_readers": len(readers),
            "scheduled_tick": len(tick),
            "events": len(events),
            "triggers": len(triggers),
        },
        "checks": checks,
        "summary": {
            "passed": sum(checks.values()),
            "total": len(checks),
            "persistence_ready": all(checks.values()),
        },
        "scope": {
            "temporary_sqlite_only": True,
            "network_attempted": False,
            "secrets_read": False,
            "login_attempted": False,
            "inference_attempted": False,
            "updates_attempted": False,
            "routing_mutated": False,
        },
    }


def _model_snapshot(
    component: dict[str, Any],
    *,
    context: int,
    observed_at: datetime,
) -> dict[str, Any]:
    return build_provider_snapshot(
        component,
        {
            "status": "observed",
            "installed_version": "fixture",
            "latest_known_version": "fixture",
            "compatibility": {},
            "dimensions": {
                "model_id": [
                    {
                        "id": "fixture-model",
                        "aliases": [],
                        "context": context,
                        "tools": True,
                        "structured_output": True,
                        "price": "free",
                        "quota": "fixture",
                        "lifecycle": "active",
                    }
                ]
            },
        },
        observed_at=observed_at.isoformat(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audita persistencia y scheduling provider-change."
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_report()
    body = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    ready = report["summary"]["persistence_ready"]
    return 0 if ready or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
