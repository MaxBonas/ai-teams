from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Fraction of monthly budget at which a soft-limit warning is emitted.
# Matches Paperclip's budget_soft_threshold_crossed event.
BUDGET_SOFT_THRESHOLD = 0.80


@dataclass(frozen=True)
class BudgetStatus:
    agent_id: str
    period: str
    budget_monthly_cents: int
    spent_cents: int
    remaining_cents: int
    exceeded: bool
    near_limit: bool
    reason: str

    @property
    def allowed(self) -> bool:
        return not self.exceeded

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "period": self.period,
            "budget_monthly_cents": self.budget_monthly_cents,
            "spent_cents": self.spent_cents,
            "remaining_cents": self.remaining_cents,
            "exceeded": self.exceeded,
            "near_limit": self.near_limit,
            "allowed": self.allowed,
            "reason": self.reason,
        }


def record_cost(
    db_path: Path,
    *,
    run_id: str,
    agent_id: str,
    amount_cents: int,
    period: str | None = None,
    event_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record one durable cost event for a run.

    This is intentionally run-id idempotent for the current v2 executor: a run
    produces at most one cost event. Later streaming/billing adapters can add a
    separate event kind if they need incremental accounting.
    """

    normalized_period = _period(period)
    amount = max(0, int(amount_cents or 0))
    with contextlib.closing(_connect(db_path)) as conn:
        existing = conn.execute(
            "SELECT * FROM cost_events WHERE run_id = ? LIMIT 1",
            (run_id,),
        ).fetchone()
        if existing is not None:
            return dict(existing)

        run = conn.execute(
            """
            SELECT issue_id, provider, model, channel, usage_json, estimated_savings_cents
            FROM runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if run is None:
            raise LookupError(f"run {run_id!r} not found")

        usage = _decode_json(run["usage_json"])
        row = conn.execute(
            """
            INSERT INTO cost_events (
                id, run_id, agent_id, issue_id, provider, model, channel,
                cost_cents, period, input_tokens, output_tokens,
                estimated_savings_cents, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                event_id or str(uuid.uuid4()),
                run_id,
                agent_id,
                run["issue_id"],
                run["provider"],
                run["model"],
                run["channel"],
                amount,
                normalized_period,
                _safe_int(usage.get("input_tokens") or usage.get("prompt_tokens")),
                _safe_int(usage.get("output_tokens") or usage.get("completion_tokens")),
                _safe_int(run["estimated_savings_cents"]),
                _json(metadata),
            ),
        ).fetchone()
        conn.execute(
            """
            UPDATE agents
            SET spent_monthly_cents = (
                    SELECT COALESCE(SUM(cost_cents), 0)
                    FROM cost_events
                    WHERE agent_id = ?
                      AND period = ?
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (agent_id, normalized_period, agent_id),
        )
        return dict(row)


def check_budget(
    db_path: Path,
    *,
    agent_id: str,
    period: str | None = None,
) -> BudgetStatus:
    normalized_period = _period(period)
    with contextlib.closing(_connect(db_path)) as conn:
        agent = conn.execute(
            "SELECT budget_monthly_cents FROM agents WHERE id = ?",
            (agent_id,),
        ).fetchone()
        if agent is None:
            raise LookupError(f"agent {agent_id!r} not found")
        spent = conn.execute(
            """
            SELECT COALESCE(SUM(cost_cents), 0)
            FROM cost_events
            WHERE agent_id = ?
              AND period = ?
            """,
            (agent_id, normalized_period),
        ).fetchone()[0]

    budget = max(0, _safe_int(agent["budget_monthly_cents"]))
    spent_cents = max(0, _safe_int(spent))
    if budget <= 0:
        return BudgetStatus(
            agent_id=agent_id,
            period=normalized_period,
            budget_monthly_cents=budget,
            spent_cents=spent_cents,
            remaining_cents=0,
            exceeded=False,
            near_limit=False,
            reason="budget_unlimited",
        )
    remaining = budget - spent_cents
    exceeded = spent_cents >= budget
    near_limit = not exceeded and spent_cents >= int(budget * BUDGET_SOFT_THRESHOLD)
    if exceeded:
        reason = "budget_exceeded"
    elif near_limit:
        reason = "budget_near_limit"
    else:
        reason = "budget_available"
    return BudgetStatus(
        agent_id=agent_id,
        period=normalized_period,
        budget_monthly_cents=budget,
        spent_cents=spent_cents,
        remaining_cents=max(0, remaining),
        exceeded=exceeded,
        near_limit=near_limit,
        reason=reason,
    )


def current_period(now: datetime | None = None) -> str:
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


def _period(value: str | None) -> str:
    text = str(value or "").strip()
    return text or current_period()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _decode_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
