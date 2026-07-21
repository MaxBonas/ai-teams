r"""Evalúa coordinación, economía y liveness de una DB de proyecto AI Teams.

No ejecuta agentes ni modifica la DB. Produce métricas comparables para una DB
``.aiteam/aiteam.db`` o un workspace que la contenga.

Uso:
    .\scripts\python_local.bat scripts\orchestrator_evals.py <workspace-o-db>
    .\scripts\python_local.bat scripts\orchestrator_evals.py <path> --output eval.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def resolve_db(path: Path) -> Path:
    candidate = Path(path).resolve()
    if candidate.is_file():
        return candidate
    for relative in (Path(".aiteam/aiteam.db"), Path("runtime/aiteam.db"), Path("aiteam.db")):
        db_path = candidate / relative
        if db_path.is_file():
            return db_path
    raise FileNotFoundError(f"No se encontró aiteam.db bajo {candidate}")


def _portable_path(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve())).replace("\\", "/")
    except ValueError:
        return str(resolved)


def evaluate_db(path: Path) -> dict[str, Any]:
    db_path = resolve_db(path)
    uri = f"file:{db_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {"issues", "runs", "wakeup_requests", "agent_reports", "cost_events", "activity_log"}
        missing = sorted(required - tables)
        if missing:
            raise ValueError(f"DB sin schema de evals; faltan tablas: {', '.join(missing)}")

        issue_counts = _counts(conn, "SELECT status, COUNT(*) n FROM issues GROUP BY status")
        run_counts = _counts(conn, "SELECT status, COUNT(*) n FROM runs GROUP BY status")
        wakeup_counts = _counts(conn, "SELECT status, COUNT(*) n FROM wakeup_requests GROUP BY status")

        totals = conn.execute(
            """
            SELECT COUNT(*) events,
                   COALESCE(SUM(input_tokens), 0) input_tokens,
                   COALESCE(SUM(output_tokens), 0) output_tokens,
                   COALESCE(SUM(cost_cents), 0) cost_cents
            FROM cost_events
            """
        ).fetchone()
        accepted_roots = int(conn.execute(
            "SELECT COUNT(*) FROM issues WHERE parent_id IS NULL AND status='done'"
        ).fetchone()[0])

        wake_rows = conn.execute(
            """
            SELECT COALESCE(
                       json_extract(payload_json, '$.issue_id'),
                       json_extract(payload_json, '$.task_id'),
                       '<sin-issue>'
                   ) issue_id,
                   COUNT(*) n
            FROM wakeup_requests
            GROUP BY issue_id
            ORDER BY n DESC, issue_id
            """
        ).fetchall()
        wake_values = [int(row["n"]) for row in wake_rows]

        repeated_runs = [
            dict(row)
            for row in conn.execute(
                """
                SELECT agent_id, issue_id, COUNT(*) runs
                FROM runs
                WHERE issue_id IS NOT NULL
                GROUP BY agent_id, issue_id
                HAVING COUNT(*) > 1
                ORDER BY runs DESC, agent_id, issue_id
                """
            ).fetchall()
        ]

        report_counts = _counts(
            conn,
            "SELECT LOWER(result) status, COUNT(*) n FROM agent_reports"
            " WHERE valid=1 AND is_assignee=1 GROUP BY LOWER(result)",
        )
        test_failures = int(conn.execute(
            "SELECT COUNT(*) FROM agent_reports WHERE valid=1 AND is_assignee=1"
            " AND LOWER(agent_role)='test_runner' AND LOWER(result) IN ('failed','blocked','partial')"
        ).fetchone()[0])
        reviewer_rejections = int(conn.execute(
            "SELECT COUNT(*) FROM agent_reports WHERE valid=1 AND is_assignee=1"
            " AND LOWER(agent_role) IN ('reviewer','code_reviewer')"
            " AND LOWER(result) IN ('changes_requested','blocked','partial','failed')"
        ).fetchone()[0])
        contradicted_approvals = int(conn.execute(
            """
            SELECT COUNT(*)
            FROM agent_reports reviewer
            JOIN issues reviewer_issue ON reviewer_issue.id = reviewer.issue_id
            JOIN runs reviewer_run ON reviewer_run.id = reviewer.run_id
            WHERE reviewer.valid=1 AND reviewer.is_assignee=1
              AND LOWER(reviewer.agent_role) IN ('reviewer','code_reviewer')
              AND LOWER(reviewer.result) IN ('approved','done','completed')
              AND EXISTS (
                  SELECT 1
                  FROM agent_reports tester
                  JOIN issues tester_issue ON tester_issue.id = tester.issue_id
                  JOIN runs tester_run ON tester_run.id = tester.run_id
                  WHERE tester.valid=1 AND tester.is_assignee=1
                    AND LOWER(tester.agent_role)='test_runner'
                    AND LOWER(tester.result) IN ('failed','blocked','partial')
                    AND COALESCE(tester_issue.parent_id, tester_issue.id)
                        = COALESCE(reviewer_issue.parent_id, reviewer_issue.id)
                    AND tester_run.rowid > reviewer_run.rowid
              )
            """
        ).fetchone()[0])

        activity_counts = _counts(
            conn,
            "SELECT action status, COUNT(*) n FROM activity_log GROUP BY action",
        )
        digest_rows = conn.execute(
            "SELECT json_extract(payload_json, '$.digest') digest"
            " FROM activity_log WHERE action='workspace.context_digest' ORDER BY rowid"
        ).fetchall()
        digests = [str(row["digest"] or "") for row in digest_rows]
        unchanged_digest_reuses = sum(
            1 for previous, current in zip(digests, digests[1:]) if current and current == previous
        )

        fix_issues = int(conn.execute(
            """
            SELECT COUNT(*) FROM issues
            WHERE LOWER(role) IN ('engineer','software_engineer')
              AND (LOWER(title) LIKE 'fix:%'
                   OR json_extract(metadata_json, '$.source')='reviewer_changes_requested_fix')
            """
        ).fetchone()[0])
        terminal_waste = int(conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status='skipped' AND error_code='issue_terminal'"
        ).fetchone()[0])
        nonterminal_runs = int(conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')"
        ).fetchone()[0])
        stale_nonterminal_runs = int(conn.execute(
            """
            SELECT COUNT(*) FROM runs
            WHERE status IN ('queued','running')
              AND datetime(COALESCE(started_at, created_at)) < datetime('now', '-30 minutes')
            """
        ).fetchone()[0])
        claimed_or_running_wakeups = int(conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE status IN ('claimed','running')"
        ).fetchone()[0])
        stale_claimed_or_running_wakeups = int(conn.execute(
            """
            SELECT COUNT(*) FROM wakeup_requests
            WHERE status IN ('claimed','running')
              AND datetime(COALESCE(claimed_at, created_at)) < datetime('now', '-30 minutes')
            """
        ).fetchone()[0])
        stranded_roots = int(conn.execute(
            """
            WITH RECURSIVE issue_tree(root_id, issue_id) AS (
                SELECT id, id
                FROM issues
                WHERE parent_id IS NULL
                UNION
                SELECT tree.root_id, child.id
                FROM issue_tree tree
                JOIN issues child ON child.parent_id = tree.issue_id
            )
            SELECT COUNT(*)
            FROM issues root
            WHERE root.parent_id IS NULL
              AND root.status NOT IN ('done', 'cancelled')
              AND NOT EXISTS (
                  SELECT 1
                  FROM issue_tree tree
                  JOIN runs run ON run.issue_id = tree.issue_id
                  WHERE tree.root_id = root.id
                    AND run.status IN ('queued', 'running')
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM issue_tree tree
                  JOIN wakeup_requests wake
                    ON COALESCE(
                        json_extract(wake.payload_json, '$.issue_id'),
                        json_extract(wake.payload_json, '$.task_id')
                    ) = tree.issue_id
                  WHERE tree.root_id = root.id
                    AND wake.status IN ('queued', 'claimed', 'running')
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM issue_tree tree
                  JOIN issue_thread_interactions interaction
                    ON interaction.issue_id = tree.issue_id
                  WHERE tree.root_id = root.id
                    AND interaction.status NOT IN ('accepted', 'rejected', 'answered', 'cancelled', 'expired')
              )
            """
        ).fetchone()[0])
        quorum = _evaluate_quorum(conn, tables)

    input_tokens = int(totals["input_tokens"])
    output_tokens = int(totals["output_tokens"])
    return {
        "db": _portable_path(db_path),
        "outcome": {
            "issues_by_status": issue_counts,
            "accepted_root_issues": accepted_roots,
        },
        "economy": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_cents": int(totals["cost_cents"]),
            "tokens_per_accepted_root": (
                round((input_tokens + output_tokens) / accepted_roots, 2)
                if accepted_roots else None
            ),
        },
        "coordination": {
            "runs_by_status": run_counts,
            "wakeups_by_status": wakeup_counts,
            "wakeups_per_issue_avg": round(sum(wake_values) / len(wake_values), 2) if wake_values else 0.0,
            "wakeups_per_issue_max": max(wake_values, default=0),
            "repeated_agent_issue_runs": repeated_runs,
            "fix_issues": fix_issues,
            "test_failure_reports": test_failures,
            "reviewer_rejection_reports": reviewer_rejections,
            "approvals_contradicted_by_later_test_failure": contradicted_approvals,
            "terminal_issue_skips": terminal_waste,
            "lead_unblock_attempts": int(activity_counts.get("lead.unblock_attempted", 0)),
        },
        "context": {
            "workspace_digest_events": len(digests),
            "unchanged_digest_reuses": unchanged_digest_reuses,
            "context_curator_issues": int(issue_counts_for_role(db_path, "context_curator")),
        },
        "reports": report_counts,
        "quorum": quorum,
        "liveness": {
            "nonterminal_runs": nonterminal_runs,
            "stale_nonterminal_runs": stale_nonterminal_runs,
            "claimed_or_running_wakeups": claimed_or_running_wakeups,
            "stale_claimed_or_running_wakeups": stale_claimed_or_running_wakeups,
            "stranded_nonterminal_roots": stranded_roots,
            "healthy": (
                stale_nonterminal_runs == 0
                and stale_claimed_or_running_wakeups == 0
                and stranded_roots == 0
            ),
        },
    }


def _evaluate_quorum(conn: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    required = {"quorum_sessions", "quorum_contributions"}
    if not required <= tables:
        return {"available": False}

    sessions = conn.execute(
        "SELECT id, status, dispositions_json FROM quorum_sessions ORDER BY created_at, id"
    ).fetchall()
    status_counts: dict[str, int] = {}
    accepted_without_provider_diversity = 0
    accepted_with_unresolved_findings = 0
    for session in sessions:
        status = str(session["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
        if status != "accepted":
            continue
        contributions = conn.execute(
            "SELECT provider, findings_json FROM quorum_contributions "
            "WHERE session_id=? AND valid=1 ORDER BY ordinal",
            (session["id"],),
        ).fetchall()
        providers = {str(row["provider"] or "").strip() for row in contributions}
        providers.discard("")
        if len(providers) < 2:
            accepted_without_provider_diversity += 1
        finding_ids = {
            str(finding.get("id") or "").strip()
            for row in contributions
            for finding in _json_list(row["findings_json"])
            if isinstance(finding, dict) and str(finding.get("id") or "").strip()
        }
        disposition_ids = {
            str(item.get("finding_id") or "").strip()
            for item in _json_list(session["dispositions_json"])
            if isinstance(item, dict) and str(item.get("finding_id") or "").strip()
        }
        if finding_ids - disposition_ids:
            accepted_with_unresolved_findings += 1

    valid, invalid = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN valid=1 THEN 1 ELSE 0 END),0), "
        "COALESCE(SUM(CASE WHEN valid=0 THEN 1 ELSE 0 END),0) FROM quorum_contributions"
    ).fetchone()
    return {
        "available": True,
        "sessions_by_status": status_counts,
        "valid_contributions": int(valid),
        "invalid_contributions": int(invalid),
        "accepted_without_provider_diversity": accepted_without_provider_diversity,
        "accepted_with_unresolved_findings": accepted_with_unresolved_findings,
        "healthy": (
            accepted_without_provider_diversity == 0
            and accepted_with_unresolved_findings == 0
            and int(status_counts.get("failed", 0)) == 0
        ),
    }


def _json_list(raw: Any) -> list[Any]:
    try:
        value = json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def issue_counts_for_role(db_path: Path, role: str) -> int:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM issues WHERE LOWER(role)=LOWER(?)", (role,)
        ).fetchone()[0])


def _counts(conn: sqlite3.Connection, query: str) -> dict[str, int]:
    return {str(row["status"]): int(row["n"]) for row in conn.execute(query).fetchall()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = evaluate_db(args.path)
    serialized = json.dumps(report, indent=2, ensure_ascii=False)
    print(serialized)
    if args.output is not None:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
