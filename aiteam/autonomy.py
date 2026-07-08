"""Autonomy policy (P5): auto-resolve OPERATIONAL escalations.

Two modes, stored per project in project_config.json (see
aiteam.project_adapters.project_autonomy):

- supervised (default): every escalation waits for the user. Optionally,
  operational escalations older than AITEAM_INTERACTION_TTL_MINUTES take
  their safe default instead of freezing the subtree forever.
- autonomous: operational escalations take their safe default immediately,
  at most ONCE per (issue, reason) — a repeat of the same escalation means
  the default didn't work, so it stays pending for the user.

PRODUCT decisions (initial_cycle_ready, suggest_tasks, ask_user_questions,
criticality_requires_approval, budget_exceeded…) are never auto-resolved:
only reasons listed in aiteam.policies.OPERATIONAL_INTERACTION_DEFAULTS
qualify. Resolution goes through resolve_interaction, so the assignee wakeup
(continuation_policy) fires exactly as if the user had clicked the button.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.db.activity_log import log_activity
from aiteam.db.interactions import ConflictError, resolve_interaction
from aiteam.policies import (
    AUTONOMY_AUTONOMOUS,
    interaction_ttl_minutes,
    operational_interaction_default,
)
from aiteam.project_adapters import project_autonomy

logger = logging.getLogger(__name__)

# Recorded as resolved_by_user_id / actor_user_id so both the interaction row
# and the activity log show WHO took the decision (auditable, filterable).
AUTONOMY_RESOLVER_ID = "autonomy"

_AUTO_RESOLVE_ACTION = "interaction.auto_resolved"


def auto_resolve_operational_interactions(db_path: Path) -> list[str]:
    """Reconciler: resolve pending operational escalations per the autonomy
    policy. Returns the ids of the interactions it resolved."""
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    mode = project_autonomy(db_path.parent)
    ttl_minutes = interaction_ttl_minutes()
    autonomous = mode == AUTONOMY_AUTONOMOUS
    if not autonomous and ttl_minutes <= 0:
        return []

    resolved: list[str] = []
    for row in _pending_interactions(db_path):
        payload = _decode_payload(row.get("payload_json"))
        reason = str(payload.get("reason") or payload.get("escalation_reason") or "").strip().lower()
        default_action = operational_interaction_default(reason)
        if default_action is None:
            continue  # product decision — always the user's
        if autonomous:
            if _already_auto_resolved(db_path, issue_id=str(row["issue_id"]), reason=reason):
                continue  # same escalation repeated — promote to the user
        elif float(row.get("age_minutes") or 0.0) < ttl_minutes:
            continue  # supervised TTL: not old enough yet
        trigger = "autonomous" if autonomous else "ttl_expired"
        try:
            resolve_interaction(
                db_path,
                interaction_id=str(row["id"]),
                action=default_action,
                resolution_data={
                    "auto_resolved": True,
                    "autonomy_trigger": trigger,
                    "reason": reason,
                },
                resolved_by_user_id=AUTONOMY_RESOLVER_ID,
            )
        except (ConflictError, LookupError):
            continue  # resolved concurrently — nothing to do
        except ValueError:
            logger.warning(
                "autonomy: default action %r not valid for interaction %s (kind %s) — skipping",
                default_action, row["id"], row.get("kind"),
            )
            continue
        log_activity(
            db_path,
            action=_AUTO_RESOLVE_ACTION,
            target_type="interaction",
            target_id=str(row["id"]),
            actor_user_id=AUTONOMY_RESOLVER_ID,
            payload={
                "issue_id": str(row["issue_id"]),
                "reason": reason,
                "action": default_action,
                "trigger": trigger,
            },
        )
        resolved.append(str(row["id"]))
    return resolved


def _pending_interactions(db_path: Path) -> list[dict[str, Any]]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT *, (julianday('now') - julianday(created_at)) * 1440.0 AS age_minutes
            FROM issue_thread_interactions
            WHERE status = 'pending'
            ORDER BY created_at ASC, rowid ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def _already_auto_resolved(db_path: Path, *, issue_id: str, reason: str) -> bool:
    """One safe default per (issue, reason): the second identical escalation
    means the default didn't fix it and the user has to decide."""
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM activity_log
            WHERE action = ?
              AND json_extract(payload_json, '$.issue_id') = ?
              AND json_extract(payload_json, '$.reason') = ?
            """,
            (_AUTO_RESOLVE_ACTION, issue_id, reason),
        ).fetchone()
        return bool(row and row[0])


def _decode_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
