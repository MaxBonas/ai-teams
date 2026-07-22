"""Medición local, consentida y sin contenido de usuario para orientación UI."""
from __future__ import annotations

import contextlib
import sqlite3
import uuid
from pathlib import Path
from typing import Any

FLOWS = frozenset({"inbox", "profile_selection", "accepted_plan_to_task"})
EVENTS = frozenset({
    "flow_started",
    "flow_completed",
    "flow_abandoned",
    "profile_selected",
    "ui_error",
})
PROFILES = frozenset({"solo_lead", "lead_quorum", "full_team"})
SESSION_END_STATUSES = frozenset({"completed", "abandoned"})


def measurement_state(db_path: Path) -> dict[str, Any]:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT enabled, current_session_id, consented_at, revoked_at FROM orientation_measurement WHERE id = 1"
        ).fetchone()
    return _state(row)


def set_measurement_consent(db_path: Path, *, enabled: bool) -> dict[str, Any]:
    """Activa/revoca consentimiento; activar garantiza una sesión viva idempotente."""
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT enabled, current_session_id FROM orientation_measurement WHERE id = 1"
        ).fetchone()
        current_session_id = str(row["current_session_id"] or "") if row else ""
        current_active = bool(current_session_id and conn.execute(
            "SELECT 1 FROM orientation_sessions WHERE id = ? AND status = 'active'",
            (current_session_id,),
        ).fetchone())
        if enabled:
            if not current_active:
                current_session_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO orientation_sessions (id, status) VALUES (?, 'active')",
                    (current_session_id,),
                )
            conn.execute(
                """
                INSERT INTO orientation_measurement (id, enabled, current_session_id, consented_at, revoked_at)
                VALUES (1, 1, ?, CURRENT_TIMESTAMP, NULL)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = 1,
                    current_session_id = excluded.current_session_id,
                    consented_at = CASE
                        WHEN orientation_measurement.enabled = 0 THEN CURRENT_TIMESTAMP
                        ELSE COALESCE(orientation_measurement.consented_at, CURRENT_TIMESTAMP)
                    END,
                    revoked_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (current_session_id,),
            )
        else:
            if current_active:
                conn.execute(
                    "UPDATE orientation_sessions SET status = 'revoked', ended_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (current_session_id,),
                )
            conn.execute(
                """
                INSERT INTO orientation_measurement (id, enabled, current_session_id, revoked_at)
                VALUES (1, 0, NULL, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = 0,
                    current_session_id = NULL,
                    revoked_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """
            )
        state = conn.execute(
            "SELECT enabled, current_session_id, consented_at, revoked_at FROM orientation_measurement WHERE id = 1"
        ).fetchone()
        conn.commit()
    return _state(state)


def record_orientation_event(
    db_path: Path,
    *,
    flow: str,
    event: str,
    profile: str | None = None,
) -> dict[str, Any]:
    normalized_flow = str(flow or "").strip().lower()
    normalized_event = str(event or "").strip().lower()
    normalized_profile = str(profile or "").strip().lower() or None
    if normalized_flow not in FLOWS:
        raise ValueError("orientation_flow_not_allowed")
    if normalized_event not in EVENTS:
        raise ValueError("orientation_event_not_allowed")
    if normalized_profile is not None and normalized_profile not in PROFILES:
        raise ValueError("orientation_profile_not_allowed")
    if normalized_event == "profile_selected" and normalized_profile is None:
        raise ValueError("orientation_profile_required")
    if normalized_event == "profile_selected" and normalized_flow != "profile_selection":
        raise ValueError("orientation_event_flow_mismatch")
    if normalized_flow == "inbox" and normalized_profile is not None:
        raise ValueError("orientation_profile_not_applicable")

    with contextlib.closing(_connect(db_path)) as conn:
        consent = conn.execute(
            "SELECT enabled, current_session_id FROM orientation_measurement WHERE id = 1"
        ).fetchone()
        if not consent or not bool(consent["enabled"]) or not consent["current_session_id"]:
            raise PermissionError("orientation_measurement_not_consented")
        session_id = str(consent["current_session_id"])
        active = conn.execute(
            "SELECT 1 FROM orientation_sessions WHERE id = ? AND status = 'active'",
            (session_id,),
        ).fetchone()
        if active is None:
            raise PermissionError("orientation_measurement_session_inactive")
        row = conn.execute(
            """
            INSERT INTO orientation_events (id, session_id, flow, event, profile)
            VALUES (?, ?, ?, ?, ?)
            RETURNING id, session_id, flow, event, profile, created_at
            """,
            (str(uuid.uuid4()), session_id, normalized_flow, normalized_event, normalized_profile),
        ).fetchone()
    return dict(row)


def end_orientation_session(db_path: Path, *, status: str) -> dict[str, Any]:
    normalized = str(status or "").strip().lower()
    if normalized not in SESSION_END_STATUSES:
        raise ValueError("orientation_session_status_not_allowed")
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        consent = conn.execute(
            "SELECT enabled, current_session_id FROM orientation_measurement WHERE id = 1"
        ).fetchone()
        session_id = str(consent["current_session_id"] or "") if consent else ""
        if not consent or not bool(consent["enabled"]) or not session_id:
            raise PermissionError("orientation_measurement_not_consented")
        updated = conn.execute(
            """
            UPDATE orientation_sessions
            SET status = ?, ended_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'active'
            RETURNING id, status, started_at, ended_at
            """,
            (normalized, session_id),
        ).fetchone()
        if updated is None:
            raise PermissionError("orientation_measurement_session_inactive")
        conn.execute(
            "UPDATE orientation_measurement SET current_session_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = 1"
        )
        conn.commit()
    return dict(updated)


def erase_orientation_measurement(db_path: Path) -> dict[str, int | bool]:
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        event_count = int(conn.execute("SELECT COUNT(*) FROM orientation_events").fetchone()[0])
        session_count = int(conn.execute("SELECT COUNT(*) FROM orientation_sessions").fetchone()[0])
        conn.execute("DELETE FROM orientation_events")
        conn.execute("DELETE FROM orientation_sessions")
        conn.execute("DELETE FROM orientation_measurement")
        conn.commit()
    return {"deleted_events": event_count, "deleted_sessions": session_count, "enabled": False}


def orientation_summary(db_path: Path) -> dict[str, Any]:
    allowed_events = tuple(sorted(EVENTS))
    placeholders = ", ".join("?" for _ in allowed_events)
    with contextlib.closing(_connect(db_path)) as conn:
        state = conn.execute(
            "SELECT enabled, current_session_id, consented_at, revoked_at FROM orientation_measurement WHERE id = 1"
        ).fetchone()
        session_rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM orientation_sessions GROUP BY status"
        ).fetchall()
        event_rows = conn.execute(
            f"""
            SELECT flow, event, COUNT(*) AS count
            FROM orientation_events
            WHERE event IN ({placeholders})
            GROUP BY flow, event
            ORDER BY flow, event
            """,
            allowed_events,
        ).fetchall()
        event_count = int(conn.execute(
            f"SELECT COUNT(*) FROM orientation_events WHERE event IN ({placeholders})",
            allowed_events,
        ).fetchone()[0])
        retired_event_count = int(conn.execute(
            f"SELECT COUNT(*) FROM orientation_events WHERE event NOT IN ({placeholders})",
            allowed_events,
        ).fetchone()[0])
    sessions = {status: 0 for status in ("active", "completed", "abandoned", "revoked")}
    for row in session_rows:
        sessions[str(row["status"])] = int(row["count"])
    flows: dict[str, dict[str, int]] = {flow: {} for flow in sorted(FLOWS)}
    for row in event_rows:
        flows[str(row["flow"])][str(row["event"])] = int(row["count"])
    return {
        "consent": _state(state),
        "sessions": sessions,
        "event_count": event_count,
        "retired_event_count": retired_event_count,
        "flows": flows,
        "privacy": {
            "storage": "local_project_sqlite",
            "external_transmission": False,
            "free_text_collected": False,
            "issue_or_workspace_ids_collected": False,
            "event_allowlist": sorted(EVENTS),
        },
        "interpretation": {
            "constructs_not_measured": ["adoption", "clarity", "satisfaction", "causality"],
            "conclusion_allowed": False,
            "reason": "observed_counts_require_human_study_context_before_product_conclusions",
        },
    }


def _state(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {"enabled": False, "current_session_id": None, "consented_at": None, "revoked_at": None}
    return {
        "enabled": bool(row["enabled"]),
        "current_session_id": row["current_session_id"],
        "consented_at": row["consented_at"],
        "revoked_at": row["revoked_at"],
    }


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
