"""Entrega externa opt-in de cambios de proveedor mediante outbox durable."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiteam.provider_change_notifications import build_provider_change_inbox
from aiteam.user_config import read_secret

SCHEMA_VERSION = "provider_change_external_delivery_v1"
DESTINATION_KINDS = frozenset({"webhook"})
DELIVERY_MODES = frozenset({"urgent_and_digest", "urgent_only", "digest_only"})
SEVERITIES = ("critical", "error", "warning", "info")
_SEVERITY_RANK = {value: index for index, value in enumerate(SEVERITIES)}
_URGENT = frozenset({"critical", "error"})
_MAX_ATTEMPTS = 3

_DELIVERY_SQL = """
CREATE TABLE IF NOT EXISTS provider_change_notification_destinations (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    label TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('webhook')),
    endpoint_secret_ref TEXT NOT NULL,
    minimum_severity TEXT NOT NULL CHECK (
        minimum_severity IN ('critical', 'error', 'warning', 'info')
    ),
    delivery_mode TEXT NOT NULL CHECK (
        delivery_mode IN ('urgent_and_digest', 'urgent_only', 'digest_only')
    ),
    cooldown_sec INTEGER NOT NULL CHECK (
        cooldown_sec BETWEEN 60 AND 604800
    ),
    explicit_consent INTEGER NOT NULL CHECK (explicit_consent IN (0, 1)),
    consented_at TEXT,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    health_status TEXT NOT NULL DEFAULT 'unknown' CHECK (
        health_status IN ('unknown', 'healthy', 'unhealthy')
    ),
    health_checked_at TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_change_notification_outbox (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    event_fingerprint TEXT NOT NULL,
    severity TEXT NOT NULL,
    delivery_class TEXT NOT NULL CHECK (
        delivery_class IN ('urgent', 'digest')
    ),
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'delivering', 'delivered', 'failed', 'suppressed')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    lease_until TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    FOREIGN KEY(destination_id)
        REFERENCES provider_change_notification_destinations(id),
    UNIQUE(destination_id, event_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_provider_change_outbox_due
    ON provider_change_notification_outbox(
        status, next_attempt_at, delivery_class, created_at
    );

CREATE TABLE IF NOT EXISTS provider_change_notification_receipts (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    outbox_ids_json TEXT NOT NULL,
    delivery_class TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('health_passed', 'health_failed', 'delivered', 'failed')
    ),
    attempt INTEGER NOT NULL,
    response_code INTEGER,
    error_code TEXT,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(destination_id)
        REFERENCES provider_change_notification_destinations(id)
);
CREATE INDEX IF NOT EXISTS idx_provider_change_receipts_destination
    ON provider_change_notification_receipts(destination_id, created_at DESC);
"""

Sender = Callable[[str, dict[str, Any]], tuple[bool, int | None, str | None]]
SecretResolver = Callable[[str], str | None]


class ProviderChangeDeliveryConflictError(Exception):
    """La revisión del destino cambió desde que se mostró al owner."""


def ensure_provider_change_delivery_schema(db_path: Path) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(_connect(db_path)) as conn:
        conn.executescript(_DELIVERY_SQL)


def validate_webhook_endpoint(value: str) -> str:
    endpoint = str(value or "").strip()
    parsed = urllib.parse.urlsplit(endpoint)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError(
            "provider notification endpoint must be an HTTPS URL "
            "without inline credentials or fragment"
        )
    return endpoint


def configure_destination(
    db_path: Path,
    *,
    label: str,
    endpoint_secret_ref: str,
    minimum_severity: str = "warning",
    delivery_mode: str = "urgent_and_digest",
    cooldown_sec: int = 3600,
    explicit_consent: bool,
    destination_id: str | None = None,
    expected_revision: int | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    clean_ref = str(endpoint_secret_ref or "").strip()
    if not clean_label or len(clean_label) > 120:
        raise ValueError("provider notification destination label invalid")
    if not clean_ref.startswith("secret:"):
        raise ValueError("provider notification endpoint requires secret_ref")
    if minimum_severity not in _SEVERITY_RANK:
        raise ValueError("provider notification minimum severity invalid")
    if delivery_mode not in DELIVERY_MODES:
        raise ValueError("provider notification delivery mode invalid")
    cooldown = int(cooldown_sec)
    if cooldown < 60 or cooldown > 604800:
        raise ValueError("provider notification cooldown must be 60..604800")
    current = _as_datetime(now).isoformat()
    target_id = str(destination_id or uuid.uuid4())
    ensure_provider_change_delivery_schema(db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM provider_change_notification_destinations WHERE id = ?",
            (target_id,),
        ).fetchone()
        if existing is None:
            if expected_revision is not None:
                raise KeyError(target_id)
            conn.execute(
                """
                INSERT INTO provider_change_notification_destinations (
                    id, schema_version, label, kind, endpoint_secret_ref,
                    minimum_severity, delivery_mode, cooldown_sec,
                    explicit_consent, consented_at, enabled, health_status,
                    health_checked_at, revision, created_at, updated_at
                ) VALUES (?, ?, ?, 'webhook', ?, ?, ?, ?, ?, ?, 0, 'unknown',
                          NULL, 1, ?, ?)
                """,
                (
                    target_id,
                    SCHEMA_VERSION,
                    clean_label,
                    clean_ref,
                    minimum_severity,
                    delivery_mode,
                    cooldown,
                    int(explicit_consent),
                    current if explicit_consent else None,
                    current,
                    current,
                ),
            )
        else:
            if expected_revision != int(existing["revision"]):
                raise ProviderChangeDeliveryConflictError(target_id)
            endpoint_changed = clean_ref != existing["endpoint_secret_ref"]
            consent_changed = bool(existing["explicit_consent"]) != bool(
                explicit_consent
            )
            reset = endpoint_changed or consent_changed or not explicit_consent
            conn.execute(
                """
                UPDATE provider_change_notification_destinations
                SET label = ?, endpoint_secret_ref = ?, minimum_severity = ?,
                    delivery_mode = ?, cooldown_sec = ?,
                    explicit_consent = ?, consented_at = ?,
                    enabled = CASE WHEN ? THEN 0 ELSE enabled END,
                    health_status = CASE WHEN ? THEN 'unknown' ELSE health_status END,
                    health_checked_at = CASE WHEN ? THEN NULL ELSE health_checked_at END,
                    revision = revision + 1, updated_at = ?
                WHERE id = ?
                """,
                (
                    clean_label,
                    clean_ref,
                    minimum_severity,
                    delivery_mode,
                    cooldown,
                    int(explicit_consent),
                    current if explicit_consent else None,
                    int(reset),
                    int(reset),
                    int(reset),
                    current,
                    target_id,
                ),
            )
        conn.commit()
    return get_destination(db_path, target_id)


def list_destinations(db_path: Path) -> list[dict[str, Any]]:
    ensure_provider_change_delivery_schema(db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT * FROM provider_change_notification_destinations
            ORDER BY enabled DESC, label COLLATE NOCASE, id
            """
        ).fetchall()
        return [_public_destination(dict(row)) for row in rows]


def get_destination(db_path: Path, destination_id: str) -> dict[str, Any]:
    ensure_provider_change_delivery_schema(db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM provider_change_notification_destinations WHERE id = ?",
            (str(destination_id),),
        ).fetchone()
    if row is None:
        raise KeyError(destination_id)
    return _public_destination(dict(row))


def destination_endpoint_secret_ref(
    db_path: Path,
    destination_id: str,
) -> str:
    """Devuelve la referencia solo para fronteras backend confiables."""
    return str(_raw_destination(db_path, destination_id)["endpoint_secret_ref"])


def test_destination(
    db_path: Path,
    destination_id: str,
    *,
    expected_revision: int,
    sender: Sender | None = None,
    secret_resolver: SecretResolver = read_secret,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    current = _as_datetime(now)
    raw = _raw_destination(db_path, destination_id)
    if int(raw["revision"]) != int(expected_revision):
        raise ProviderChangeDeliveryConflictError(destination_id)
    endpoint = secret_resolver(str(raw["endpoint_secret_ref"]))
    if not endpoint:
        outcome = (False, None, "secret_unavailable")
    else:
        endpoint = validate_webhook_endpoint(endpoint)
        outcome = _safe_send(
            sender or _post_json,
            endpoint,
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "health_check",
                "title": "AI Teams · prueba de notificación",
                "summary": "Destino verificado; todavía no se ha activado.",
                "sent_at": current.isoformat(),
            },
        )
    ok, response_code, error_code = outcome
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        updated = conn.execute(
            """
            UPDATE provider_change_notification_destinations
            SET health_status = ?, health_checked_at = ?,
                enabled = CASE WHEN ? THEN enabled ELSE 0 END,
                revision = revision + 1, updated_at = ?
            WHERE id = ? AND revision = ?
            """,
            (
                "healthy" if ok else "unhealthy",
                current.isoformat(),
                int(ok),
                current.isoformat(),
                destination_id,
                expected_revision,
            ),
        )
        if updated.rowcount != 1:
            conn.rollback()
            raise ProviderChangeDeliveryConflictError(destination_id)
        _insert_receipt(
            conn,
            destination_id=destination_id,
            outbox_ids=[],
            delivery_class="health",
            status="health_passed" if ok else "health_failed",
            attempt=1,
            response_code=response_code,
            error_code=error_code,
            payload={"kind": "health_check", "ok": ok},
            now=current,
        )
        conn.commit()
    return get_destination(db_path, destination_id)


def set_destination_enabled(
    db_path: Path,
    destination_id: str,
    *,
    enabled: bool,
    expected_revision: int,
    secret_resolver: SecretResolver = read_secret,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    current = _as_datetime(now)
    raw = _raw_destination(db_path, destination_id)
    if int(raw["revision"]) != int(expected_revision):
        raise ProviderChangeDeliveryConflictError(destination_id)
    if enabled:
        if not bool(raw["explicit_consent"]):
            raise ValueError("provider notification explicit consent required")
        if raw["health_status"] != "healthy" or not raw["health_checked_at"]:
            raise ValueError("provider notification healthy destination required")
        if not secret_resolver(str(raw["endpoint_secret_ref"])):
            raise ValueError("provider notification secret unavailable")
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        updated = conn.execute(
            """
            UPDATE provider_change_notification_destinations
            SET enabled = ?, revision = revision + 1, updated_at = ?
            WHERE id = ? AND revision = ?
            """,
            (
                int(enabled),
                current.isoformat(),
                destination_id,
                expected_revision,
            ),
        )
        if updated.rowcount != 1:
            conn.rollback()
            raise ProviderChangeDeliveryConflictError(destination_id)
        conn.commit()
    return get_destination(db_path, destination_id)


def sync_provider_change_outbox(
    db_path: Path,
    *,
    now: datetime | str | None = None,
) -> dict[str, int]:
    current = _as_datetime(now)
    ensure_provider_change_delivery_schema(db_path)
    inbox = build_provider_change_inbox(
        db_path,
        now=current,
        include_snoozed=False,
        limit=500,
    )
    destinations = [
        row for row in _raw_destinations(db_path)
        if row["enabled"] and row["health_status"] == "healthy"
    ]
    inserted = suppressed = 0
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for destination in destinations:
            for notification in inbox["notifications"]:
                severity = str(notification["severity"])
                delivery_class = (
                    "urgent" if severity in _URGENT else "digest"
                )
                eligible = _destination_accepts(
                    destination, severity, delivery_class
                )
                payload = _external_payload(notification, delivery_class)
                payload_json = _canonical_json(payload)
                before = conn.total_changes
                conn.execute(
                    """
                    INSERT OR IGNORE INTO provider_change_notification_outbox (
                        id, schema_version, destination_id, case_id,
                        event_fingerprint, severity, delivery_class,
                        payload_json, payload_sha256, status, attempt_count,
                        next_attempt_at, lease_until, created_at, delivered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, ?, NULL)
                    """,
                    (
                        str(uuid.uuid4()),
                        SCHEMA_VERSION,
                        destination["id"],
                        notification["id"],
                        notification["event_fingerprint"],
                        severity,
                        delivery_class,
                        payload_json,
                        hashlib.sha256(payload_json.encode()).hexdigest(),
                        "pending" if eligible else "suppressed",
                        current.isoformat(),
                        current.isoformat(),
                    ),
                )
                if conn.total_changes > before:
                    if eligible:
                        inserted += 1
                    else:
                        suppressed += 1
                else:
                    target_status = "pending" if eligible else "suppressed"
                    changed = conn.execute(
                        """
                        UPDATE provider_change_notification_outbox
                        SET status = ?, next_attempt_at = ?
                        WHERE destination_id = ? AND event_fingerprint = ?
                          AND status IN ('pending', 'suppressed')
                          AND status != ?
                        """,
                        (
                            target_status,
                            current.isoformat(),
                            destination["id"],
                            notification["event_fingerprint"],
                            target_status,
                        ),
                    )
                    if changed.rowcount:
                        if eligible:
                            inserted += 1
                        else:
                            suppressed += 1
        conn.commit()
    return {"inserted": inserted, "suppressed": suppressed}


def deliver_provider_change_outbox(
    db_path: Path,
    *,
    sender: Sender | None = None,
    secret_resolver: SecretResolver = read_secret,
    now: datetime | str | None = None,
    limit: int = 25,
) -> dict[str, int]:
    current = _as_datetime(now)
    ensure_provider_change_delivery_schema(db_path)
    delivered = failed = deferred = 0
    for destination in _raw_destinations(db_path):
        if not destination["enabled"] or destination["health_status"] != "healthy":
            continue
        endpoint = secret_resolver(str(destination["endpoint_secret_ref"]))
        if not endpoint:
            _disable_unhealthy(db_path, destination["id"], current)
            continue
        try:
            endpoint = validate_webhook_endpoint(endpoint)
        except ValueError:
            _disable_unhealthy(db_path, destination["id"], current)
            continue
        rows = _claim_due(
            db_path,
            destination_id=destination["id"],
            now=current,
            limit=limit,
        )
        urgent = [row for row in rows if row["delivery_class"] == "urgent"]
        digest = [row for row in rows if row["delivery_class"] == "digest"]
        batches = [[row] for row in urgent]
        if digest:
            if _digest_in_cooldown(db_path, destination, current):
                _release_claims(db_path, digest, current + timedelta(
                    seconds=int(destination["cooldown_sec"])
                ))
                deferred += len(digest)
            else:
                batches.append(digest)
        for batch in batches:
            payload = _delivery_batch_payload(batch, current)
            ok, response_code, error_code = _safe_send(
                sender or _post_json, endpoint, payload
            )
            _finish_batch(
                db_path,
                destination=destination,
                rows=batch,
                ok=ok,
                response_code=response_code,
                error_code=error_code,
                payload=payload,
                now=current,
            )
            if ok:
                delivered += len(batch)
            else:
                failed += len(batch)
    return {"delivered": delivered, "failed": failed, "deferred": deferred}


def delivery_summary(db_path: Path) -> dict[str, Any]:
    ensure_provider_change_delivery_schema(db_path)
    destinations = list_destinations(db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        counts = {
            str(row["status"]): int(row["count"])
            for row in conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM provider_change_notification_outbox GROUP BY status
                """
            ).fetchall()
        }
    enabled = [row for row in destinations if row["enabled"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "external_delivery_enabled": bool(enabled),
        "external_delivery_reason": (
            "configured_opt_in_destination"
            if enabled
            else "external_channels_not_configured"
        ),
        "destinations": destinations,
        "outbox": counts,
    }


def _public_destination(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "schema_version": row["schema_version"],
        "label": row["label"],
        "kind": row["kind"],
        "endpoint_configured": bool(row["endpoint_secret_ref"]),
        "minimum_severity": row["minimum_severity"],
        "delivery_mode": row["delivery_mode"],
        "cooldown_sec": int(row["cooldown_sec"]),
        "explicit_consent": bool(row["explicit_consent"]),
        "consented_at": row["consented_at"],
        "enabled": bool(row["enabled"]),
        "health_status": row["health_status"],
        "health_checked_at": row["health_checked_at"],
        "revision": int(row["revision"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _raw_destination(db_path: Path, destination_id: str) -> dict[str, Any]:
    ensure_provider_change_delivery_schema(db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM provider_change_notification_destinations WHERE id = ?",
            (str(destination_id),),
        ).fetchone()
    if row is None:
        raise KeyError(destination_id)
    return dict(row)


def _raw_destinations(db_path: Path) -> list[dict[str, Any]]:
    ensure_provider_change_delivery_schema(db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM provider_change_notification_destinations"
            ).fetchall()
        ]


def _destination_accepts(
    destination: dict[str, Any],
    severity: str,
    delivery_class: str,
) -> bool:
    if _SEVERITY_RANK[severity] > _SEVERITY_RANK[
        str(destination["minimum_severity"])
    ]:
        return False
    mode = str(destination["delivery_mode"])
    return (
        mode == "urgent_and_digest"
        or (mode == "urgent_only" and delivery_class == "urgent")
        or (mode == "digest_only" and delivery_class == "digest")
    )


def _external_payload(
    notification: dict[str, Any],
    delivery_class: str,
) -> dict[str, Any]:
    provider = notification["provider"]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "provider_change",
        "delivery_class": delivery_class,
        "case_id": notification["id"],
        "severity": notification["severity"],
        "title": notification["title"],
        "summary": notification["summary"],
        "provider": {
            "provider_id": provider["provider_id"],
            "component_id": provider["component_id"],
            "surface": provider["surface"],
        },
        "change": {
            "kind": notification["change"]["kind"],
            "dimension": notification["change"]["dimension"],
        },
        "next_action": notification["next_action"],
        "cockpit_path": "/config?section=provider-changes",
    }


def _claim_due(
    db_path: Path,
    *,
    destination_id: str,
    now: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    lease_until = (now + timedelta(minutes=2)).isoformat()
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT * FROM provider_change_notification_outbox
            WHERE destination_id = ?
              AND (
                (status = 'pending' AND next_attempt_at <= ?)
                OR (status = 'delivering' AND lease_until <= ?)
              )
            ORDER BY CASE delivery_class WHEN 'urgent' THEN 0 ELSE 1 END,
                     created_at, id
            LIMIT ?
            """,
            (destination_id, now.isoformat(), now.isoformat(), max(1, limit)),
        ).fetchall()
        ids = [str(row["id"]) for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""
                UPDATE provider_change_notification_outbox
                SET status = 'delivering', lease_until = ?,
                    attempt_count = attempt_count + 1
                WHERE id IN ({placeholders})
                """,
                (lease_until, *ids),
            )
        conn.commit()
    return [
        {**dict(row), "attempt_count": int(row["attempt_count"]) + 1}
        for row in rows
    ]


def _release_claims(
    db_path: Path,
    rows: list[dict[str, Any]],
    next_attempt: datetime,
) -> None:
    if not rows:
        return
    ids = [str(row["id"]) for row in rows]
    placeholders = ",".join("?" for _ in ids)
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            f"""
            UPDATE provider_change_notification_outbox
            SET status = 'pending', lease_until = NULL,
                attempt_count = MAX(0, attempt_count - 1),
                next_attempt_at = ?
            WHERE id IN ({placeholders})
            """,
            (next_attempt.isoformat(), *ids),
        )
        conn.commit()


def _finish_batch(
    db_path: Path,
    *,
    destination: dict[str, Any],
    rows: list[dict[str, Any]],
    ok: bool,
    response_code: int | None,
    error_code: str | None,
    payload: dict[str, Any],
    now: datetime,
) -> None:
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            attempts = int(row["attempt_count"])
            terminal = ok or attempts >= _MAX_ATTEMPTS
            status = "delivered" if ok else ("failed" if terminal else "pending")
            retry_at = now + timedelta(seconds=min(3600, 30 * (2 ** attempts)))
            conn.execute(
                """
                UPDATE provider_change_notification_outbox
                SET status = ?, next_attempt_at = ?, lease_until = NULL,
                    delivered_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    retry_at.isoformat(),
                    now.isoformat() if ok else None,
                    row["id"],
                ),
            )
        _insert_receipt(
            conn,
            destination_id=destination["id"],
            outbox_ids=[str(row["id"]) for row in rows],
            delivery_class=str(rows[0]["delivery_class"]),
            status="delivered" if ok else "failed",
            attempt=max(int(row["attempt_count"]) for row in rows),
            response_code=response_code,
            error_code=error_code,
            payload=payload,
            now=now,
        )
        terminal_failure = not ok and all(
            int(row["attempt_count"]) >= _MAX_ATTEMPTS for row in rows
        )
        if terminal_failure:
            conn.execute(
                """
                UPDATE provider_change_notification_destinations
                SET health_status = 'unhealthy', enabled = 0,
                    health_checked_at = ?, revision = revision + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (now.isoformat(), now.isoformat(), destination["id"]),
            )
        conn.commit()


def _insert_receipt(
    conn: sqlite3.Connection,
    *,
    destination_id: str,
    outbox_ids: list[str],
    delivery_class: str,
    status: str,
    attempt: int,
    response_code: int | None,
    error_code: str | None,
    payload: dict[str, Any],
    now: datetime,
) -> None:
    canonical = _canonical_json(payload)
    conn.execute(
        """
        INSERT INTO provider_change_notification_receipts (
            id, schema_version, destination_id, outbox_ids_json,
            delivery_class, status, attempt, response_code, error_code,
            content_sha256, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            SCHEMA_VERSION,
            destination_id,
            _canonical_json(outbox_ids),
            delivery_class,
            status,
            attempt,
            response_code,
            _safe_error_code(error_code),
            hashlib.sha256(canonical.encode()).hexdigest(),
            now.isoformat(),
        ),
    )


def _digest_in_cooldown(
    db_path: Path,
    destination: dict[str, Any],
    now: datetime,
) -> bool:
    threshold = (now - timedelta(seconds=int(destination["cooldown_sec"]))).isoformat()
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM provider_change_notification_receipts
            WHERE destination_id = ? AND delivery_class = 'digest'
              AND status = 'delivered' AND created_at > ?
            LIMIT 1
            """,
            (destination["id"], threshold),
        ).fetchone()
    return row is not None


def _delivery_batch_payload(
    rows: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    items = [json.loads(str(row["payload_json"])) for row in rows]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "provider_change_digest" if len(items) > 1 else "provider_change",
        "delivery_class": rows[0]["delivery_class"],
        "count": len(items),
        "items": items,
        "sent_at": now.isoformat(),
    }


def _disable_unhealthy(
    db_path: Path,
    destination_id: str,
    now: datetime,
) -> None:
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE provider_change_notification_destinations
            SET enabled = 0, health_status = 'unhealthy',
                health_checked_at = ?, revision = revision + 1, updated_at = ?
            WHERE id = ?
            """,
            (now.isoformat(), now.isoformat(), destination_id),
        )
        conn.commit()


def _post_json(
    endpoint: str,
    payload: dict[str, Any],
) -> tuple[bool, int | None, str | None]:
    body = _canonical_json(payload).encode("utf-8")
    request = urllib.request.Request(
        validate_webhook_endpoint(endpoint),
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "AI-Teams/provider-change-notifier",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            code = int(response.status)
        return 200 <= code < 300, code, None if 200 <= code < 300 else "http_error"
    except urllib.error.HTTPError as exc:
        return False, int(exc.code), "http_error"
    except urllib.error.URLError:
        return False, None, "network_error"
    except TimeoutError:
        return False, None, "timeout"


def _safe_send(
    sender: Sender,
    endpoint: str,
    payload: dict[str, Any],
) -> tuple[bool, int | None, str | None]:
    try:
        return sender(endpoint, payload)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False, None, "delivery_error"


def _safe_error_code(value: str | None) -> str | None:
    return value if value in {
        None,
        "secret_unavailable",
        "http_error",
        "network_error",
        "timeout",
    } else "delivery_error"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(db_path), timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _as_datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
