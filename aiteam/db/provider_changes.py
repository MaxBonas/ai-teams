"""Persistencia y scheduling durable de cambios de proveedor."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiteam.provider_change_detection import (
    compare_provider_snapshots,
    run_read_only_probe,
    validate_provider_snapshot,
)

SCHEMA_VERSION = "provider_change_persistence_v1"
EVENT_STATUSES = frozenset(
    {"open", "acknowledged", "snoozed", "resolved"}
)
_UNAVAILABLE = frozenset(
    {"offline", "rate_limited", "auth_required", "failed"}
)

_ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS provider_change_snapshots (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    profile_id TEXT,
    channel_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    component_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    probe_status TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_change_snapshots_identity
    ON provider_change_snapshots(identity_key, observed_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS provider_change_diffs (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    previous_snapshot_sha256 TEXT NOT NULL,
    current_snapshot_sha256 TEXT NOT NULL,
    decision TEXT NOT NULL,
    diff_sha256 TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_change_diffs_identity
    ON provider_change_diffs(identity_key, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS provider_change_events (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    identity_key TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    profile_id TEXT,
    channel_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    component_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    kind TEXT NOT NULL,
    dimension TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('open', 'acknowledged', 'snoozed', 'resolved')),
    owner TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1
        CHECK (occurrence_count >= 1),
    snoozed_until TEXT,
    acknowledged_at TEXT,
    resolved_at TEXT,
    source_diff_sha256 TEXT NOT NULL,
    change_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_change_events_attention
    ON provider_change_events(status, severity, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_provider_change_events_identity
    ON provider_change_events(identity_key, status, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS provider_change_triggers (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    event_fingerprint TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'consumed', 'dismissed')),
    affected_scope_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    consumed_at TEXT,
    UNIQUE(event_fingerprint, trigger_type)
);
CREATE INDEX IF NOT EXISTS idx_provider_change_triggers_status
    ON provider_change_triggers(status, created_at, id);

CREATE TABLE IF NOT EXISTS provider_change_schedules (
    identity_key TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    profile_id TEXT,
    channel_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    component_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    cadence_sec INTEGER NOT NULL CHECK (cadence_sec >= 60),
    base_backoff_sec INTEGER NOT NULL CHECK (base_backoff_sec >= 1),
    max_backoff_sec INTEGER NOT NULL CHECK (max_backoff_sec >= base_backoff_sec),
    jitter_sec INTEGER NOT NULL CHECK (jitter_sec >= 0),
    next_check_at TEXT NOT NULL,
    last_checked_at TEXT,
    last_probe_status TEXT,
    last_snapshot_sha256 TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
        CHECK (consecutive_failures >= 0),
    lease_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_change_schedules_due
    ON provider_change_schedules(next_check_at, lease_until, identity_key);
"""


def ensure_provider_change_schema(db_path: Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(_connect(db_path)) as conn:
        conn.executescript(_ENSURE_SQL)


def provider_component_key(component: Mapping[str, Any]) -> str:
    values = (
        str(component.get("scope_id") or "").strip(),
        str(component.get("surface") or "").strip(),
        str(component.get("component_id") or "").strip(),
    )
    if not all(values):
        raise ValueError("provider component identity is incomplete")
    return "|".join(values)


def register_provider_change_schedules(
    db_path: Path,
    components: list[dict[str, Any]],
    *,
    now: datetime | str | None = None,
    cadence_sec: int = 86_400,
    base_backoff_sec: int = 300,
    max_backoff_sec: int = 21_600,
    jitter_sec: int = 300,
) -> dict[str, Any]:
    current = _coerce_datetime(now) or datetime.now(timezone.utc)
    if cadence_sec < 60 or base_backoff_sec < 1:
        raise ValueError("provider schedule cadence/backoff is invalid")
    if max_backoff_sec < base_backoff_sec or jitter_sec < 0:
        raise ValueError("provider schedule backoff/jitter is invalid")
    ensure_provider_change_schema(db_path)
    inserted = 0
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for component in components:
                identity_key = provider_component_key(component)
                identity = _component_identity(component)
                cursor = conn.execute(
                    """
                    INSERT INTO provider_change_schedules (
                        identity_key, schema_version, scope_id, profile_id,
                        channel_id, provider_id, component_id, surface,
                        cadence_sec, base_backoff_sec, max_backoff_sec,
                        jitter_sec, next_check_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(identity_key) DO UPDATE SET
                        schema_version = excluded.schema_version,
                        scope_id = excluded.scope_id,
                        profile_id = excluded.profile_id,
                        channel_id = excluded.channel_id,
                        provider_id = excluded.provider_id,
                        component_id = excluded.component_id,
                        surface = excluded.surface,
                        cadence_sec = excluded.cadence_sec,
                        base_backoff_sec = excluded.base_backoff_sec,
                        max_backoff_sec = excluded.max_backoff_sec,
                        jitter_sec = excluded.jitter_sec,
                        updated_at = excluded.updated_at
                    """,
                    (
                        identity_key,
                        SCHEMA_VERSION,
                        identity["scope_id"],
                        identity["profile_id"],
                        identity["channel_id"],
                        identity["provider_id"],
                        identity["component_id"],
                        identity["surface"],
                        int(cadence_sec),
                        int(base_backoff_sec),
                        int(max_backoff_sec),
                        int(jitter_sec),
                        current.isoformat(),
                        current.isoformat(),
                        current.isoformat(),
                    ),
                )
                inserted += cursor.rowcount == 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {
        "schema_version": SCHEMA_VERSION,
        "registered": len(components),
        "inserted_or_updated": inserted,
        "next_due_at": current.isoformat(),
    }


def claim_due_provider_checks(
    db_path: Path,
    *,
    now: datetime | str | None = None,
    limit: int = 1,
    lease_sec: int = 300,
    available_identity_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    current = _coerce_datetime(now) or datetime.now(timezone.utc)
    bounded = max(1, min(int(limit), 50))
    if lease_sec < 1:
        raise ValueError("provider schedule lease must be positive")
    ensure_provider_change_schema(db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            params: list[Any] = [
                current.isoformat(),
                current.isoformat(),
            ]
            source_filter = ""
            if available_identity_keys is not None:
                keys = sorted(available_identity_keys)
                if not keys:
                    conn.execute("COMMIT")
                    return []
                placeholders = ",".join("?" for _ in keys)
                source_filter = f" AND identity_key IN ({placeholders})"
                params.extend(keys)
            params.append(bounded)
            rows = conn.execute(
                f"""
                SELECT * FROM provider_change_schedules
                WHERE next_check_at <= ?
                  AND (lease_until IS NULL OR lease_until <= ?)
                  {source_filter}
                ORDER BY next_check_at ASC, identity_key ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
            lease_until = (current + timedelta(seconds=lease_sec)).isoformat()
            for row in rows:
                conn.execute(
                    """
                    UPDATE provider_change_schedules
                    SET lease_until = ?, updated_at = ?
                    WHERE identity_key = ?
                    """,
                    (
                        lease_until,
                        current.isoformat(),
                        row["identity_key"],
                    ),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return [
        {
            **dict(row),
            "lease_until": lease_until,
        }
        for row in rows
    ]


def complete_provider_check(
    db_path: Path,
    identity_key: str,
    *,
    probe_status: str,
    snapshot_sha256: str | None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    if probe_status not in {"observed", *_UNAVAILABLE}:
        raise ValueError("provider schedule probe status drift")
    current = _coerce_datetime(now) or datetime.now(timezone.utc)
    ensure_provider_change_schema(db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                """
                SELECT * FROM provider_change_schedules
                WHERE identity_key = ?
                """,
                (identity_key,),
            ).fetchone()
            if row is None:
                raise KeyError(identity_key)
            success = probe_status == "observed"
            failures = 0 if success else int(row["consecutive_failures"]) + 1
            delay = (
                int(row["cadence_sec"])
                if success
                else min(
                    int(row["max_backoff_sec"]),
                    int(row["base_backoff_sec"])
                    * (2 ** min(failures - 1, 16)),
                )
            )
            jitter = _deterministic_jitter(
                identity_key,
                failures if failures else int(current.timestamp()),
                int(row["jitter_sec"]),
            )
            next_check = current + timedelta(seconds=delay + jitter)
            conn.execute(
                """
                UPDATE provider_change_schedules
                SET next_check_at = ?,
                    last_checked_at = ?,
                    last_probe_status = ?,
                    last_snapshot_sha256 = COALESCE(?, last_snapshot_sha256),
                    consecutive_failures = ?,
                    lease_until = NULL,
                    updated_at = ?
                WHERE identity_key = ?
                """,
                (
                    next_check.isoformat(),
                    current.isoformat(),
                    probe_status,
                    snapshot_sha256,
                    failures,
                    current.isoformat(),
                    identity_key,
                ),
            )
            updated = conn.execute(
                """
                SELECT * FROM provider_change_schedules
                WHERE identity_key = ?
                """,
                (identity_key,),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return dict(updated)


def reconcile_provider_snapshot(
    db_path: Path,
    snapshot: Mapping[str, Any],
    *,
    owner: str = "AI Teams maintainer",
) -> dict[str, Any]:
    validate_provider_snapshot(snapshot)
    identity_key = _snapshot_identity_key(snapshot)
    now_iso = str(snapshot["observed_at"])
    ensure_provider_change_schema(db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                """
                SELECT id FROM provider_change_snapshots
                WHERE snapshot_sha256 = ?
                """,
                (snapshot["snapshot_sha256"],),
            ).fetchone()
            snapshot_inserted = existing is None
            if not snapshot_inserted:
                conn.execute("COMMIT")
                return {
                    "schema_version": SCHEMA_VERSION,
                    "snapshot_inserted": False,
                    "diff_inserted": False,
                    "events_created": 0,
                    "events_reobserved": 0,
                    "triggers_created": 0,
                    "baseline_established": False,
                    "reason": "duplicate_snapshot",
                }
            _insert_snapshot(conn, snapshot, identity_key)
            baseline_row = _baseline_snapshot_row(
                conn,
                identity_key=identity_key,
                current_hash=str(snapshot["snapshot_sha256"]),
                observed_only=snapshot["probe_status"] == "observed",
            )
            if (
                snapshot["probe_status"] == "observed"
                and baseline_row is None
            ):
                _resolve_unavailable_events(
                    conn, identity_key=identity_key, now_iso=now_iso
                )
                conn.execute("COMMIT")
                return {
                    "schema_version": SCHEMA_VERSION,
                    "snapshot_inserted": snapshot_inserted,
                    "diff_inserted": False,
                    "events_created": 0,
                    "events_reobserved": 0,
                    "triggers_created": 0,
                    "baseline_established": True,
                }
            baseline = (
                json.loads(baseline_row["payload_json"])
                if baseline_row is not None
                else snapshot
            )
            diff = compare_provider_snapshots(baseline, snapshot)
            diff_inserted = _insert_diff(conn, diff, identity_key, now_iso)
            created = 0
            reobserved = 0
            triggers = 0
            for change in diff["changes"]:
                event_result = _upsert_event(
                    conn,
                    snapshot=snapshot,
                    diff=diff,
                    change=change,
                    identity_key=identity_key,
                    owner=owner,
                    now_iso=now_iso,
                )
                created += event_result["created"]
                reobserved += event_result["reobserved"]
                if (
                    (
                        event_result["created"]
                        or event_result["reopened"]
                    )
                    and change.get("calibration_impact") is True
                ):
                    triggers += _insert_trigger(
                        conn,
                        fingerprint=event_result["fingerprint"],
                        snapshot=snapshot,
                        change=change,
                        now_iso=now_iso,
                    )
            if snapshot["probe_status"] == "observed":
                _resolve_unavailable_events(
                    conn, identity_key=identity_key, now_iso=now_iso
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_inserted": snapshot_inserted,
        "diff_inserted": diff_inserted,
        "events_created": created,
        "events_reobserved": reobserved,
        "triggers_created": triggers,
        "baseline_established": False,
        "diff": diff,
    }


def transition_provider_event(
    db_path: Path,
    event_id: str,
    *,
    status: str,
    now: datetime | str | None = None,
    snoozed_until: datetime | str | None = None,
) -> dict[str, Any]:
    if status not in EVENT_STATUSES:
        raise ValueError("provider event status drift")
    current = _coerce_datetime(now) or datetime.now(timezone.utc)
    snooze = _coerce_datetime(snoozed_until)
    if status == "snoozed" and (snooze is None or snooze <= current):
        raise ValueError("provider event snooze must end in the future")
    if status != "snoozed" and snooze is not None:
        raise ValueError("snoozed_until is only valid for snoozed events")
    ensure_provider_change_schema(db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT * FROM provider_change_events WHERE id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            raise KeyError(event_id)
        if row["status"] == "resolved" and status != "resolved":
            raise ValueError("resolved provider events are terminal")
        acknowledged_at = (
            current.isoformat()
            if status == "acknowledged"
            else row["acknowledged_at"]
        )
        resolved_at = (
            current.isoformat()
            if status == "resolved"
            else None
        )
        conn.execute(
            """
            UPDATE provider_change_events
            SET status = ?,
                snoozed_until = ?,
                acknowledged_at = ?,
                resolved_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                snooze.isoformat() if snooze else None,
                acknowledged_at,
                resolved_at,
                current.isoformat(),
                event_id,
            ),
        )
        updated = conn.execute(
            "SELECT * FROM provider_change_events WHERE id = ?",
            (event_id,),
        ).fetchone()
    return _decode_event(updated)


def list_provider_events(
    db_path: Path,
    *,
    statuses: set[str] | None = None,
    limit: int = 100,
    now: datetime | str | None = None,
) -> list[dict[str, Any]]:
    requested = statuses or set(EVENT_STATUSES)
    if not requested <= EVENT_STATUSES:
        raise ValueError("provider event status filter drift")
    current = _coerce_datetime(now) or datetime.now(timezone.utc)
    if not Path(db_path).exists():
        return []
    placeholders = ",".join("?" for _ in sorted(requested))
    with contextlib.closing(_connect(db_path)) as conn:
        if not _table_exists(conn, "provider_change_events"):
            return []
        conn.execute(
            """
            UPDATE provider_change_events
            SET status = 'open', snoozed_until = NULL, updated_at = ?
            WHERE status = 'snoozed'
              AND snoozed_until <= ?
            """,
            (current.isoformat(), current.isoformat()),
        )
        rows = conn.execute(
            f"""
            SELECT * FROM provider_change_events
            WHERE status IN ({placeholders})
            ORDER BY
                CASE severity
                    WHEN 'critical' THEN 0
                    WHEN 'error' THEN 1
                    WHEN 'warning' THEN 2
                    ELSE 3
                END,
                last_seen_at DESC,
                id ASC
            LIMIT ?
            """,
            [*sorted(requested), max(1, min(int(limit), 500))],
        ).fetchall()
    return [_decode_event(row) for row in rows]


def list_pending_provider_triggers(
    db_path: Path,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not Path(db_path).exists():
        return []
    with contextlib.closing(_connect(db_path)) as conn:
        if not _table_exists(conn, "provider_change_triggers"):
            return []
        rows = conn.execute(
            """
            SELECT * FROM provider_change_triggers
            WHERE status = 'pending'
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [_decode_trigger(row) for row in rows]


def provider_change_schedule_summary(
    db_path: Path,
    *,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    current = _coerce_datetime(now) or datetime.now(timezone.utc)
    path = Path(db_path)
    if not path.exists():
        return _empty_schedule_summary()
    with contextlib.closing(_connect_readonly(path)) as conn:
        if not _table_exists(conn, "provider_change_schedules"):
            return _empty_schedule_summary()
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN next_check_at <= ? AND
                    (lease_until IS NULL OR lease_until <= ?) THEN 1 ELSE 0 END)
                    AS due,
                SUM(CASE WHEN lease_until > ? THEN 1 ELSE 0 END) AS leased,
                SUM(CASE WHEN consecutive_failures > 0 THEN 1 ELSE 0 END)
                    AS backing_off,
                SUM(CASE WHEN last_probe_status IS NULL THEN 1 ELSE 0 END)
                    AS never_checked
            FROM provider_change_schedules
            """,
            (
                current.isoformat(),
                current.isoformat(),
                current.isoformat(),
            ),
        ).fetchone()
    return {
        "schema_version": SCHEMA_VERSION,
        "initialized": True,
        "status": (
            "attention_required"
            if int(row["backing_off"] or 0) > 0
            else "due"
            if int(row["due"] or 0) > 0
            else "current"
        ),
        "counts": {
            "total": int(row["total"] or 0),
            "due": int(row["due"] or 0),
            "leased": int(row["leased"] or 0),
            "backing_off": int(row["backing_off"] or 0),
            "never_checked": int(row["never_checked"] or 0),
        },
        "read_only": True,
    }


def run_scheduled_provider_checks(
    db_path: Path,
    components: Mapping[str, Mapping[str, Any]],
    readers: Mapping[str, Callable[[], Mapping[str, Any]]],
    *,
    now: datetime | str | None = None,
    max_checks: int = 1,
) -> list[dict[str, Any]]:
    current = _coerce_datetime(now) or datetime.now(timezone.utc)
    available = set(components) & set(readers)
    claimed = claim_due_provider_checks(
        db_path,
        now=current,
        limit=max_checks,
        available_identity_keys=available,
    )
    results: list[dict[str, Any]] = []
    for schedule in claimed:
        identity_key = str(schedule["identity_key"])
        try:
            snapshot = run_read_only_probe(
                components[identity_key],
                readers[identity_key],
                observed_at=current.isoformat(),
            )
            reconciliation = reconcile_provider_snapshot(
                db_path, snapshot
            )
            probe_status = str(snapshot["probe_status"])
            snapshot_hash = str(snapshot["snapshot_sha256"])
            error_type = None
        except Exception as exc:
            reconciliation = None
            probe_status = "failed"
            snapshot_hash = None
            error_type = type(exc).__name__
        updated = complete_provider_check(
            db_path,
            identity_key,
            probe_status=probe_status,
            snapshot_sha256=snapshot_hash,
            now=current,
        )
        results.append(
            {
                "identity_key": identity_key,
                "probe_status": probe_status,
                "snapshot_sha256": snapshot_hash,
                "reconciliation": reconciliation,
                "error_type": error_type,
                "next_check_at": updated["next_check_at"],
                "consecutive_failures": updated[
                    "consecutive_failures"
                ],
            }
        )
    return results


def _insert_snapshot(
    conn: sqlite3.Connection,
    snapshot: Mapping[str, Any],
    identity_key: str,
) -> None:
    identity = snapshot["identity"]
    conn.execute(
        """
        INSERT INTO provider_change_snapshots (
            id, schema_version, identity_key, scope_id, profile_id,
            channel_id, provider_id, component_id, surface, probe_status,
            observed_at, snapshot_sha256, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            SCHEMA_VERSION,
            identity_key,
            identity["scope_id"],
            identity["profile_id"],
            identity["channel_id"],
            identity["provider_id"],
            identity["component_id"],
            identity["surface"],
            snapshot["probe_status"],
            snapshot["observed_at"],
            snapshot["snapshot_sha256"],
            _json(snapshot),
            snapshot["observed_at"],
        ),
    )


def _baseline_snapshot_row(
    conn: sqlite3.Connection,
    *,
    identity_key: str,
    current_hash: str,
    observed_only: bool,
) -> sqlite3.Row | None:
    observed_filter = "AND probe_status = 'observed'" if observed_only else ""
    return conn.execute(
        f"""
        SELECT * FROM provider_change_snapshots
        WHERE identity_key = ?
          AND snapshot_sha256 != ?
          {observed_filter}
        ORDER BY observed_at DESC, id DESC
        LIMIT 1
        """,
        (identity_key, current_hash),
    ).fetchone()


def _insert_diff(
    conn: sqlite3.Connection,
    diff: Mapping[str, Any],
    identity_key: str,
    now_iso: str,
) -> bool:
    cursor = conn.execute(
        """
        INSERT INTO provider_change_diffs (
            id, schema_version, identity_key, previous_snapshot_sha256,
            current_snapshot_sha256, decision, diff_sha256, payload_json,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(diff_sha256) DO NOTHING
        """,
        (
            str(uuid.uuid4()),
            SCHEMA_VERSION,
            identity_key,
            diff["previous_snapshot_sha256"],
            diff["current_snapshot_sha256"],
            diff["summary"]["decision"],
            diff["diff_sha256"],
            _json(diff),
            now_iso,
        ),
    )
    return cursor.rowcount == 1


def _upsert_event(
    conn: sqlite3.Connection,
    *,
    snapshot: Mapping[str, Any],
    diff: Mapping[str, Any],
    change: Mapping[str, Any],
    identity_key: str,
    owner: str,
    now_iso: str,
) -> dict[str, Any]:
    identity = snapshot["identity"]
    fingerprint = _sha256(
        {
            "identity": identity,
            "kind": change["kind"],
            "dimension": change["dimension"],
            "before": change["before"],
            "after": change["after"],
        }
    )
    existing = conn.execute(
        """
        SELECT id, status FROM provider_change_events
        WHERE fingerprint = ?
        """,
        (fingerprint,),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO provider_change_events (
                id, schema_version, fingerprint, identity_key, scope_id,
                profile_id, channel_id, provider_id, component_id, surface,
                kind, dimension, severity, status, owner, first_seen_at,
                last_seen_at, source_diff_sha256, change_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?,
                      ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                SCHEMA_VERSION,
                fingerprint,
                identity_key,
                identity["scope_id"],
                identity["profile_id"],
                identity["channel_id"],
                identity["provider_id"],
                identity["component_id"],
                identity["surface"],
                change["kind"],
                change["dimension"],
                change["severity"],
                owner,
                now_iso,
                now_iso,
                diff["diff_sha256"],
                _json(change),
                now_iso,
            ),
        )
        return {
            "fingerprint": fingerprint,
            "created": 1,
            "reobserved": 0,
            "reopened": 0,
        }
    reopened = existing["status"] == "resolved"
    conn.execute(
        """
        UPDATE provider_change_events
        SET last_seen_at = ?,
            occurrence_count = occurrence_count + 1,
            status = CASE WHEN status = 'resolved' THEN 'open' ELSE status END,
            resolved_at = CASE WHEN status = 'resolved' THEN NULL
                               ELSE resolved_at END,
            source_diff_sha256 = ?,
            change_json = ?,
            updated_at = ?
        WHERE fingerprint = ?
        """,
        (
            now_iso,
            diff["diff_sha256"],
            _json(change),
            now_iso,
            fingerprint,
        ),
    )
    return {
        "fingerprint": fingerprint,
        "created": 0,
        "reobserved": 1,
        "reopened": int(reopened),
    }


def _insert_trigger(
    conn: sqlite3.Connection,
    *,
    fingerprint: str,
    snapshot: Mapping[str, Any],
    change: Mapping[str, Any],
    now_iso: str,
) -> int:
    affected_scope = _affected_scope(snapshot, change)
    cursor = conn.execute(
        """
        INSERT INTO provider_change_triggers (
            id, schema_version, event_fingerprint, trigger_type, status,
            affected_scope_json, created_at
        ) VALUES (?, ?, ?, 'evidence_revalidation', 'pending', ?, ?)
        ON CONFLICT(event_fingerprint, trigger_type) DO UPDATE SET
            status = 'pending',
            affected_scope_json = excluded.affected_scope_json,
            created_at = excluded.created_at,
            consumed_at = NULL
        """,
        (
            str(uuid.uuid4()),
            SCHEMA_VERSION,
            fingerprint,
            _json(affected_scope),
            now_iso,
        ),
    )
    return cursor.rowcount == 1


def _affected_scope(
    snapshot: Mapping[str, Any],
    change: Mapping[str, Any],
) -> dict[str, Any]:
    identity = snapshot["identity"]
    model_ids: list[str] = []
    if str(change.get("kind") or "").startswith("model_"):
        dimension = str(change.get("dimension") or "")
        if dimension.startswith("model:"):
            parts = dimension.split(":", 2)
            if len(parts) == 3 and parts[1]:
                model_ids.append(parts[1])
        for value in (change.get("before"), change.get("after")):
            if isinstance(value, str) and value:
                model_ids.append(value)
    return {
        "profile_id": identity["profile_id"],
        "component_id": identity["component_id"],
        "surface": identity["surface"],
        "dimension": change["dimension"],
        "model_ids": sorted(set(model_ids)),
        "all_profiles": False,
        "all_models": False,
    }


def _resolve_unavailable_events(
    conn: sqlite3.Connection,
    *,
    identity_key: str,
    now_iso: str,
) -> None:
    conn.execute(
        """
        UPDATE provider_change_events
        SET status = 'resolved',
            resolved_at = ?,
            snoozed_until = NULL,
            updated_at = ?
        WHERE identity_key = ?
          AND kind = 'observation_unavailable'
          AND status != 'resolved'
        """,
        (now_iso, now_iso, identity_key),
    )


def _component_identity(component: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: component.get(key)
        for key in (
            "scope_id",
            "profile_id",
            "channel_id",
            "provider_id",
            "component_id",
            "surface",
        )
    }


def _snapshot_identity_key(snapshot: Mapping[str, Any]) -> str:
    return provider_component_key(snapshot["identity"])


def _decode_event(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["change"] = json.loads(result.pop("change_json"))
    return result


def _decode_trigger(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["affected_scope"] = json.loads(
        result.pop("affected_scope_json")
    )
    return result


def _empty_schedule_summary() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "initialized": False,
        "status": "unknown",
        "counts": {
            "total": 0,
            "due": 0,
            "leased": 0,
            "backing_off": 0,
            "never_checked": 0,
        },
        "read_only": True,
    }


def _deterministic_jitter(
    identity_key: str,
    attempt: int,
    jitter_sec: int,
) -> int:
    if jitter_sec <= 0:
        return 0
    digest = hashlib.sha256(
        f"{identity_key}:{attempt}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:4], "big") % (jitter_sec + 1)


def _coerce_datetime(
    value: datetime | str | None,
) -> datetime | None:
    if value is None:
        return None
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("provider persistence timestamp must have timezone")
    return parsed.astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table_name,),
        ).fetchone()
        is not None
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(db_path), timeout=20.0, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn
