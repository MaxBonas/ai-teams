"""Audita si la telemetría durable justifica activar paralelismo por canal.

No ejecuta modelos ni modifica SQLite. La provenance de
``dispatch_candidate_decisions`` es evidencia exacta; las bases anteriores a
esa tabla conservan valor histórico, pero se etiquetan como aproximadas.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.policies import (  # noqa: E402
    WORK_SLOT_ROLES,
    parallel_batch_max,
    parallel_channels_enabled,
)

BLOCKED_REASONS = {"dependency_blocked", "checkout_active"}


def _timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _seconds(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    return max(0.0, (later - earlier).total_seconds())


def _json_object(value: object) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _root_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT id, parent_id FROM issues").fetchall()
    parents = {str(row["id"]): str(row["parent_id"] or "") for row in rows}
    roots: dict[str, str] = {}
    for issue_id in parents:
        current = issue_id
        seen: set[str] = set()
        while parents.get(current) and current not in seen:
            seen.add(current)
            current = parents[current]
        roots[issue_id] = current
    return roots


def _provider_key(run: dict[str, Any]) -> str:
    if run["role"] == "test_runner":
        return "builtin"
    return str(run.get("provider") or run.get("adapter_type") or "unknown").strip().lower()


def _capacity_key(item: dict[str, Any]) -> str:
    return str(item.get("capacity_pool") or _provider_key(item)).strip().lower()


def _parallel_eligible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["agent_id"] == right["agent_id"]:
        return False
    if left["root_issue_id"] == right["root_issue_id"]:
        return False
    left_pool = _capacity_key(left)
    right_pool = _capacity_key(right)
    if left_pool != "builtin" and left_pool == right_pool:
        return False
    return not (left["role"] in WORK_SLOT_ROLES and right["role"] in WORK_SLOT_ROLES)


def _load_runs(
    conn: sqlite3.Connection,
    *,
    tables: set[str],
    roots: dict[str, str],
) -> tuple[list[dict[str, Any]], int]:
    has_wakeups = "wakeup_requests" in tables
    wake_join = "LEFT JOIN wakeup_requests w ON w.id = r.wakeup_request_id" if has_wakeups else ""
    requested_column = "w.requested_at" if has_wakeups else "NULL"
    rows = conn.execute(
        f"""
        SELECT r.id, r.agent_id, r.issue_id, r.wakeup_request_id,
               r.adapter_type, r.provider, r.status, r.error_code,
               r.started_at, r.finished_at, a.role,
               {requested_column} AS requested_at
        FROM runs r
        LEFT JOIN agents a ON a.id = r.agent_id
        {wake_join}
        ORDER BY r.started_at, r.id
        """
    ).fetchall()
    runs: list[dict[str, Any]] = []
    for row in rows:
        started = _timestamp(row["started_at"])
        finished = _timestamp(row["finished_at"])
        if started is None or finished is None or finished < started:
            continue
        issue_id = str(row["issue_id"] or "")
        runs.append({
            "id": str(row["id"]),
            "wakeup_request_id": str(row["wakeup_request_id"] or ""),
            "agent_id": str(row["agent_id"] or ""),
            "issue_id": issue_id,
            "root_issue_id": roots.get(issue_id, issue_id or f"run:{row['id']}"),
            "role": str(row["role"] or "").strip().lower(),
            "adapter_type": str(row["adapter_type"] or ""),
            "provider": str(row["provider"] or ""),
            "status": str(row["status"] or ""),
            "error_code": str(row["error_code"] or ""),
            "requested": _timestamp(row["requested_at"]),
            "started": started,
            "finished": finished,
        })
    return runs, len(rows)


def _base_metrics(path: Path, runs: list[dict[str, Any]], recorded_runs: int) -> dict[str, Any]:
    waits = [
        wait
        for run in runs
        if (wait := _seconds(run["started"], run["requested"])) is not None
    ]
    return {
        "database": str(path).replace("\\", "/"),
        "recorded_runs": recorded_runs,
        "timed_runs": len(runs),
        "excluded_untimed_runs": recorded_runs - len(runs),
        "root_count": len({run["root_issue_id"] for run in runs}),
        "provider_count": len({_provider_key(run) for run in runs if _provider_key(run) != "builtin"}),
        "capacity_pool_count": None,
        "work_slot_runs": sum(run["role"] in WORK_SLOT_ROLES for run in runs),
        "total_queue_wait_samples": len(waits),
        "total_queue_wait_seconds": round(sum(waits), 3),
        "total_queue_wait_seconds_median": round(statistics.median(waits), 3) if waits else None,
        # Alias de lectura para consumidores v1.
        "queue_wait_samples": len(waits),
        "queue_wait_seconds_median": round(statistics.median(waits), 3) if waits else None,
        "rate_limit_errors": sum(
            run["error_code"] in {"subscription_cli_usage_limit", "provider_rate_limited", "rate_limited"}
            for run in runs
        ),
    }


def _eligible_overlap_count(runs: list[dict[str, Any]]) -> int:
    count = 0
    for index, left in enumerate(runs):
        for right in runs[index + 1:]:
            overlaps = left["started"] < right["finished"] and right["started"] < left["finished"]
            if overlaps and _parallel_eligible(left, right):
                count += 1
    return count


def _audit_approximate(base: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    eligible_waits: list[float] = []
    for queued in runs:
        if queued["requested"] is None:
            continue
        blockers = [
            max(0.0, (active["finished"] - max(queued["requested"], active["started"])).total_seconds())
            for active in runs
            if active["started"] <= queued["started"]
            and queued["requested"] < active["finished"] <= queued["started"]
            and _parallel_eligible(active, queued)
        ]
        if blockers and max(blockers) > 0:
            eligible_waits.append(max(blockers))
    return {
        **base,
        "evidence_quality": "approximate",
        "parallelizable_wait_runs": len(eligible_waits),
        "parallelizable_wait_seconds": round(sum(eligible_waits), 3),
        "eligible_serial_wait_runs": len(eligible_waits),
        "eligible_serial_wait_seconds": round(sum(eligible_waits), 3),
        "eligible_overlap_pairs": _eligible_overlap_count(runs),
        "dispatch_evidence": {
            "candidate_decisions": 0,
            "dispatch_batches": 0,
            "candidate_snapshot_batches": 0,
            "full_queue_batches": 0,
            "selected_runs_covered": 0,
            "selected_run_coverage_ratio": 0.0 if runs else None,
            "excluded_by_reason": {},
        },
        "limitations": [
            "sin dispatch_candidate_decisions: raíz y proveedor sólo aproximan la elegibilidad histórica",
            "no puede separar dependencia o checkout de la espera inferida",
        ],
    }


def _audit_exact(
    conn: sqlite3.Connection,
    base: dict[str, Any],
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    raw = conn.execute(
        """
        SELECT batch_id, dispatch_mode, wakeup_request_id, agent_id, issue_id,
               root_issue_id, role, capacity_pool, is_work_slot, requested_at,
               ready_at, considered_at, decision, reason, details_json
        FROM dispatch_candidate_decisions
        ORDER BY considered_at, batch_id, requested_at, wakeup_request_id
        """
    ).fetchall()
    decisions = [
        {
            **dict(row),
            "wakeup_request_id": str(row["wakeup_request_id"] or ""),
            "agent_id": str(row["agent_id"] or ""),
            "root_issue_id": str(row["root_issue_id"] or ""),
            "role": str(row["role"] or "").strip().lower(),
            "capacity_pool": str(row["capacity_pool"] or "unknown"),
            "ready": _timestamp(row["ready_at"]),
            "considered": _timestamp(row["considered_at"]),
            "details": _json_object(row["details_json"]),
        }
        for row in raw
    ]
    batches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_wakeup: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in decisions:
        batches[str(item["batch_id"])].append(item)
        by_wakeup[item["wakeup_request_id"]].append(item)
    full_queue_batches = sum(any(
        item["details"].get("snapshot_contract") == "candidate_queue_prefix_v1"
        for item in items
    ) for items in batches.values())
    has_full_snapshot = full_queue_batches > 0
    quality = "exact" if has_full_snapshot else "partial_exact"

    selected_by_wakeup = {
        wakeup_id: next((item for item in items if item["decision"] == "selected"), None)
        for wakeup_id, items in by_wakeup.items()
    }
    selected_by_wakeup = {key: value for key, value in selected_by_wakeup.items() if value}
    covered_runs = [run for run in runs if run["wakeup_request_id"] in selected_by_wakeup]
    exact_runs: list[dict[str, Any]] = []
    ready_waits: list[float] = []
    for run in covered_runs:
        selected = selected_by_wakeup[run["wakeup_request_id"]]
        exact_run = {
            **run,
            "root_issue_id": selected["root_issue_id"],
            "role": selected["role"],
            "capacity_pool": selected["capacity_pool"],
        }
        exact_runs.append(exact_run)
        wait = _seconds(run["started"], selected["ready"])
        if wait is not None:
            ready_waits.append(wait)

    opportunities: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for items in batches.values():
        selected = [item for item in items if item["decision"] == "selected"]
        if len(selected) != 1 or selected[0]["dispatch_mode"] != "sequential":
            continue
        active = selected[0]
        for waiting in items:
            if waiting["reason"] != "sequential_mode":
                continue
            if _parallel_eligible(active, waiting) and waiting["considered"] is not None:
                previous = opportunities.get(waiting["wakeup_request_id"])
                if previous is None or waiting["considered"] < previous[0]:
                    opportunities[waiting["wakeup_request_id"]] = (waiting["considered"], waiting)

    parallel_waits: list[float] = []
    for wakeup_id, (opportunity_at, _) in opportunities.items():
        later_selection = selected_by_wakeup.get(wakeup_id)
        if later_selection is None:
            continue
        wait = _seconds(later_selection["considered"], opportunity_at)
        if wait is not None:
            parallel_waits.append(wait)

    reasons = Counter(str(item["reason"]) for item in decisions if item["decision"] == "rejected")
    coverage_ratio = round(len(covered_runs) / len(runs), 4) if runs else None
    return {
        **base,
        "capacity_pool_count": len({
            item["capacity_pool"]
            for item in decisions
            if item["capacity_pool"] != "builtin"
        }),
        "evidence_quality": quality,
        "ready_wait_samples": len(ready_waits),
        "ready_wait_seconds": round(sum(ready_waits), 3),
        "parallelizable_wait_runs": len(parallel_waits),
        "parallelizable_wait_seconds": round(sum(parallel_waits), 3),
        "eligible_serial_wait_runs": len(parallel_waits),
        "eligible_serial_wait_seconds": round(sum(parallel_waits), 3),
        "eligible_overlap_pairs": _eligible_overlap_count(exact_runs),
        "dispatch_evidence": {
            "candidate_decisions": len(decisions),
            "dispatch_batches": len(batches),
            "candidate_snapshot_batches": full_queue_batches,
            "full_queue_batches": full_queue_batches,
            "selected_runs_covered": len(covered_runs),
            "selected_run_coverage_ratio": coverage_ratio,
            "excluded_by_reason": {
                reason: reasons.get(reason, 0) for reason in sorted(BLOCKED_REASONS)
            },
        },
        "limitations": (
            [] if quality == "exact" else [
                "hay decisiones persistidas, pero ningún batch demuestra un snapshot completo de la cola"
            ]
        ),
    }


def audit_database(path: Path) -> dict[str, Any]:
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"runs", "issues", "agents"}.issubset(tables):
            raise ValueError(f"{path}: schema v2 incompleto")
        roots = _root_map(conn)
        runs, recorded_runs = _load_runs(conn, tables=tables, roots=roots)
        base = _base_metrics(path, runs, recorded_runs)
        if "dispatch_candidate_decisions" not in tables:
            return _audit_approximate(base, runs)
        decision_count = conn.execute("SELECT COUNT(*) FROM dispatch_candidate_decisions").fetchone()[0]
        if not decision_count:
            return _audit_approximate(base, runs)
        return _audit_exact(conn, base, runs)


def audit_databases(paths: Iterable[Path]) -> dict[str, Any]:
    sources = [audit_database(path) for path in paths]
    exact_sources = [item for item in sources if item["evidence_quality"] == "exact"]
    approximate_signal_runs = sum(
        item["parallelizable_wait_runs"]
        for item in sources
        if item["evidence_quality"] == "approximate"
    )
    exact_wait_runs = sum(item["parallelizable_wait_runs"] for item in exact_sources)
    exact_wait_seconds = round(sum(item["parallelizable_wait_seconds"] for item in exact_sources), 3)
    all_wait_runs = sum(item["parallelizable_wait_runs"] for item in sources)
    all_wait_seconds = round(sum(item["parallelizable_wait_seconds"] for item in sources), 3)
    qualities = {item["evidence_quality"] for item in sources}
    aggregate_quality = next(iter(qualities)) if len(qualities) == 1 else "mixed"
    contention_trigger_observed = exact_wait_runs > 0 and exact_wait_seconds > 0
    return {
        "schema_version": 2,
        "audit": "parallel_channel_capacity",
        "evidence_quality": aggregate_quality,
        "policy": {
            "default_enabled": False,
            "environment_override_active": parallel_channels_enabled(),
            "batch_max": parallel_batch_max(),
            "constraints": [
                "distinct_agents",
                "distinct_root_issues",
                "distinct_non_builtin_capacity_pools",
                "at_most_one_workspace_work_slot",
            ],
        },
        "sources": sources,
        "aggregate": {
            "database_count": len(sources),
            "exact_database_count": len(exact_sources),
            "partial_exact_database_count": sum(item["evidence_quality"] == "partial_exact" for item in sources),
            "approximate_database_count": sum(item["evidence_quality"] == "approximate" for item in sources),
            "recorded_runs": sum(item["recorded_runs"] for item in sources),
            "timed_runs": sum(item["timed_runs"] for item in sources),
            "excluded_untimed_runs": sum(item["excluded_untimed_runs"] for item in sources),
            "total_queue_wait_seconds": round(sum(item["total_queue_wait_seconds"] for item in sources), 3),
            "single_root_databases": sum(item["root_count"] <= 1 for item in sources),
            "single_provider_databases": sum(item["provider_count"] <= 1 for item in sources),
            "parallelizable_wait_runs": all_wait_runs,
            "parallelizable_wait_seconds": all_wait_seconds,
            "exact_parallelizable_wait_runs": exact_wait_runs,
            "exact_parallelizable_wait_seconds": exact_wait_seconds,
            "approximate_parallelizable_wait_runs": approximate_signal_runs,
            "eligible_serial_wait_runs": all_wait_runs,
            "eligible_serial_wait_seconds": all_wait_seconds,
            "eligible_overlap_pairs": sum(item["eligible_overlap_pairs"] for item in sources),
            "rate_limit_errors": sum(item["rate_limit_errors"] for item in sources),
        },
        "conclusion": {
            "contention_trigger_observed": contention_trigger_observed,
            "approximate_contention_signal_observed": approximate_signal_runs > 0,
            "evidence_sufficient_for_default_enable": False,
            "default_change_allowed": False,
            "decision": "retain_sequential_default",
            "reason": (
                "la provenance exacta muestra espera paralelizable; falta A/B de calidad, cuota y liveness"
                if contention_trigger_observed
                else "no existe todavía espera paralelizable mayor que cero demostrada con provenance exacta"
            ),
            "reopen_when": [
                "exact_parallelizable_wait_runs > 0 en proyectos reales comparables",
                "A/B secuencial-paralelo con misma cola y resultados",
                "sin regresión de checkout, evidencia, cuota ni liveness",
            ],
        },
        "limitations": [
            "las fuentes approximate no pueden disparar por sí solas un canario vivo",
            "sin A/B secuencial-paralelo no se puede autorizar un cambio de default",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("databases", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_databases(args.databases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["conclusion"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
