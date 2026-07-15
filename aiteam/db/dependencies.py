from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.db.wakeups import enqueue_wakeup


def add_dependency(
    db_path: Path,
    *,
    issue_id: str,
    depends_on_issue_id: str,
    relation_type: str = "blocks",
) -> dict[str, Any]:
    """Record that *issue_id* is blocked by *depends_on_issue_id*.

    Returns the inserted row. Raises ValueError on self-dependency.
    Raises sqlite3.IntegrityError on duplicate.
    """
    if issue_id == depends_on_issue_id:
        raise ValueError("an issue cannot depend on itself")
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO issue_dependencies (issue_id, depends_on_issue_id, relation_type)
            VALUES (?, ?, ?)
            """,
            (issue_id, depends_on_issue_id, relation_type),
        )
        row = conn.execute(
            "SELECT * FROM issue_dependencies WHERE issue_id = ? AND depends_on_issue_id = ?",
            (issue_id, depends_on_issue_id),
        ).fetchone()
        return dict(row)


def add_dependency_if_missing(
    db_path: Path,
    *,
    issue_id: str,
    depends_on_issue_id: str,
    relation_type: str = "blocks",
) -> bool:
    """Record a dependency if it does not already exist."""
    if issue_id == depends_on_issue_id:
        return False
    with contextlib.closing(_connect(db_path)) as conn:
        result = conn.execute(
            """
            INSERT OR IGNORE INTO issue_dependencies (issue_id, depends_on_issue_id, relation_type)
            VALUES (?, ?, ?)
            """,
            (issue_id, depends_on_issue_id, relation_type),
        )
        return result.rowcount > 0


def remove_dependency(
    db_path: Path,
    *,
    issue_id: str,
    depends_on_issue_id: str,
) -> bool:
    """Remove the dependency. Returns True if a row was deleted."""
    with contextlib.closing(_connect(db_path)) as conn:
        result = conn.execute(
            "DELETE FROM issue_dependencies WHERE issue_id = ? AND depends_on_issue_id = ?",
            (issue_id, depends_on_issue_id),
        )
        return result.rowcount > 0


def list_dependencies(db_path: Path, *, issue_id: str) -> list[dict[str, Any]]:
    """Return issues that *issue_id* depends on (i.e. its blockers)."""
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT d.issue_id, d.depends_on_issue_id, d.relation_type, d.created_at,
                   i.title AS blocker_title, i.status AS blocker_status
            FROM issue_dependencies d
            JOIN issues i ON i.id = d.depends_on_issue_id
            WHERE d.issue_id = ?
            ORDER BY d.created_at ASC
            """,
            (issue_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_blocked_issues(db_path: Path, *, depends_on_issue_id: str) -> list[dict[str, Any]]:
    """Return issues blocked by *depends_on_issue_id*."""
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT d.issue_id, d.depends_on_issue_id, d.relation_type, d.created_at,
                   i.title AS blocked_title, i.status AS blocked_status,
                   i.assignee_agent_id
            FROM issue_dependencies d
            JOIN issues i ON i.id = d.issue_id
            WHERE d.depends_on_issue_id = ?
            ORDER BY d.created_at ASC
            """,
            (depends_on_issue_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def all_blockers_resolved(db_path: Path, *, issue_id: str) -> bool:
    """Return True if every blocker of *issue_id* is terminal (done/cancelled)."""
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS unresolved
            FROM issue_dependencies d
            JOIN issues i ON i.id = d.depends_on_issue_id
            WHERE d.issue_id = ?
              AND i.status NOT IN ('done', 'cancelled')
            """,
            (issue_id,),
        ).fetchone()
        return int(row["unresolved"]) == 0


def unresolved_blockers(db_path: Path, *, issue_id: str) -> list[dict[str, Any]]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT d.depends_on_issue_id AS issue_id,
                   i.title,
                   i.status,
                   i.assignee_agent_id
            FROM issue_dependencies d
            JOIN issues i ON i.id = d.depends_on_issue_id
            WHERE d.issue_id = ?
              AND i.status NOT IN ('done', 'cancelled')
            ORDER BY d.created_at ASC
            """,
            (issue_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def sync_default_child_dependencies(db_path: Path, *, parent_issue_id: str) -> list[dict[str, str]]:
    """Apply the default programming workflow dependencies for a parent issue.

    Engineer issues produce the implementation artifact. Reviewer, QA and
    test_runner issues should not run before that artifact exists, so they
    depend on every open engineering child under the same parent. Sin
    test_runner aquí, el builtin despertaba al asignarse y ejecutaba la suite
    contra un workspace todavía vacío (visto en vivo en CLI Gastos,
    2026-07-15: run 'blocked' 41s antes de que el engineer entregara).
    """
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, role, status
            FROM issues
            WHERE parent_id = ?
              AND status != 'cancelled'
            ORDER BY created_at ASC, rowid ASC
            """,
            (parent_issue_id,),
        ).fetchall()
    children = [dict(row) for row in rows]
    # El test_designer escribe la suite desde la spec EN PARALELO al engineer
    # (sin dependencia entre ellos — no debe ver la implementación); ambos son
    # prerrequisito de reviewer/qa/test_runner: el runner ejecuta las dos
    # suites y el reviewer juzga con todo el material delante.
    engineering = [
        row["id"]
        for row in children
        if str(row.get("role") or "").strip().lower() in {"engineer", "software_engineer", "test_designer"}
    ]
    dependents = [
        row["id"]
        for row in children
        if str(row.get("role") or "").strip().lower() in {"reviewer", "code_reviewer", "qa", "qa_engineer", "test_runner"}
    ]
    created: list[dict[str, str]] = []
    for dependent_id in dependents:
        for engineer_id in engineering:
            if add_dependency_if_missing(
                db_path,
                issue_id=dependent_id,
                depends_on_issue_id=engineer_id,
            ):
                created.append({"issue_id": dependent_id, "depends_on_issue_id": engineer_id})
    return created


def resolve_blocker_wakeups(
    db_path: Path,
    *,
    resolved_issue_id: str,
    source_run_id: str | None = None,
) -> list[str]:
    """When *resolved_issue_id* reaches a terminal state, wake every issue it was blocking.

    Only wakes issues where ALL blockers are now resolved.
    Returns list of issue_ids that received a wakeup.
    """
    blocked = list_blocked_issues(db_path, depends_on_issue_id=resolved_issue_id)
    woken: list[str] = []
    for row in blocked:
        blocked_issue_id = row["issue_id"]
        assignee = row.get("assignee_agent_id")
        if not assignee:
            continue
        # A terminal dependent has nothing left to do — waking it produced
        # zombie runs posting verdicts on done issues (capa-2 fan-out bursts).
        if str(row.get("blocked_status") or "") in {"done", "cancelled"}:
            continue
        if not all_blockers_resolved(db_path, issue_id=blocked_issue_id):
            continue
        enqueue_wakeup(
            db_path,
            agent_id=assignee,
            source="dependency",
            reason="blockers_resolved",
            trigger_detail=f"blocker:{resolved_issue_id}:done",
            payload={
                "issue_id": blocked_issue_id,
                "wake_reason": "blockers_resolved",
                "resolved_blocker_id": resolved_issue_id,
                "source_run_id": source_run_id or "",
            },
            idempotency_key=f"blockers_resolved:{blocked_issue_id}:{resolved_issue_id}",
        )
        woken.append(blocked_issue_id)
    return woken


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
