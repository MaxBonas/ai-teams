"""Canario determinista del contrato durable Lead + Quorum, sin LLM ni red.

Ejercita los recibos v2 que el workflow real debe producir:

  plan revisión A → dos aportes independientes → gate → síntesis revisión B
  → disposiciones del Lead → plan aceptado terminal, sin ejecutar código.

No simula calidad lingüística. Protege las invariantes mecánicas sobre las que
se conectará Scheduler/RunExecutor en la siguiente fase.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.db.migration import SCHEMA_PATH  # noqa: E402
from aiteam.db.quorum_sessions import (  # noqa: E402
    accept_quorum_synthesis,
    create_quorum_session,
    evaluate_quorum_session,
    record_quorum_contribution,
)


def _init(workdir: Path) -> Path:
    runtime = workdir / ".aiteam"
    runtime.mkdir(parents=True, exist_ok=True)
    db_path = runtime / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal:q', 'Canario quorum')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES "
            "('role:lead', 'lead', 'Lead', 'manual'),"
            "('role:q1', 'reviewer', 'Auditor OpenAI', 'openai_api'),"
            "('role:q2', 'reviewer', 'Auditor Google', 'gemini_api')"
        )
        conn.execute(
            """
            INSERT INTO issues (
                id, goal_id, title, status, role, assignee_agent_id, metadata_json
            ) VALUES (
                'issue:q', 'goal:q', 'Elegir arquitectura durable', 'in_progress',
                'lead', 'role:lead', '{"profile":"lead_quorum"}'
            )
            """
        )
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status, provider, model, channel) VALUES "
            "('run:q1', 'role:q1', 'issue:q', 'completed', 'openai', 'stub-a', 'api'),"
            "('run:q2', 'role:q2', 'issue:q', 'completed', 'google', 'stub-b', 'api'),"
            "('run:synthesis', 'role:lead', 'issue:q', 'completed', 'local', 'stub-lead', 'local')"
        )
        conn.execute(
            """
            INSERT INTO issue_documents (
                id, issue_id, key, title, body, current_revision_id, revision_number
            ) VALUES ('doc:plan', 'issue:q', 'plan', 'Plan', 'Plan inicial A', 'rev:a', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO issue_document_revisions (
                id, document_id, issue_id, key, title, body, revision_number
            ) VALUES ('rev:a', 'doc:plan', 'issue:q', 'plan', 'Plan', 'Plan inicial A', 1)
            """
        )
        conn.commit()
    return db_path


def run_canary(workdir: Path) -> dict[str, Any]:
    db_path = _init(workdir)
    session = create_quorum_session(
        db_path, issue_id="issue:q", base_plan_revision_id="rev:a"
    )
    for ordinal, (agent, run_id, provider, finding_id) in enumerate(
        (
            ("role:q1", "run:q1", "openai", "risk:persistence"),
            ("role:q2", "run:q2", "google", "risk:migration"),
        ),
        start=1,
    ):
        record_quorum_contribution(
            db_path,
            session_id=session["id"],
            agent_id=agent,
            run_id=run_id,
            ordinal=ordinal,
            provider=provider,
            model=f"stub-{ordinal}",
            channel="api",
            result="changes_requested",
            evidence=f"Auditor {ordinal} revisó exactamente rev:a.",
            findings=[{"id": finding_id, "severity": "medium", "summary": "riesgo concreto"}],
        )
    gate = evaluate_quorum_session(db_path, session_id=session["id"])
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE issue_documents SET body='Plan consolidado B', current_revision_id='rev:b', revision_number=2 "
            "WHERE id='doc:plan'"
        )
        conn.execute(
            """
            INSERT INTO issue_document_revisions (
                id, document_id, issue_id, key, title, body, revision_number, created_by_run_id
            ) VALUES (
                'rev:b', 'doc:plan', 'issue:q', 'plan', 'Plan',
                'Plan consolidado B', 2, 'run:synthesis'
            )
            """
        )
        conn.commit()
    accepted = accept_quorum_synthesis(
        db_path,
        session_id=session["id"],
        synthesis_run_id="run:synthesis",
        final_plan_revision_id="rev:b",
        dispositions=[
            {"finding_id": "risk:persistence", "decision": "accept", "rationale": "reduce pérdida"},
            {"finding_id": "risk:migration", "decision": "qualify", "rationale": "migración gradual"},
        ],
    )

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        metadata = json.loads(conn.execute(
            "SELECT metadata_json FROM issues WHERE id='issue:q'"
        ).fetchone()[0])
        contributions = conn.execute(
            "SELECT provider, valid FROM quorum_contributions WHERE session_id=? ORDER BY ordinal",
            (session["id"],),
        ).fetchall()
        checks = {
            "gate_ready": gate["ready"],
            "two_valid_contributions": len(contributions) == 2 and all(row["valid"] for row in contributions),
            "provider_diversity": len({row["provider"] for row in contributions}) == 2,
            "session_accepted": accepted["status"] == "accepted",
            "final_plan_revision_recorded": accepted["final_plan_revision_id"] == "rev:b",
            "planning_completed_without_execution": (
                metadata.get("profile") == "lead_quorum"
                and metadata.get("planning_status") == "accepted_plan"
                and conn.execute("SELECT status FROM issues WHERE id='issue:q'").fetchone()[0] == "done"
            ),
            "no_execution_continuation": conn.execute(
                "SELECT COUNT(*) FROM wakeup_requests WHERE reason='quorum_accepted' AND status='queued'"
            ).fetchone()[0] == 0,
            "no_live_runs": conn.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')"
            ).fetchone()[0] == 0,
            "no_orphan_wakeups": conn.execute(
                "SELECT COUNT(*) FROM wakeup_requests WHERE status IN ('claimed','running')"
            ).fetchone()[0] == 0,
        }
    return {"ok": all(checks.values()), "checks": checks, "db": str(db_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, default=None)
    args = parser.parse_args()
    if args.workdir:
        workdir = args.workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        report = run_canary(workdir)
    else:
        with tempfile.TemporaryDirectory(prefix="aiteam-quorum-canary-", ignore_cleanup_errors=True) as tmp:
            report = run_canary(Path(tmp))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nCANARIO QUORUM {'OK' if report['ok'] else 'ROTO'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
