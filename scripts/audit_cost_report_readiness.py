"""Audita si una SQLite tiene volumen suficiente para el informe económico.

El auditor es read-only y no calcula rankings ni extrapola ahorro. Cada entrega
es un issue raíz junto con todo su subárbol. El gate exige una muestra mínima
por perfil, cobertura temporal y señales de calidad con provenance confiable.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.audit_parallel_trigger_inventory import discover_databases  # noqa: E402

MIN_TERMINAL_DELIVERIES_PER_PROFILE = 5
MIN_TIMED_RUN_COVERAGE = 0.80
MIN_COST_PROVENANCE_COVERAGE = 0.80
MIN_QUALITY_DELIVERY_COVERAGE = 0.80
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "lost", "skipped"}
TERMINAL_DELIVERY_STATUSES = {"done", "cancelled"}
QUALITY_ROLES = {"reviewer", "code_reviewer", "qa", "qa_engineer", "test_runner"}
POSITIVE_QUALITY_RESULTS = {"approved", "done", "completed"}
NEGATIVE_QUALITY_RESULTS = {"changes_requested", "blocked", "partial", "failed"}


def _display_path(path: Path) -> str:
    try:
        value = path.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        value = path.resolve()
    return str(value).replace("\\", "/")


def _json_object(value: object) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _root_map(issues: list[dict[str, Any]]) -> dict[str, str]:
    parents = {str(row["id"]): str(row.get("parent_id") or "") for row in issues}
    roots: dict[str, str] = {}
    for issue_id in parents:
        current = issue_id
        seen: set[str] = set()
        while parents.get(current) and current not in seen:
            seen.add(current)
            current = parents[current]
        roots[issue_id] = current
    return roots


def _profile(issue: dict[str, Any]) -> str:
    metadata = _json_object(issue.get("metadata_json"))
    value = str(metadata.get("profile") or "").strip().lower()
    return value if value in {"solo_lead", "lead_quorum", "full_team"} else "unknown"


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def audit_database(path: Path) -> dict[str, Any]:
    """Devuelve sólo cobertura y conteos; nunca una conclusión económica."""
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"issues", "runs"}.issubset(tables):
            raise ValueError("control_plane_schema_missing")
        issues = [dict(row) for row in conn.execute(
            "SELECT id, parent_id, title, status, metadata_json FROM issues"
        )]
        runs = [dict(row) for row in conn.execute(
            """
            SELECT id, issue_id, status, started_at, finished_at
            FROM runs
            """
        )]
        reports = []
        if "agent_reports" in tables:
            reports = [dict(row) for row in conn.execute(
                """
                SELECT issue_id, agent_role, result, created_at, rowid
                FROM agent_reports
                WHERE valid = 1 AND is_assignee = 1
                ORDER BY created_at DESC, rowid DESC
                """
            )]
        cost_event_run_ids: set[str] = set()
        if "cost_events" in tables:
            cost_event_run_ids = {
                str(row[0])
                for row in conn.execute("SELECT DISTINCT run_id FROM cost_events WHERE run_id IS NOT NULL")
            }

    roots = _root_map(issues)
    root_rows = [row for row in issues if not row.get("parent_id")]
    profile_roots: dict[str, list[str]] = defaultdict(list)
    for row in root_rows:
        profile_roots[_profile(row)].append(str(row["id"]))

    terminal_runs = [row for row in runs if str(row.get("status")) in TERMINAL_RUN_STATUSES]
    runs_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in terminal_runs:
        issue_id = str(run.get("issue_id") or "")
        root_id = roots.get(issue_id)
        if root_id:
            runs_by_root[root_id].append(run)

    latest_quality_by_issue: dict[str, dict[str, Any]] = {}
    for report in reports:
        issue_id = str(report.get("issue_id") or "")
        role = str(report.get("agent_role") or "").lower()
        result = str(report.get("result") or "").lower()
        if issue_id not in latest_quality_by_issue and role in QUALITY_ROLES and (
            result in POSITIVE_QUALITY_RESULTS or result in NEGATIVE_QUALITY_RESULTS
        ):
            latest_quality_by_issue[issue_id] = report

    deliveries: list[dict[str, Any]] = []
    for root in root_rows:
        root_id = str(root["id"])
        delivery_runs = runs_by_root.get(root_id, [])
        subtree_ids = {issue_id for issue_id, mapped_root in roots.items() if mapped_root == root_id}
        quality_signals = [latest_quality_by_issue[item] for item in subtree_ids if item in latest_quality_by_issue]
        deliveries.append({
            "profile": _profile(root),
            "status": str(root.get("status") or ""),
            "terminal": str(root.get("status") or "") in TERMINAL_DELIVERY_STATUSES,
            "run_count": len(delivery_runs),
            "timed_run_count": sum(bool(run.get("started_at") and run.get("finished_at")) for run in delivery_runs),
            "cost_provenance_run_count": sum(str(run["id"]) in cost_event_run_ids for run in delivery_runs),
            "quality_signal_count": len(quality_signals),
            "quality_pass_count": sum(str(item.get("result") or "").lower() in POSITIVE_QUALITY_RESULTS for item in quality_signals),
        })

    by_profile: list[dict[str, Any]] = []
    for profile in sorted(profile_roots):
        selected = [item for item in deliveries if item["profile"] == profile]
        terminal = [item for item in selected if item["terminal"]]
        run_count = sum(int(item["run_count"]) for item in terminal)
        timed_run_count = sum(int(item["timed_run_count"]) for item in terminal)
        cost_provenance_run_count = sum(
            int(item["cost_provenance_run_count"]) for item in terminal
        )
        quality_deliveries = sum(int(item["quality_signal_count"]) > 0 for item in terminal)
        quality_signal_count = sum(int(item["quality_signal_count"]) for item in terminal)
        quality_pass_count = sum(int(item["quality_pass_count"]) for item in terminal)
        exclusions: list[str] = []
        if profile == "unknown":
            exclusions.append("unknown_profile")
        if len(terminal) < MIN_TERMINAL_DELIVERIES_PER_PROFILE:
            exclusions.append("insufficient_terminal_deliveries")
        if _ratio(timed_run_count, run_count) < MIN_TIMED_RUN_COVERAGE:
            exclusions.append("insufficient_latency_coverage")
        if _ratio(cost_provenance_run_count, run_count) < MIN_COST_PROVENANCE_COVERAGE:
            exclusions.append("insufficient_cost_provenance")
        if _ratio(quality_deliveries, len(terminal)) < MIN_QUALITY_DELIVERY_COVERAGE:
            exclusions.append("insufficient_quality_coverage")
        by_profile.append({
            "profile": profile,
            "delivery_count": len(selected),
            "terminal_delivery_count": len(terminal),
            "run_count": run_count,
            "timed_run_count": timed_run_count,
            "timed_run_coverage": _ratio(timed_run_count, run_count),
            "cost_provenance_run_count": cost_provenance_run_count,
            "cost_provenance_coverage": _ratio(cost_provenance_run_count, run_count),
            "quality_delivery_count": quality_deliveries,
            "quality_delivery_coverage": _ratio(quality_deliveries, len(terminal)),
            "quality_signal_count": quality_signal_count,
            "quality_pass_count": quality_pass_count,
            "ready": not exclusions,
            "exclusions": exclusions,
        })

    return {
        "database": _display_path(path),
        "delivery_count": len(deliveries),
        "terminal_delivery_count": sum(bool(item["terminal"]) for item in deliveries),
        "terminal_run_count": len(terminal_runs),
        "profiles": by_profile,
        "report_ready": bool(by_profile) and all(item["ready"] for item in by_profile),
    }


def audit_inventory(root: Path) -> dict[str, Any]:
    databases, discovery_errors, pruned = discover_databases(root)
    sources: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    for path in databases:
        if not path.exists() or path.stat().st_size == 0:
            invalid.append({"database": _display_path(path), "error": "empty_database"})
            continue
        try:
            sources.append(audit_database(path))
        except (sqlite3.Error, ValueError) as exc:
            invalid.append({"database": _display_path(path), "error": str(exc) or type(exc).__name__})
    ready = [item for item in sources if item["report_ready"]]
    terminal_deliveries_by_profile: dict[str, int] = defaultdict(int)
    exclusion_counts: dict[str, int] = defaultdict(int)
    for source in sources:
        for profile in source["profiles"]:
            terminal_deliveries_by_profile[str(profile["profile"])] += int(
                profile["terminal_delivery_count"]
            )
            for exclusion in profile["exclusions"]:
                exclusion_counts[str(exclusion)] += 1
    return {
        "schema_version": 1,
        "audit": "cost_report_readiness",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": _display_path(root),
        "contract": {
            "read_only": True,
            "models_or_network_used": False,
            "issue_content_exported": False,
            "delivery_definition": "root_issue_and_recursive_subtree",
            "minimum_terminal_deliveries_per_profile": MIN_TERMINAL_DELIVERIES_PER_PROFILE,
            "minimum_timed_run_coverage": MIN_TIMED_RUN_COVERAGE,
            "minimum_cost_provenance_coverage": MIN_COST_PROVENANCE_COVERAGE,
            "minimum_quality_delivery_coverage": MIN_QUALITY_DELIVERY_COVERAGE,
            "quality_provenance": "latest_valid_assignee_report_from_review_qa_or_test_role",
            "actual_cost_provenance": "runs.actual_cost_cents_with_cost_events_coverage_reported_separately",
            "estimated_savings_is_not_actual_cost": True,
        },
        "summary": {
            "discovered_database_count": len(databases),
            "audited_database_count": len(sources),
            "invalid_database_count": len(invalid),
            "discovery_error_count": len(discovery_errors),
            "pruned_ephemeral_directory_count": len(pruned),
            "ready_project_count": len(ready),
            "terminal_deliveries_by_profile": dict(sorted(terminal_deliveries_by_profile.items())),
            "profile_exclusion_counts": dict(sorted(exclusion_counts.items())),
        },
        "ready_projects": ready,
        "sources": sources,
        "invalid_databases": invalid,
        "discovery_errors": discovery_errors,
        "conclusion": {
            "cost_report_allowed": bool(ready),
            "decision": "build_report" if ready else "collect_more_deliveries",
            "reason": (
                "al menos un proyecto satisface volumen y cobertura por perfil"
                if ready
                else "ningún proyecto retenido satisface todavía volumen y cobertura por perfil"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT / "runtime")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    report = audit_inventory(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["conclusion"], ensure_ascii=False))
    return int(bool(args.require_ready) and not report["conclusion"]["cost_report_allowed"])


if __name__ == "__main__":
    raise SystemExit(main())
