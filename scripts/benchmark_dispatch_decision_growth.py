"""Mide crecimiento y coste local de ``dispatch_candidate_decisions``.

El benchmark es hermético: crea colas SQLite sintéticas, ejecuta exactamente
``plan_sequential_batch`` y retira un wakeup listo por iteración. No usa red,
modelos ni bases de proyectos reales. Los thresholds se congelan en código para
evitar decidir una poda después de observar el resultado.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.db.migration import SCHEMA_PATH  # noqa: E402
from aiteam.heartbeat.scheduler import plan_sequential_batch  # noqa: E402

CASE_VERSION = "dispatch-decision-growth-v1"
DEFAULT_QUEUE_SIZES = (1, 25, 100, 1000)
SNAPSHOT_LIMIT = 25
QUERY_REPETITIONS = 25
THRESHOLDS = {
    "max_rows_per_wakeup": 25.0,
    "max_planning_ms_per_dispatch": 50.0,
    "max_query_median_ms": 10.0,
    "max_delta_bytes_per_wakeup": 32768.0,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def expected_decision_rows(queue_size: int, *, limit: int = SNAPSHOT_LIMIT) -> int:
    """Filas exactas al drenar una cola lista fotografiando un prefijo fijo."""
    if queue_size < 0 or limit < 1:
        raise ValueError("queue_size must be >= 0 and limit must be >= 1")
    return sum(min(remaining, limit) for remaining in range(queue_size, 0, -1))


def _storage_bytes(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
        if candidate.exists()
    )


def _init_database(path: Path, *, queue_size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base_time = datetime(2026, 7, 22, tzinfo=timezone.utc)
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal', 'Dispatch growth')")
        conn.execute(
            """
            INSERT INTO agents (id, role, name, adapter_type, adapter_config_json)
            VALUES ('agent', 'file_scout', 'Scout', 'hermetic', ?)
            """,
            (json.dumps({"capacity_pool": "benchmark-pool"}),),
        )
        conn.executemany(
            """
            INSERT INTO issues (
                id, goal_id, title, status, role, assignee_agent_id
            ) VALUES (?, 'goal', ?, 'in_progress', 'file_scout', 'agent')
            """,
            ((f"issue-{ordinal:04d}", f"Issue {ordinal}") for ordinal in range(queue_size)),
        )
        conn.executemany(
            """
            INSERT INTO wakeup_requests (
                id, agent_id, source, reason, status, payload_json, requested_at
            ) VALUES (?, 'agent', 'dispatch_growth_benchmark', 'same_queue',
                      'queued', ?, ?)
            """,
            (
                (
                    f"wakeup-{ordinal:04d}",
                    json.dumps({"issue_id": f"issue-{ordinal:04d}"}, sort_keys=True),
                    (base_time + timedelta(milliseconds=ordinal)).isoformat(),
                )
                for ordinal in range(queue_size)
            ),
        )
        conn.commit()
        conn.execute("VACUUM")


def _query_latencies(path: Path, *, wakeup_id: str, batch_id: str) -> dict[str, float]:
    samples: dict[str, list[float]] = {"wakeup_history": [], "recent_batch": []}
    with sqlite3.connect(str(path)) as conn:
        statements = {
            "wakeup_history": (
                "SELECT decision, reason, considered_at FROM dispatch_candidate_decisions "
                "WHERE wakeup_request_id = ? ORDER BY considered_at",
                (wakeup_id,),
            ),
            "recent_batch": (
                "SELECT wakeup_request_id, decision, reason FROM dispatch_candidate_decisions "
                "WHERE batch_id = ? ORDER BY considered_at",
                (batch_id,),
            ),
        }
        for sql, params in statements.values():
            conn.execute(sql, params).fetchall()
        for _ in range(QUERY_REPETITIONS):
            for name, (sql, params) in statements.items():
                started = time.perf_counter()
                conn.execute(sql, params).fetchall()
                samples[name].append((time.perf_counter() - started) * 1000)
    out: dict[str, float] = {}
    for name, values in samples.items():
        ordered = sorted(values)
        p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
        out[f"{name}_median_ms"] = round(statistics.median(values), 6)
        out[f"{name}_p95_ms"] = round(ordered[p95_index], 6)
    return out


def _run_case(path: Path, *, queue_size: int) -> dict[str, Any]:
    _init_database(path, queue_size=queue_size)
    baseline_bytes = _storage_bytes(path)
    planning_samples: list[float] = []
    last_batch_id = ""

    for _ in range(queue_size):
        started = time.perf_counter()
        plan = plan_sequential_batch(path, limit=SNAPSHOT_LIMIT)
        planning_samples.append((time.perf_counter() - started) * 1000)
        if len(plan.selected_wakeup_ids) != 1:
            raise AssertionError(
                f"expected one selected wakeup, got {plan.selected_wakeup_ids!r}"
            )
        last_batch_id = plan.batch_id
        with sqlite3.connect(str(path)) as conn:
            conn.execute(
                """
                UPDATE wakeup_requests
                SET status = 'finished', finished_at = ?, updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (_now(), _now(), plan.selected_wakeup_ids[0]),
            )
            conn.commit()

    with sqlite3.connect(str(path)) as conn:
        decision_rows = int(
            conn.execute("SELECT COUNT(*) FROM dispatch_candidate_decisions").fetchone()[0]
        )
        batch_count = int(
            conn.execute(
                "SELECT COUNT(DISTINCT batch_id) FROM dispatch_candidate_decisions"
            ).fetchone()[0]
        )
        max_observations = int(
            conn.execute(
                """
                SELECT COALESCE(MAX(observations), 0)
                FROM (
                    SELECT COUNT(*) AS observations
                    FROM dispatch_candidate_decisions
                    GROUP BY wakeup_request_id
                )
                """
            ).fetchone()[0]
        )
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])

    final_bytes = _storage_bytes(path)
    expected_rows = expected_decision_rows(queue_size)
    queries = _query_latencies(
        path,
        wakeup_id=f"wakeup-{queue_size - 1:04d}",
        batch_id=last_batch_id,
    )
    return {
        "queue_size": queue_size,
        "snapshot_limit": SNAPSHOT_LIMIT,
        "dispatch_count": queue_size,
        "decision_rows": decision_rows,
        "expected_decision_rows": expected_rows,
        "rows_per_wakeup": round(decision_rows / queue_size, 4),
        "max_observations_per_wakeup": max_observations,
        "baseline_bytes": baseline_bytes,
        "final_bytes": final_bytes,
        "delta_bytes": final_bytes - baseline_bytes,
        "delta_bytes_per_wakeup": round((final_bytes - baseline_bytes) / queue_size, 4),
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "planning_total_ms": round(sum(planning_samples), 4),
        "planning_per_dispatch_median_ms": round(statistics.median(planning_samples), 6),
        "planning_per_dispatch_p95_ms": round(
            sorted(planning_samples)[max(0, math.ceil(0.95 * len(planning_samples)) - 1)],
            6,
        ),
        **queries,
        "checks": {
            "row_formula_exact": decision_rows == expected_rows,
            "one_batch_per_dispatch": batch_count == queue_size,
            "snapshot_bound_respected": max_observations <= SNAPSHOT_LIMIT,
        },
    }


def _median_case(queue_size: int, runs: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = (
        "decision_rows",
        "rows_per_wakeup",
        "delta_bytes",
        "delta_bytes_per_wakeup",
        "planning_total_ms",
        "planning_per_dispatch_median_ms",
        "planning_per_dispatch_p95_ms",
        "wakeup_history_median_ms",
        "wakeup_history_p95_ms",
        "recent_batch_median_ms",
        "recent_batch_p95_ms",
    )
    return {
        "queue_size": queue_size,
        "repeats": len(runs),
        **{
            name: round(float(statistics.median(run[name] for run in runs)), 6)
            for name in metric_names
        },
        "max_observations_per_wakeup": max(
            int(run["max_observations_per_wakeup"]) for run in runs
        ),
        "checks": {
            key: all(bool(run["checks"][key]) for run in runs)
            for key in runs[0]["checks"]
        },
    }


def run_benchmark(
    *,
    workdir: Path | None = None,
    queue_sizes: tuple[int, ...] = DEFAULT_QUEUE_SIZES,
    repeats: int = 3,
) -> dict[str, Any]:
    if not queue_sizes or any(size < 1 for size in queue_sizes):
        raise ValueError("queue_sizes must contain positive integers")
    if repeats < 1:
        raise ValueError("repeats must be >= 1")

    managed_tmp: tempfile.TemporaryDirectory[str] | None = None
    if workdir is None:
        managed_tmp = tempfile.TemporaryDirectory(
            prefix=".tmp_dispatch_growth_", dir=REPO_ROOT
        )
        root = Path(managed_tmp.name)
    else:
        root = Path(workdir)
        root.mkdir(parents=True, exist_ok=True)

    try:
        raw_cases: dict[int, list[dict[str, Any]]] = {}
        for size in queue_sizes:
            raw_cases[size] = [
                _run_case(root / f"n-{size}-repeat-{repeat}" / "aiteam.db", queue_size=size)
                for repeat in range(1, repeats + 1)
            ]
        cases = [_median_case(size, raw_cases[size]) for size in queue_sizes]
    finally:
        if managed_tmp is not None:
            cleanup_error: PermissionError | None = None
            for _ in range(20):
                try:
                    managed_tmp.cleanup()
                    cleanup_error = None
                    break
                except PermissionError as exc:
                    cleanup_error = exc
                    gc.collect()
                    time.sleep(0.1)
            if cleanup_error is not None:
                raise cleanup_error

    largest = cases[-1]
    pressure_gates = {
        "rows_per_wakeup_within_bound": (
            largest["rows_per_wakeup"] <= THRESHOLDS["max_rows_per_wakeup"]
        ),
        "planning_latency_within_bound": (
            largest["planning_per_dispatch_median_ms"]
            <= THRESHOLDS["max_planning_ms_per_dispatch"]
        ),
        "query_latency_within_bound": (
            max(
                largest["wakeup_history_median_ms"],
                largest["recent_batch_median_ms"],
            )
            <= THRESHOLDS["max_query_median_ms"]
        ),
        "storage_growth_within_bound": (
            largest["delta_bytes_per_wakeup"]
            <= THRESHOLDS["max_delta_bytes_per_wakeup"]
        ),
    }
    structural_checks = {
        f"queue_{case['queue_size']}_{name}": value
        for case in cases
        for name, value in case["checks"].items()
    }
    pressure_observed = not all(pressure_gates.values())
    return {
        "schema_version": 1,
        "benchmark": "dispatch_candidate_decision_growth",
        "case_version": CASE_VERSION,
        "generated_at": _now(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
        },
        "contract": {
            "models_or_network_used": False,
            "project_databases_read": False,
            "queue_sizes": list(queue_sizes),
            "repeats": repeats,
            "snapshot_limit": SNAPSHOT_LIMIT,
            "query_repetitions": QUERY_REPETITIONS,
            "thresholds_preregistered_in_code": THRESHOLDS,
            "constructs_not_measured": [
                "long-running workspace fragmentation",
                "concurrent writer contention",
                "production hardware outside this machine",
            ],
        },
        "cases": cases,
        "checks": {**structural_checks, **pressure_gates},
        "conclusion": {
            "structural_amplification_bounded": all(structural_checks.values()),
            "operational_pressure_observed": pressure_observed,
            "retention_implementation_allowed": pressure_observed,
            "decision": (
                "design_table_specific_retention"
                if pressure_observed
                else "retain_additive_log_and_monitor"
            ),
            "reason": (
                "al menos un threshold preregistrado fue superado"
                if pressure_observed
                else "la amplificación exacta permanece dentro de los thresholds preregistrados"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    report = run_benchmark(workdir=args.workdir, repeats=args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["conclusion"], ensure_ascii=False))
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
