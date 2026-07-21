"""Benchmark offline de calidad incremental de planes ``lead_quorum``.

Compara la revisión A que vieron los auditores con la revisión B aceptada. La
rúbrica vive fuera del workspace entregado a los modelos y combina cobertura
específica del caso con obligaciones generales de un plan durable.

Uso sobre una ejecución real ya terminada (cero llamadas LLM adicionales):
    python scripts/benchmark_quorum_plans.py --db <db> --rubric <rubrica.json>

También acepta ``--base-plan`` y ``--final-plan`` para calibrar el scorer con
fixtures o planes exportados sin abrir una DB de proyecto.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.quorum_quality import evaluate_plan_depth  # noqa: E402
from scripts.benchmark_integrity import quorum_evaluation_contract  # noqa: E402


GENERAL_CRITERIA = (
    {"id": "objective", "weight": 1, "patterns": [r"objetiv", r"goal|propósito"]},
    {"id": "closure", "weight": 1, "patterns": [r"criteri.{0,20}(cierre|aceptaci)", r"done|completad"]},
    {"id": "decomposition", "weight": 1, "patterns": [r"sub-?issue|tarea|fase|paso"]},
    {"id": "ownership", "weight": 1, "patterns": [r"owner|responsable|reporta|asignad"]},
    {"id": "evidence", "weight": 1, "patterns": [r"evidencia|recibo|artefacto|diff|test"]},
    {"id": "risk", "weight": 1, "patterns": [r"riesgo|fallo|rotura|rollback"]},
    {"id": "escalation", "weight": 1, "patterns": [r"escal|bloque|intervenci.{0,10}human|usuario"]},
    {"id": "continuation", "weight": 1, "patterns": [r"siguiente run|continuaci|wakeup|heartbeat|reanud"]},
)


def _matches(text: str, patterns: list[str]) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)]


def score_plan(plan: str, rubric: dict[str, Any]) -> dict[str, Any]:
    """Puntúa cobertura verificable; no intenta juzgar estilo ni verdad factual."""
    text = str(plan or "").strip()
    criteria = [*GENERAL_CRITERIA, *rubric.get("criteria", [])]
    results: list[dict[str, Any]] = []
    earned = 0.0
    available = 0.0
    hard_failures: list[str] = []
    for criterion in criteria:
        weight = float(criterion.get("weight", 1))
        patterns = [str(value) for value in criterion.get("patterns", [])]
        minimum = int(criterion.get("min_matches", 1))
        found = _matches(text, patterns)
        passed = len(found) >= minimum
        available += weight
        earned += weight if passed else 0
        if criterion.get("required") and not passed:
            hard_failures.append(str(criterion["id"]))
        results.append({
            "id": criterion["id"], "weight": weight, "passed": passed,
            "matched": found, "required": bool(criterion.get("required")),
        })

    penalties: list[dict[str, Any]] = []
    penalty_total = 0.0
    for penalty in rubric.get("penalties", []):
        found = _matches(text, [str(value) for value in penalty.get("patterns", [])])
        applied = float(penalty.get("weight", 0)) if found else 0.0
        penalty_total += applied
        penalties.append({"id": penalty["id"], "applied": applied, "matched": found})

    raw = max(0.0, earned - penalty_total)
    return {
        "score": round(raw, 2),
        "max_score": round(available, 2),
        "score_pct": round((raw / available * 100) if available else 0.0, 2),
        "hard_failures": hard_failures,
        "passes_hard_gate": not hard_failures,
        "word_count": len(re.findall(r"\S+", text)),
        "criteria": results,
        "penalties": penalties,
    }


def load_quorum_pair(db_path: Path, *, issue_id: str | None = None) -> dict[str, Any]:
    """Extrae Plan A/B y economía/provenance de una sesión terminal real."""
    db_path = Path(db_path).resolve()
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        where = "WHERE qs.issue_id = ?" if issue_id else ""
        params = (issue_id,) if issue_id else ()
        row = conn.execute(
            f"""
            SELECT qs.* FROM quorum_sessions qs
            {where}
            ORDER BY qs.created_at DESC LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None:
            raise ValueError("no quorum session found")
        session = dict(row)
        if not session.get("final_plan_revision_id"):
            raise ValueError(f"quorum session {session['id']} has no accepted final plan")
        revisions = conn.execute(
            "SELECT id, body, revision_number FROM issue_document_revisions WHERE id IN (?, ?)",
            (session["base_plan_revision_id"], session["final_plan_revision_id"]),
        ).fetchall()
        by_id = {str(item["id"]): dict(item) for item in revisions}
        missing = [revision for revision in (session["base_plan_revision_id"], session["final_plan_revision_id"]) if revision not in by_id]
        if missing:
            raise ValueError(f"missing plan revisions: {', '.join(missing)}")
        contributions = [dict(item) for item in conn.execute(
            """
            SELECT qc.ordinal, qc.provider, qc.model, qc.channel, qc.valid, qc.run_id,
                   COALESCE(SUM(ce.input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(ce.output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(ce.cost_cents), 0) AS cost_cents
            FROM quorum_contributions qc
            LEFT JOIN cost_events ce ON ce.run_id = qc.run_id
            WHERE qc.session_id = ?
            GROUP BY qc.id ORDER BY qc.ordinal
            """,
            (session["id"],),
        ).fetchall()]
    return {
        "session": {key: session.get(key) for key in (
            "id", "issue_id", "status", "base_plan_revision_id", "final_plan_revision_id",
            "requested_contributions", "min_valid_contributions", "skipped_reason",
        )},
        "base_plan": by_id[session["base_plan_revision_id"]]["body"],
        "final_plan": by_id[session["final_plan_revision_id"]]["body"],
        "contributions": contributions,
    }


def diagnose_incomplete_quorum(db_path: Path, rubric: dict[str, Any], *, issue_id: str | None = None) -> dict[str, Any]:
    """Return actionable evidence when a real benchmark cannot reach Plan B."""
    with sqlite3.connect(str(Path(db_path).resolve())) as conn:
        conn.row_factory = sqlite3.Row
        where = "WHERE issue_id=?" if issue_id else ""
        params = (issue_id,) if issue_id else ()
        session = conn.execute(
            f"SELECT * FROM quorum_sessions {where} ORDER BY created_at DESC LIMIT 1", params
        ).fetchone()
        if session is None:
            return {"session": None, "base": None, "contributions": [], "auditor_runs": []}
        session_dict = dict(session)
        base = conn.execute(
            "SELECT body FROM issue_document_revisions WHERE id=?",
            (session_dict["base_plan_revision_id"],),
        ).fetchone()
        contributions = [dict(row) for row in conn.execute(
            "SELECT ordinal,provider,model,channel,valid,run_id FROM quorum_contributions "
            "WHERE session_id=? ORDER BY ordinal",
            (session_dict["id"],),
        ).fetchall()]
        auditor_runs = [dict(row) for row in conn.execute(
            """
            SELECT r.id,r.agent_id,r.status,r.provider,r.model,r.channel,r.error_code,r.error,
                   r.usage_json
            FROM runs r JOIN issues i ON i.id=r.issue_id
            WHERE i.parent_id=? AND json_extract(i.metadata_json,'$.quorum_session_id')=?
            ORDER BY r.created_at
            """,
            (session_dict["issue_id"], session_dict["id"]),
        ).fetchall()]
    return {
        "session": {key: session_dict.get(key) for key in (
            "id", "issue_id", "status", "base_plan_revision_id", "final_plan_revision_id",
            "requested_contributions", "min_valid_contributions", "skipped_reason",
        )},
        "base": score_plan(str(base["body"]), rubric) if base else None,
        "contributions": contributions,
        "auditor_runs": auditor_runs,
    }


def evaluate_pair(base_plan: str, final_plan: str, rubric: dict[str, Any], *, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    base = score_plan(base_plan, rubric)
    final = score_plan(final_plan, rubric)
    delta = round(final["score_pct"] - base["score_pct"], 2)
    evaluation_contract = quorum_evaluation_contract(
        base_structural=evaluate_plan_depth(base_plan),
        final_structural=evaluate_plan_depth(final_plan),
    )
    return {
        "benchmark": "lead_quorum_plan_quality",
        "rubric_id": rubric.get("id"),
        "base": base,
        "final": final,
        "delta_score_pct": delta,
        "improved": delta > 0,
        "regressed": delta < 0,
        "hard_gate_improved": (not base["passes_hard_gate"]) and final["passes_hard_gate"],
        "evaluation_contract": evaluation_contract,
        "provenance": provenance or {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--db", type=Path)
    source.add_argument("--base-plan", type=Path)
    source.add_argument("--goal", type=Path, help="ejecuta un lead_quorum real con este objetivo")
    parser.add_argument("--final-plan", type=Path)
    parser.add_argument("--issue-id")
    parser.add_argument("--rubric", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--profiles", default="codex_subscription,openai_api")
    parser.add_argument("--max-ticks", type=int, default=12)
    parser.add_argument("--max-minutes", type=float, default=20.0)
    args = parser.parse_args()
    rubric = json.loads(args.rubric.read_text(encoding="utf-8"))
    runtime_metrics: dict[str, Any] | None = None
    if args.goal:
        from scripts.benchmark_vs_codex import run_team_arm

        workdir = (args.workdir or (
            REPO_ROOT / "runtime" / "bench" / f"quorum-{time.strftime('%Y%m%d-%H%M%S')}"
        )).resolve()
        workspace = workdir / "lead_quorum"
        runtime_metrics = run_team_arm(
            workspace,
            args.goal.read_text(encoding="utf-8"),
            profile_ids=[item.strip() for item in args.profiles.split(",") if item.strip()],
            run_profile="lead_quorum",
            max_ticks=args.max_ticks,
            max_minutes=args.max_minutes,
        )
        try:
            pair = load_quorum_pair(workspace / ".aiteam" / "aiteam.db", issue_id=args.issue_id)
        except ValueError as exc:
            diagnostics = diagnose_incomplete_quorum(
                workspace / ".aiteam" / "aiteam.db", rubric, issue_id=args.issue_id
            )
            report = {
                "benchmark": "lead_quorum_plan_quality",
                "rubric_id": rubric.get("id"),
                "completed": False,
                "failure": str(exc),
                "diagnostics": diagnostics,
                "provenance": {"runtime": runtime_metrics},
            }
            serialized = json.dumps(report, indent=2, ensure_ascii=False)
            print(serialized)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(serialized + "\n", encoding="utf-8")
            return 2
    elif args.db:
        try:
            pair = load_quorum_pair(args.db, issue_id=args.issue_id)
        except ValueError as exc:
            report = {
                "benchmark": "lead_quorum_plan_quality",
                "rubric_id": rubric.get("id"),
                "completed": False,
                "failure": str(exc),
                "diagnostics": diagnose_incomplete_quorum(args.db, rubric, issue_id=args.issue_id),
                "provenance": {},
            }
            serialized = json.dumps(report, indent=2, ensure_ascii=False)
            print(serialized)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(serialized + "\n", encoding="utf-8")
            return 2
    else:
        pair = None
    if pair is not None:
        report = evaluate_pair(pair["base_plan"], pair["final_plan"], rubric, provenance={
            "session": pair["session"], "contributions": pair["contributions"],
            "runtime": runtime_metrics,
        })
    else:
        if not args.final_plan:
            parser.error("--final-plan is required with --base-plan")
        report = evaluate_pair(
            args.base_plan.read_text(encoding="utf-8"),
            args.final_plan.read_text(encoding="utf-8"),
            rubric,
        )
    serialized = json.dumps(report, indent=2, ensure_ascii=False)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if report["final"]["passes_hard_gate"] and not report["regressed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
