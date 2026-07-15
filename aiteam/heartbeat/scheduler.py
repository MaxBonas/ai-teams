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


def batch_candidates(db_path: Path, *, limit: int = 25) -> list[dict[str, Any]]:
    """Wakeups en cola enriquecidos para la selección del batch paralelo.

    Cada candidato lleva el rol y adapter de su agente, la issue objetivo y la
    raíz de su subtree (CTE recursivo sobre parent_id) — todo lo que
    ``select_parallel_batch`` necesita para aplicar restricciones sin reclamar
    nada todavía (reclamar y devolver a la cola no es atómico; filtrar antes sí).
    """
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT w.id AS wakeup_id, w.agent_id, w.payload_json,
                   a.role, a.adapter_type, a.adapter_config_json
            FROM wakeup_requests w
            LEFT JOIN agents a ON a.id = w.agent_id
            WHERE w.status = 'queued'
            ORDER BY w.requested_at ASC, w.id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = _decode_json(row["payload_json"])
            issue_id = str(payload.get("issue_id") or payload.get("task_id") or "").strip()
            root_id = issue_id
            if issue_id:
                root_row = conn.execute(
                    """
                    WITH RECURSIVE chain(id, parent_id) AS (
                        SELECT id, parent_id FROM issues WHERE id = ?
                        UNION ALL
                        SELECT i.id, i.parent_id FROM issues i JOIN chain c ON i.id = c.parent_id
                    )
                    SELECT id FROM chain WHERE parent_id IS NULL LIMIT 1
                    """,
                    (issue_id,),
                ).fetchone()
                if root_row:
                    root_id = str(root_row["id"])
            out.append({
                "wakeup_id": str(row["wakeup_id"]),
                "agent_id": str(row["agent_id"] or ""),
                "role": str(row["role"] or "").strip().lower(),
                "adapter_type": str(row["adapter_type"] or ""),
                "adapter_config_json": str(row["adapter_config_json"] or "{}"),
                "issue_id": issue_id,
                "root_issue_id": root_id or f"agent:{row['agent_id']}",
            })
        return out


def select_parallel_batch(candidates: list[dict[str, Any]], *, max_runs: int) -> list[str]:
    """Selección pura del batch paralelo (testeable sin DB).

    Restricciones, en orden de llegada de la cola:
      1. Proveedores DISTINTOS (el pacing TPM del governor es por proveedor;
         dos runs del mismo proveedor en paralelo se pisan el presupuesto).
      2. Agentes distintos y subtrees (raíz) distintos — dos runs del mismo
         subtree comparten estado de issue y se generan comentarios mutuamente.
      3. Como máximo UN rol de slot de trabajo (WORK_SLOT_ROLES): el workspace
         es compartido y la atribución de deltas/evidencia no sobrevive a dos
         editores o verificadores concurrentes.
    """
    from aiteam.hiring_economics import provider_and_model_for
    from aiteam.policies import WORK_SLOT_ROLES
    import json as _json

    selected: list[str] = []
    providers: set[str] = set()
    agents: set[str] = set()
    roots: set[str] = set()
    work_slot_taken = False
    for cand in candidates:
        if len(selected) >= max_runs:
            break
        role = cand["role"]
        try:
            provider, _ = provider_and_model_for(
                cand["adapter_type"], _json.loads(cand["adapter_config_json"] or "{}")
            )
        except Exception:
            provider = cand["adapter_type"] or "?"
        # test_runner se ejecuta como builtin determinista: no consume proveedor.
        provider_key = "builtin" if role == "test_runner" else (provider or cand["adapter_type"] or "?")
        is_work = role in WORK_SLOT_ROLES
        if cand["agent_id"] in agents:
            continue
        if cand["root_issue_id"] in roots:
            continue
        if provider_key != "builtin" and provider_key in providers:
            continue
        if is_work and work_slot_taken:
            continue
        selected.append(cand["wakeup_id"])
        agents.add(cand["agent_id"])
        roots.add(cand["root_issue_id"])
        if provider_key != "builtin":
            providers.add(provider_key)
        if is_work:
            work_slot_taken = True
    return selected


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
