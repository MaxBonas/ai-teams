from __future__ import annotations

import contextlib
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.db.dependencies import unresolved_blockers
from aiteam.db.runs import create_run
from aiteam.db.wakeups import claim_next_wakeup, enqueue_wakeup, finish_wakeup
from aiteam.hiring_economics import estimate_run_economics


@dataclass(frozen=True)
class DispatchResult:
    wakeup_request: dict[str, Any]
    run: dict[str, Any]


class HeartbeatScheduler:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def tick_timers(self, now: datetime | str | None = None) -> list[dict[str, Any]]:
        """Enqueue timer wakeups for agents whose heartbeat interval is due."""
        now_dt = _coerce_datetime(now) or datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        enqueued: list[dict[str, Any]] = []
        for agent in self._timer_agents():
            interval = max(0, int(agent.get("heartbeat_interval_sec") or 0))
            if interval <= 0:
                continue
            last = _coerce_datetime(agent.get("last_heartbeat_at"))
            if last is not None and (now_dt - last).total_seconds() < interval:
                continue
            bucket = int(now_dt.timestamp() // interval)
            wakeup = enqueue_wakeup(
                self.db_path,
                agent_id=agent["id"],
                source="timer",
                reason="timer",
                trigger_detail=f"heartbeat_interval_sec={interval}",
                payload={"agent_id": agent["id"], "wake_reason": "timer"},
                idempotency_key=f"timer:{agent['id']}:{bucket}",
            )
            self._update_agent_heartbeat(agent["id"], now_iso)
            enqueued.append(wakeup)
        return enqueued

    def dispatch_next(
        self,
        *,
        agent_id: str | None = None,
        wakeup_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> DispatchResult | None:
        """Claim the next queued wakeup and create its durable run.

        This method deliberately stops before executing an adapter. It is the
        durable handoff point the future adapter runtime can consume.
        """
        while True:
            wakeup = claim_next_wakeup(self.db_path, agent_id=agent_id, wakeup_ids=wakeup_ids)
            if wakeup is None:
                return None
            payload = _decode_json(wakeup.get("payload_json"))
            issue_id = _clean_optional(payload.get("issue_id") or payload.get("task_id"))
            blockers = unresolved_blockers(self.db_path, issue_id=issue_id) if issue_id else []
            if not blockers:
                break
            finish_wakeup(
                self.db_path,
                wakeup_id=wakeup["id"],
                status="skipped",
                error="issue_dependencies_blocked",
            )
            self._log_blocked_wakeup(wakeup, issue_id=issue_id, blockers=blockers)
        payload = _decode_json(wakeup.get("payload_json"))
        run_id = str(payload.get("run_id") or f"run:{uuid.uuid4()}")
        issue_id = _clean_optional(payload.get("issue_id") or payload.get("task_id"))
        reason = _clean_optional(wakeup.get("reason")) or _clean_optional(payload.get("wake_reason"))
        context_snapshot = {
            "wake_reason": reason,
            "wake_source": wakeup.get("source"),
            "wakeup_request_id": wakeup.get("id"),
        }
        for key in (
            "interaction_id",
            "kind",
            "action",
            "child_issue_id",
            "child_issue_status",
            "child_liveness_state",
            "child_liveness_reason",
            "reporting_agent_id",
            "source_run_id",
            "liveness_state",
            "liveness_reason",
            "instruction",
            "continuation_attempt",
            "max_continuation_attempts",
            "prompt_budget_hint",
            "timeout_retry_attempt",
        ):
            if _clean_optional(payload.get(key)):
                context_snapshot[key] = _clean_optional(payload.get(key))
        if issue_id:
            context_snapshot["issue_id"] = issue_id
        estimated_cost_cents = _safe_int(payload.get("estimated_cost_cents"))
        estimated_savings_cents = _safe_int(payload.get("estimated_savings_cents"))
        if estimated_cost_cents == 0 and estimated_savings_cents == 0:
            # Wake sources rarely carry economics — compute them here so every
            # run lands in the DB with a real estimate and savings figure.
            try:
                estimated_cost_cents, estimated_savings_cents = estimate_run_economics(
                    self.db_path, str(wakeup["agent_id"])
                )
            except Exception:
                pass
        run = create_run(
            self.db_path,
            run_id=run_id,
            agent_id=wakeup["agent_id"],
            issue_id=issue_id,
            wakeup_request_id=wakeup["id"],
            profile=_clean_optional(payload.get("profile")),
            invocation_source=str(wakeup.get("source") or "manual"),
            trigger_detail=_clean_optional(wakeup.get("trigger_detail")),
            context_snapshot=context_snapshot,
            delegation_reason=_clean_optional(payload.get("delegation_reason")),
            complexity=_clean_optional(payload.get("complexity")),
            estimated_cost_cents=estimated_cost_cents,
            estimated_savings_cents=estimated_savings_cents,
        )
        self._mark_wakeup_running(wakeup["id"], run["id"])
        wakeup["status"] = "running"
        wakeup["run_id"] = run["id"]
        return DispatchResult(wakeup_request=wakeup, run=run)

    def _log_blocked_wakeup(
        self,
        wakeup: dict[str, Any],
        *,
        issue_id: str | None,
        blockers: list[dict[str, Any]],
    ) -> None:
        with contextlib.closing(_connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO activity_log (
                    id, actor_agent_id, action, target_type, target_id, payload_json
                )
                VALUES (?, ?, 'wakeup.skipped_blocked', 'wakeup', ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    wakeup.get("agent_id"),
                    wakeup.get("id"),
                    __import__("json").dumps(
                        {
                            "issue_id": issue_id,
                            "blockers": blockers,
                            "reason": "issue_dependencies_blocked",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )

    def _timer_agents(self) -> list[dict[str, Any]]:
        with contextlib.closing(_connect(self.db_path)) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM agents
                WHERE heartbeat_interval_sec > 0
                  AND status IN ('active', 'idle', 'running')
                ORDER BY id ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def _update_agent_heartbeat(self, agent_id: str, now_iso: str) -> None:
        with contextlib.closing(_connect(self.db_path)) as conn:
            conn.execute(
                """
                UPDATE agents
                SET last_heartbeat_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (now_iso, agent_id),
            )

    def _mark_wakeup_running(self, wakeup_id: str, run_id: str) -> None:
        with contextlib.closing(_connect(self.db_path)) as conn:
            conn.execute(
                """
                UPDATE wakeup_requests
                SET status = 'running',
                    run_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = 'claimed'
                """,
                (run_id, wakeup_id),
            )


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _coerce_datetime(value: datetime | str | Any | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _decode_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        payload = __import__("json").loads(value)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _clean_optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
