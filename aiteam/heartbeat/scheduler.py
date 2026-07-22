from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.db.runs import create_run
from aiteam.db.wakeups import claim_next_wakeup, enqueue_wakeup, finish_wakeup
from aiteam.hiring_economics import estimate_run_economics, provider_and_model_for
from aiteam.policies import WORK_SLOT_ROLES
from aiteam.provider_identity import capacity_pool_key
from aiteam.user_config import resolve_adapter_config


@dataclass(frozen=True)
class DispatchResult:
    wakeup_request: dict[str, Any]
    run: dict[str, Any]


@dataclass(frozen=True)
class ParallelBatchPlan:
    batch_id: str
    candidates: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    selected_wakeup_ids: list[str]


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
        record_candidate_decision: bool = True,
    ) -> DispatchResult | None:
        """Claim the next queued wakeup and create its durable run.

        This method deliberately stops before executing an adapter. It is the
        durable handoff point the future adapter runtime can consume.
        """
        while True:
            wakeup = claim_next_wakeup(self.db_path, agent_id=agent_id, wakeup_ids=wakeup_ids)
            if wakeup is None:
                return None
            candidate = _candidate_snapshot(self.db_path, wakeup)
            readiness_reason = str(candidate.get("readiness_reason") or "")
            if not readiness_reason:
                break
            if record_candidate_decision:
                record_dispatch_decisions(
                    self.db_path,
                    batch_id=f"sequential:{uuid.uuid4()}",
                    dispatch_mode="sequential",
                    decisions=[{**candidate, "decision": "rejected", "reason": readiness_reason}],
                )
            error = (
                "issue_dependencies_blocked"
                if readiness_reason == "dependency_blocked"
                else "issue_checkout_active"
            )
            finish_wakeup(
                self.db_path,
                wakeup_id=wakeup["id"],
                status="skipped",
                error=error,
            )
            self._log_unready_wakeup(wakeup, candidate=candidate, error=error)
        if record_candidate_decision:
            record_dispatch_decisions(
                self.db_path,
                batch_id=f"sequential:{uuid.uuid4()}",
                dispatch_mode="sequential",
                decisions=[{**candidate, "decision": "selected", "reason": "selected"}],
            )
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
            "quorum_session_id",
            "correction",
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

    def _log_unready_wakeup(
        self,
        wakeup: dict[str, Any],
        *,
        candidate: dict[str, Any],
        error: str,
    ) -> None:
        with contextlib.closing(_connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO activity_log (
                    id, actor_agent_id, action, target_type, target_id, payload_json
                )
                VALUES (?, ?, ?, 'wakeup', ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    wakeup.get("agent_id"),
                    (
                        "wakeup.skipped_blocked"
                        if error == "issue_dependencies_blocked"
                        else "wakeup.skipped_checkout"
                    ),
                    wakeup.get("id"),
                    __import__("json").dumps(
                        {
                            "issue_id": candidate.get("issue_id"),
                            "blockers": (
                                candidate.get("readiness_details") or {}
                            ).get("blockers", []),
                            "checkout_run_id": (
                                candidate.get("readiness_details") or {}
                            ).get("checkout_run_id"),
                            "reason": error,
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
            SELECT w.id AS wakeup_id, w.agent_id, w.payload_json, w.requested_at,
                   a.role, a.adapter_type, a.adapter_config_json
            FROM wakeup_requests w
            LEFT JOIN agents a ON a.id = w.agent_id
            WHERE w.status = 'queued'
            ORDER BY w.requested_at ASC, w.id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_enrich_candidate(conn, dict(row)) for row in rows]


def plan_parallel_batch(db_path: Path, *, max_runs: int, limit: int = 25) -> ParallelBatchPlan:
    """Evalúa y persiste un snapshot completo antes de reclamar wakeups."""
    candidates = batch_candidates(db_path, limit=limit)
    decisions = evaluate_parallel_batch(candidates, max_runs=max_runs)
    batch_id = f"parallel:{uuid.uuid4()}"
    record_dispatch_decisions(
        db_path,
        batch_id=batch_id,
        dispatch_mode="parallel",
        decisions=decisions,
    )
    return ParallelBatchPlan(
        batch_id=batch_id,
        candidates=candidates,
        decisions=decisions,
        selected_wakeup_ids=[
            str(item["wakeup_id"])
            for item in decisions
            if item["decision"] == "selected"
        ],
    )


def select_parallel_batch(candidates: list[dict[str, Any]], *, max_runs: int) -> list[str]:
    """Selección pura del batch paralelo (testeable sin DB).

    Restricciones, en orden de llegada de la cola:
      1. Pools de capacidad DISTINTOS (dos runs que comparten cuota o pacing
         no se solapan aunque transporten modelos de vendors distintos).
      2. Agentes distintos y subtrees (raíz) distintos — dos runs del mismo
         subtree comparten estado de issue y se generan comentarios mutuamente.
      3. Como máximo UN rol de slot de trabajo (WORK_SLOT_ROLES): el workspace
         es compartido y la atribución de deltas/evidencia no sobrevive a dos
         editores o verificadores concurrentes.
    """
    return [
        str(item["wakeup_id"])
        for item in evaluate_parallel_batch(candidates, max_runs=max_runs)
        if item["decision"] == "selected"
    ]


def evaluate_parallel_batch(
    candidates: list[dict[str, Any]], *, max_runs: int
) -> list[dict[str, Any]]:
    """Devuelve una decisión y un motivo estable para cada candidato."""
    selected: list[str] = []
    capacity_pools: set[str] = set()
    agents: set[str] = set()
    roots: set[str] = set()
    work_slot_taken = False
    decisions: list[dict[str, Any]] = []
    for cand in candidates:
        role = str(cand.get("role") or "")
        pool = str(cand.get("capacity_pool") or _candidate_capacity_pool(cand))
        is_work = role in WORK_SLOT_ROLES
        reason = str(cand.get("readiness_reason") or "")
        if not reason and len(selected) >= max_runs:
            reason = "batch_limit"
        if not reason and cand["agent_id"] in agents:
            reason = "same_agent"
        if not reason and cand["root_issue_id"] in roots:
            reason = "same_root_issue"
        if not reason and pool != "builtin" and pool in capacity_pools:
            reason = "same_capacity_pool"
        if not reason and is_work and work_slot_taken:
            reason = "second_work_slot"
        if reason:
            decisions.append({
                **cand,
                "capacity_pool": pool,
                "is_work_slot": is_work,
                "decision": "rejected",
                "reason": reason,
            })
            continue
        selected.append(str(cand["wakeup_id"]))
        agents.add(str(cand["agent_id"]))
        roots.add(str(cand["root_issue_id"]))
        if pool != "builtin":
            capacity_pools.add(pool)
        if is_work:
            work_slot_taken = True
        decisions.append({
            **cand,
            "capacity_pool": pool,
            "is_work_slot": is_work,
            "decision": "selected",
            "reason": "selected",
        })
    return decisions


def record_dispatch_decisions(
    db_path: Path,
    *,
    batch_id: str,
    dispatch_mode: str,
    decisions: list[dict[str, Any]],
    considered_at: str | None = None,
) -> None:
    """Persiste la provenance de selección sin depender de una run futura."""
    observed_at = considered_at or datetime.now(timezone.utc).isoformat()
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for item in decisions:
                wakeup_id = str(item.get("wakeup_id") or "")
                ready_at = None
                if not item.get("readiness_reason"):
                    previous = conn.execute(
                        """
                        SELECT MIN(ready_at)
                        FROM dispatch_candidate_decisions
                        WHERE wakeup_request_id = ? AND ready_at IS NOT NULL
                        """,
                        (wakeup_id,),
                    ).fetchone()
                    ready_at = str(previous[0]) if previous and previous[0] else observed_at
                details = dict(item.get("readiness_details") or {})
                details["ready_at_semantics"] = "first_scheduler_observation"
                conn.execute(
                    """
                    INSERT INTO dispatch_candidate_decisions (
                        id, batch_id, dispatch_mode, wakeup_request_id, agent_id,
                        issue_id, root_issue_id, role, capacity_pool, is_work_slot,
                        requested_at, ready_at, considered_at, decision, reason,
                        details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(batch_id, wakeup_request_id) DO NOTHING
                    """,
                    (
                        str(uuid.uuid4()),
                        batch_id,
                        dispatch_mode,
                        wakeup_id or None,
                        str(item.get("agent_id") or "") or None,
                        str(item.get("issue_id") or "") or None,
                        str(
                            item.get("root_issue_id")
                            or f"agent:{item.get('agent_id') or 'unknown'}"
                        ),
                        str(item.get("role") or ""),
                        str(item.get("capacity_pool") or _candidate_capacity_pool(item)),
                        int(bool(item.get("is_work_slot"))),
                        item.get("requested_at"),
                        ready_at,
                        observed_at,
                        str(item["decision"]),
                        str(item["reason"]),
                        json.dumps(details, ensure_ascii=False, sort_keys=True),
                    ),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _candidate_snapshot(db_path: Path, wakeup: dict[str, Any]) -> dict[str, Any]:
    with contextlib.closing(_connect(db_path)) as conn:
        return _enrich_candidate(conn, wakeup)


def _enrich_candidate(
    conn: sqlite3.Connection, wakeup: dict[str, Any]
) -> dict[str, Any]:
    payload = _decode_json(wakeup.get("payload_json"))
    wakeup_id = str(wakeup.get("wakeup_id") or wakeup.get("id") or "")
    agent_id = str(wakeup.get("agent_id") or "")
    agent = conn.execute(
        "SELECT role, adapter_type, adapter_config_json FROM agents WHERE id = ?",
        (agent_id,),
    ).fetchone()
    role = str(wakeup.get("role") or (agent["role"] if agent else "") or "").strip().lower()
    adapter_type = str(
        wakeup.get("adapter_type") or (agent["adapter_type"] if agent else "") or ""
    )
    adapter_config_json = str(
        wakeup.get("adapter_config_json")
        or (agent["adapter_config_json"] if agent else "{}")
        or "{}"
    )
    issue_id = str(payload.get("issue_id") or payload.get("task_id") or "").strip()
    root_id = issue_id or f"agent:{agent_id}"
    blockers: list[dict[str, Any]] = []
    checkout_run_id = ""
    checkout_run_status = ""
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
        blockers = [
            dict(row)
            for row in conn.execute(
                """
                SELECT d.depends_on_issue_id AS issue_id, i.status
                FROM issue_dependencies d
                JOIN issues i ON i.id = d.depends_on_issue_id
                WHERE d.issue_id = ? AND i.status NOT IN ('done', 'cancelled')
                ORDER BY d.created_at, d.depends_on_issue_id
                """,
                (issue_id,),
            ).fetchall()
        ]
        checkout = conn.execute(
            """
            SELECT i.checkout_run_id, r.status AS run_status
            FROM issues i
            LEFT JOIN runs r ON r.id = i.checkout_run_id
            WHERE i.id = ?
            """,
            (issue_id,),
        ).fetchone()
        if checkout:
            checkout_run_id = str(checkout["checkout_run_id"] or "")
            checkout_run_status = str(checkout["run_status"] or "")
    payload_run_id = str(payload.get("run_id") or "")
    active_checkout = bool(
        checkout_run_id
        and checkout_run_id != payload_run_id
        and checkout_run_status in {"queued", "running"}
    )
    readiness_reason = ""
    if blockers:
        readiness_reason = "dependency_blocked"
    elif active_checkout:
        readiness_reason = "checkout_active"
    candidate = {
        "wakeup_id": wakeup_id,
        "agent_id": agent_id,
        "role": role,
        "adapter_type": adapter_type,
        "adapter_config_json": adapter_config_json,
        "issue_id": issue_id,
        "root_issue_id": root_id,
        "requested_at": wakeup.get("requested_at"),
        "readiness_reason": readiness_reason,
        "readiness_details": {
            "blockers": blockers,
            "checkout_run_id": checkout_run_id or None,
            "checkout_run_status": checkout_run_status or None,
        },
    }
    candidate["capacity_pool"] = _candidate_capacity_pool(candidate)
    candidate["is_work_slot"] = role in WORK_SLOT_ROLES
    return candidate


def _candidate_capacity_pool(candidate: dict[str, Any]) -> str:
    if str(candidate.get("role") or "").strip().lower() == "test_runner":
        return "builtin"
    adapter_type = str(candidate.get("adapter_type") or "")
    config = _decode_json(candidate.get("adapter_config_json"))
    try:
        provider, _ = provider_and_model_for(adapter_type, config)
        effective_config = resolve_adapter_config(adapter_type, config)
        return capacity_pool_key(
            profile_id=str(config.get("profile_id") or ""),
            provider=provider or adapter_type,
            config=effective_config,
        )
    except Exception:
        return capacity_pool_key(provider=adapter_type) or "unknown"


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
