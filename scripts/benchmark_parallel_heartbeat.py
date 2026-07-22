"""A/B hermético del HeartbeatLoop secuencial frente a paralelo.

Ejecuta una cola determinista clonada, sin modelos ni red. El objetivo es
validar corrección, aislamiento del fallo y limpieza durable; la duración es
informativa y no autoriza ningún cambio de política.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.db.migration import SCHEMA_PATH  # noqa: E402
from aiteam.db.runs import finish_run, mark_run_running  # noqa: E402
from aiteam.db.wakeups import finish_wakeup  # noqa: E402
from aiteam.heartbeat.loop import HeartbeatLoop  # noqa: E402
from scripts.audit_parallel_channels import audit_database  # noqa: E402

CASE_VERSION = "parallel-heartbeat-hermetic-v1"
PARALLEL_FLAG = "AITEAM_PARALLEL_CHANNELS"
PARALLEL_MAX = "AITEAM_PARALLEL_MAX"


@dataclass(frozen=True)
class Branch:
    issue_id: str
    agent_id: str
    role: str
    adapter_type: str
    capacity_pool: str
    expected_run_status: str


BRANCHES = (
    Branch("root-work-a", "agent-work-a", "engineer", "hermetic-a", "pool-a", "completed"),
    Branch("root-work-b", "agent-work-b", "reviewer", "hermetic-b", "pool-b", "completed"),
    Branch("root-read-c", "agent-read-c", "file_scout", "hermetic-c", "pool-c", "completed"),
    Branch("root-fail-d", "agent-fail-d", "web_scout", "hermetic-d", "pool-d", "failed"),
)
FIRST_PARALLEL_BATCH = {"root-work-a", "root-read-c", "root-fail-d"}
WORK_SLOT_ISSUES = {"root-work-a", "root-work-b"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _parallel_setting(enabled: bool) -> Iterator[None]:
    previous = os.environ.get(PARALLEL_FLAG)
    previous_max = os.environ.get(PARALLEL_MAX)
    if enabled:
        os.environ[PARALLEL_FLAG] = "1"
        os.environ[PARALLEL_MAX] = "3"
    else:
        os.environ.pop(PARALLEL_FLAG, None)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(PARALLEL_FLAG, None)
        else:
            os.environ[PARALLEL_FLAG] = previous
        if previous_max is None:
            os.environ.pop(PARALLEL_MAX, None)
        else:
            os.environ[PARALLEL_MAX] = previous_max


def _init_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal', 'Hermetic parallel A/B')")
        for ordinal, branch in enumerate(BRANCHES, start=1):
            conn.execute(
                """
                INSERT INTO agents (
                    id, role, name, adapter_type, adapter_config_json,
                    heartbeat_interval_sec, status
                ) VALUES (?, ?, ?, ?, ?, 0, 'active')
                """,
                (
                    branch.agent_id,
                    branch.role,
                    branch.agent_id,
                    branch.adapter_type,
                    json.dumps({"capacity_pool": branch.capacity_pool}),
                ),
            )
            conn.execute(
                """
                INSERT INTO issues (
                    id, goal_id, title, status, role, assignee_agent_id
                ) VALUES (?, 'goal', ?, 'in_progress', ?, ?)
                """,
                (branch.issue_id, branch.issue_id, branch.role, branch.agent_id),
            )
            conn.execute(
                """
                INSERT INTO wakeup_requests (
                    id, agent_id, source, reason, status, payload_json, requested_at
                ) VALUES (?, ?, 'hermetic_benchmark', 'same_queue', 'queued', ?, ?)
                """,
                (
                    f"wakeup-{ordinal}",
                    branch.agent_id,
                    json.dumps({"issue_id": branch.issue_id}, sort_keys=True),
                    f"2026-07-22T00:00:0{ordinal}+00:00",
                ),
            )
        conn.commit()


class _HermeticExecutor:
    """Executor determinista mínimo para probar scheduling y durabilidad."""

    def __init__(self, db_path: Path, *, parallel: bool, delay_seconds: float) -> None:
        self.db_path = db_path
        self.parallel = parallel
        self.delay_seconds = delay_seconds
        self._origin = time.perf_counter()
        self._lock = threading.Lock()
        self._barrier = threading.Barrier(len(FIRST_PARALLEL_BATCH), timeout=5.0)
        self.intervals: dict[str, dict[str, float]] = {}
        self.execution_order: list[str] = []

    def execute(self, dispatch: Any) -> None:
        run = dispatch.run
        wakeup = dispatch.wakeup_request
        issue_id = str(run.get("issue_id") or "")
        run_id = str(run["id"])
        started_at = _now()
        mark_run_running(self.db_path, run_id=run_id, started_at=started_at)
        with self._lock:
            self.execution_order.append(issue_id)
            self.intervals[issue_id] = {
                "started_ms": round((time.perf_counter() - self._origin) * 1000, 3),
            }

        barrier_error = ""
        if self.parallel and issue_id in FIRST_PARALLEL_BATCH:
            try:
                self._barrier.wait()
            except threading.BrokenBarrierError:
                barrier_error = "hermetic_parallel_barrier_broken"
        time.sleep(self.delay_seconds)

        expected = next(item.expected_run_status for item in BRANCHES if item.issue_id == issue_id)
        status = "failed" if barrier_error else expected
        error = barrier_error or ("intentional_hermetic_failure" if status == "failed" else None)
        error_code = error
        finished_at = _now()
        finish_run(
            self.db_path,
            run_id=run_id,
            status=status,
            exit_code=0 if status == "completed" else 1,
            error=error,
            error_code=error_code,
            result={"case": CASE_VERSION, "issue_id": issue_id},
            finished_at=finished_at,
        )
        finish_wakeup(
            self.db_path,
            wakeup_id=str(wakeup["id"]),
            status="finished" if status == "completed" else "failed",
            run_id=run_id,
            error=error,
            finished_at=finished_at,
        )
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE issues SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                ("done" if status == "completed" else "blocked", issue_id),
            )
        with self._lock:
            self.intervals[issue_id]["finished_ms"] = round(
                (time.perf_counter() - self._origin) * 1000,
                3,
            )


def _overlap(left: dict[str, float], right: dict[str, float]) -> bool:
    return left["started_ms"] < right["finished_ms"] and right["started_ms"] < left["finished_ms"]


def _overlap_pairs(intervals: dict[str, dict[str, float]]) -> list[list[str]]:
    issue_ids = sorted(intervals)
    pairs: list[list[str]] = []
    for index, left_id in enumerate(issue_ids):
        for right_id in issue_ids[index + 1:]:
            if _overlap(intervals[left_id], intervals[right_id]):
                pairs.append([left_id, right_id])
    return pairs


def _collect_arm(path: Path, executor: _HermeticExecutor, dispatched: int) -> dict[str, Any]:
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        runs = conn.execute(
            "SELECT issue_id, status, error_code FROM runs ORDER BY issue_id"
        ).fetchall()
        wakeups = conn.execute(
            """
            SELECT json_extract(payload_json, '$.issue_id') AS issue_id, status, error
            FROM wakeup_requests ORDER BY issue_id
            """
        ).fetchall()
        issues = conn.execute("SELECT id, status FROM issues ORDER BY id").fetchall()
        active_runs = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status IN ('queued', 'running')"
        ).fetchone()[0]
        active_wakeups = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE status IN ('queued', 'claimed', 'running')"
        ).fetchone()[0]
        active_checkouts = conn.execute(
            """
            SELECT COUNT(*) FROM issues i JOIN runs r ON r.id = i.checkout_run_id
            WHERE r.status IN ('queued', 'running')
            """
        ).fetchone()[0]
        first_batch = [
            dict(row)
            for row in conn.execute(
                """
                SELECT wakeup_request_id, root_issue_id, decision, reason,
                       capacity_pool, is_work_slot
                FROM dispatch_candidate_decisions
                WHERE batch_id = (
                    SELECT batch_id FROM dispatch_candidate_decisions
                    ORDER BY considered_at, batch_id LIMIT 1
                )
                ORDER BY requested_at, wakeup_request_id
                """
            ).fetchall()
        ]
    audit = audit_database(path)
    return {
        "dispatched_runs": dispatched,
        "execution_order": executor.execution_order,
        "intervals_ms": executor.intervals,
        "overlap_pairs": _overlap_pairs(executor.intervals),
        "run_status_by_issue": {str(row["issue_id"]): str(row["status"]) for row in runs},
        "run_error_by_issue": {str(row["issue_id"]): str(row["error_code"] or "") for row in runs},
        "wakeup_status_by_issue": {str(row["issue_id"]): str(row["status"]) for row in wakeups},
        "issue_status_by_issue": {str(row["id"]): str(row["status"]) for row in issues},
        "active_runs": active_runs,
        "active_wakeups": active_wakeups,
        "active_checkouts": active_checkouts,
        "first_dispatch_batch": first_batch,
        "audit": {
            "evidence_quality": audit["evidence_quality"],
            "candidate_snapshot_batches": audit["dispatch_evidence"]["candidate_snapshot_batches"],
            "selected_run_coverage_ratio": audit["dispatch_evidence"]["selected_run_coverage_ratio"],
            "parallelizable_wait_runs": audit["parallelizable_wait_runs"],
            "parallelizable_wait_seconds": audit["parallelizable_wait_seconds"],
        },
    }


def _run_arm(path: Path, *, parallel: bool, delay_seconds: float) -> dict[str, Any]:
    _init_database(path)
    executor = _HermeticExecutor(path, parallel=parallel, delay_seconds=delay_seconds)
    loop = HeartbeatLoop(path, executor)  # type: ignore[arg-type]
    with _parallel_setting(parallel):
        dispatched = asyncio.run(loop.run_once())
    return _collect_arm(path, executor, dispatched)


def run_benchmark(*, workdir: Path | None = None, delay_seconds: float = 0.04) -> dict[str, Any]:
    managed_tmp: tempfile.TemporaryDirectory[str] | None = None
    if workdir is None:
        managed_tmp = tempfile.TemporaryDirectory(prefix="aiteam-parallel-heartbeat-")
        root = Path(managed_tmp.name)
    else:
        root = Path(workdir)
        root.mkdir(parents=True, exist_ok=True)
    try:
        sequential = _run_arm(root / "sequential" / "aiteam.db", parallel=False, delay_seconds=delay_seconds)
        parallel = _run_arm(root / "parallel" / "aiteam.db", parallel=True, delay_seconds=delay_seconds)
    finally:
        if managed_tmp is not None:
            # Windows puede conservar durante unos milisegundos el handle de
            # SQLite que usó el executor del event loop. Forzar finalizadores y
            # reintentar de forma acotada evita dejar runtime temporal sin
            # ocultar un handle realmente filtrado.
            cleanup_error: PermissionError | None = None
            for _ in range(10):
                try:
                    managed_tmp.cleanup()
                    cleanup_error = None
                    break
                except PermissionError as exc:
                    cleanup_error = exc
                    gc.collect()
                    time.sleep(0.05)
            if cleanup_error is not None:
                raise cleanup_error

    expected_runs = {item.issue_id: item.expected_run_status for item in BRANCHES}
    expected_wakeups = {
        item.issue_id: "finished" if item.expected_run_status == "completed" else "failed"
        for item in BRANCHES
    }
    expected_issues = {
        item.issue_id: "done" if item.expected_run_status == "completed" else "blocked"
        for item in BRANCHES
    }
    parallel_overlaps = {tuple(pair) for pair in parallel["overlap_pairs"]}
    expected_parallel_overlaps = {
        tuple(sorted((left, right)))
        for index, left in enumerate(sorted(FIRST_PARALLEL_BATCH))
        for right in sorted(FIRST_PARALLEL_BATCH)[index + 1:]
    }
    parallel_first = {item["root_issue_id"]: item for item in parallel["first_dispatch_batch"]}
    checks = {
        "same_four_runs_dispatched": sequential["dispatched_runs"] == parallel["dispatched_runs"] == len(BRANCHES),
        "terminal_run_parity": sequential["run_status_by_issue"] == parallel["run_status_by_issue"] == expected_runs,
        "terminal_wakeup_parity": sequential["wakeup_status_by_issue"] == parallel["wakeup_status_by_issue"] == expected_wakeups,
        "terminal_issue_parity": sequential["issue_status_by_issue"] == parallel["issue_status_by_issue"] == expected_issues,
        "sequential_has_no_overlap": sequential["overlap_pairs"] == [],
        "parallel_overlaps_only_admitted_batch": parallel_overlaps == expected_parallel_overlaps,
        "work_slots_never_overlap": not any(
            set(pair) == WORK_SLOT_ISSUES for pair in parallel_overlaps
        ),
        "second_work_slot_rejected": (
            parallel_first.get("root-work-b", {}).get("reason") == "second_work_slot"
            and parallel_first.get("root-work-b", {}).get("decision") == "rejected"
        ),
        "parallel_first_batch_selection_exact": {
            issue_id
            for issue_id, item in parallel_first.items()
            if item.get("decision") == "selected"
        } == FIRST_PARALLEL_BATCH,
        "intentional_failure_isolated": all(
            arm["run_error_by_issue"].get("root-fail-d") == "intentional_hermetic_failure"
            and all(
                not error
                for issue_id, error in arm["run_error_by_issue"].items()
                if issue_id != "root-fail-d"
            )
            for arm in (sequential, parallel)
        ),
        "no_orphans": all(
            arm[key] == 0
            for arm in (sequential, parallel)
            for key in ("active_runs", "active_wakeups", "active_checkouts")
        ),
        "exact_dispatch_coverage": all(
            arm["audit"]["evidence_quality"] == "exact"
            and arm["audit"]["selected_run_coverage_ratio"] == 1.0
            for arm in (sequential, parallel)
        ),
    }
    return {
        "schema_version": 1,
        "benchmark": "parallel_heartbeat_hermetic_ab",
        "case_version": CASE_VERSION,
        "generated_at": _now(),
        "contract": {
            "models_or_network_used": False,
            "same_initial_queue": True,
            "root_count": len(BRANCHES),
            "capacity_pool_count": len({item.capacity_pool for item in BRANCHES}),
            "work_slot_candidates": sorted(WORK_SLOT_ISSUES),
            "intentional_failure_issue": "root-fail-d",
        },
        "arms": {"sequential": sequential, "parallel": parallel},
        "checks": checks,
        "conclusion": {
            "correction_validated": all(checks.values()),
            "performance_claim_allowed": False,
            "live_contention_trigger_satisfied": False,
            "default_change_allowed": False,
            "decision": "retain_sequential_default",
            "reason": "A/B hermético de corrección; no representa latencia, cuota ni calidad de proveedores vivos",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workdir", type=Path)
    args = parser.parse_args()
    report = run_benchmark(workdir=args.workdir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["conclusion"], ensure_ascii=False))
    return 0 if report["conclusion"]["correction_validated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
