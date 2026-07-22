"""Audita si la telemetría durable justifica activar paralelismo por canal.

No ejecuta modelos ni modifica SQLite. Busca contención entre wakeups ya
encolados que el selector productivo habría podido solapar: agentes, raíces y
proveedores distintos, con como máximo un rol que edite o verifique workspace.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
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


def _parallel_eligible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["agent_id"] == right["agent_id"]:
        return False
    if left["root_issue_id"] == right["root_issue_id"]:
        return False
    left_provider = _provider_key(left)
    right_provider = _provider_key(right)
    if left_provider != "builtin" and left_provider == right_provider:
        return False
    return not (left["role"] in WORK_SLOT_ROLES and right["role"] in WORK_SLOT_ROLES)


def audit_database(path: Path) -> dict[str, Any]:
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"runs", "issues", "agents"}.issubset(tables):
            raise ValueError(f"{path}: schema v2 incompleto")
        roots = _root_map(conn)
        has_wakeups = "wakeup_requests" in tables
        wake_join = (
            "LEFT JOIN wakeup_requests w ON w.id = r.wakeup_request_id"
            if has_wakeups else ""
        )
        requested_column = "w.requested_at" if has_wakeups else "NULL"
        rows = conn.execute(
            f"""
            SELECT r.id, r.agent_id, r.issue_id, r.adapter_type, r.provider,
                   r.status, r.error_code, r.started_at, r.finished_at,
                   a.role, {requested_column} AS requested_at
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

    queue_wait_seconds = [
        max(0.0, (run["started"] - run["requested"]).total_seconds())
        for run in runs if run["requested"] is not None
    ]
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

    eligible_overlaps = 0
    for index, left in enumerate(runs):
        for right in runs[index + 1:]:
            overlaps = left["started"] < right["finished"] and right["started"] < left["finished"]
            if overlaps and _parallel_eligible(left, right):
                eligible_overlaps += 1

    rate_limit_errors = sum(
        run["error_code"] in {"subscription_cli_usage_limit", "provider_rate_limited", "rate_limited"}
        for run in runs
    )
    return {
        "database": str(path).replace("\\", "/"),
        "recorded_runs": len(rows),
        "timed_runs": len(runs),
        "excluded_untimed_runs": len(rows) - len(runs),
        "root_count": len({run["root_issue_id"] for run in runs}),
        "provider_count": len({_provider_key(run) for run in runs if _provider_key(run) != "builtin"}),
        "work_slot_runs": sum(run["role"] in WORK_SLOT_ROLES for run in runs),
        "queue_wait_samples": len(queue_wait_seconds),
        "queue_wait_seconds_median": round(statistics.median(queue_wait_seconds), 3) if queue_wait_seconds else None,
        "eligible_serial_wait_runs": len(eligible_waits),
        "eligible_serial_wait_seconds": round(sum(eligible_waits), 3),
        "eligible_overlap_pairs": eligible_overlaps,
        "rate_limit_errors": rate_limit_errors,
    }


def audit_databases(paths: Iterable[Path]) -> dict[str, Any]:
    sources = [audit_database(path) for path in paths]
    eligible_wait_runs = sum(item["eligible_serial_wait_runs"] for item in sources)
    eligible_wait_seconds = round(sum(item["eligible_serial_wait_seconds"] for item in sources), 3)
    eligible_overlaps = sum(item["eligible_overlap_pairs"] for item in sources)
    rate_limit_errors = sum(item["rate_limit_errors"] for item in sources)
    contention_trigger_observed = eligible_wait_runs > 0 and eligible_wait_seconds > 0
    return {
        "schema_version": 1,
        "audit": "parallel_channel_capacity",
        "policy": {
            "default_enabled": False,
            "environment_override_active": parallel_channels_enabled(),
            "batch_max": parallel_batch_max(),
            "constraints": [
                "distinct_agents",
                "distinct_root_issues",
                "distinct_non_builtin_providers",
                "at_most_one_workspace_work_slot",
            ],
        },
        "sources": sources,
        "aggregate": {
            "database_count": len(sources),
            "recorded_runs": sum(item["recorded_runs"] for item in sources),
            "timed_runs": sum(item["timed_runs"] for item in sources),
            "excluded_untimed_runs": sum(item["excluded_untimed_runs"] for item in sources),
            "single_root_databases": sum(item["root_count"] <= 1 for item in sources),
            "single_provider_databases": sum(item["provider_count"] <= 1 for item in sources),
            "eligible_serial_wait_runs": eligible_wait_runs,
            "eligible_serial_wait_seconds": eligible_wait_seconds,
            "eligible_overlap_pairs": eligible_overlaps,
            "rate_limit_errors": rate_limit_errors,
        },
        "conclusion": {
            "contention_trigger_observed": contention_trigger_observed,
            "evidence_sufficient_for_default_enable": False,
            "default_change_allowed": False,
            "decision": "retain_sequential_default",
            "reason": (
                "hay contención elegible; falta A/B de calidad, cuota y liveness antes de activar"
                if contention_trigger_observed
                else "la muestra no contiene espera entre raíces y proveedores que el selector pudiera solapar"
            ),
            "reopen_when": [
                "eligible_serial_wait_runs > 0 en proyectos reales comparables",
                "A/B secuencial-paralelo con misma cola y resultados",
                "sin regresión de checkout, evidencia, cuota ni liveness",
            ],
        },
        "limitations": [
            "el proveedor persistido aproxima la clave de capacidad usada por el scheduler",
            "el histórico retenido puede no representar proyectos multi-raíz y multi-proveedor",
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
