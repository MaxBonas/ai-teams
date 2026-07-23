"""Canario capa-2 de síntesis causal mediante un ``context_curator`` real.

Ejecuta exactamente una run durable scheduler->executor, persiste el bloque en
SQLite y lo evalúa después con una rúbrica oculta para el modelo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers.workspace import _initialize_project_runtime  # noqa: E402
from aiteam.adapters.registry import build_default_registry  # noqa: E402
from aiteam.db.agents import create_agent  # noqa: E402
from aiteam.db.comments import create_comment  # noqa: E402
from aiteam.db.documents import get_context_summary  # noqa: E402
from aiteam.db.issues import create_issue  # noqa: E402
from aiteam.db.wakeups import enqueue_wakeup  # noqa: E402
from aiteam.db.wake_payload import build_context_curation_target  # noqa: E402
from aiteam.heartbeat.executor import RunExecutor  # noqa: E402
from aiteam.heartbeat.scheduler import HeartbeatScheduler  # noqa: E402
from aiteam.project_adapters import (  # noqa: E402
    choose_adapter_for_role,
    project_profiles,
    write_project_adapter_policy,
)
from aiteam.tools.catalog import default_capabilities_for_role  # noqa: E402
from scripts.context_summary_evals import evaluate_summary  # noqa: E402


CONTRACT_VERSION = "two_causal_slices_three_seeds_each_v1"
EXPECTED_CELLS = {
    (case_id, seed)
    for case_id in ("auth_migration", "queue_rollout")
    for seed in (1, 2, 3)
}


def bootstrap_profile_ids(profile_id: str) -> list[str]:
    """Keep local-only role calibration separate from bootstrap Lead eligibility."""
    if profile_id.startswith("local_"):
        return [profile_id, "codex_subscription"]
    return [profile_id]


def _expanded_thread(source: str, *, min_chars: int) -> str:
    source = source.strip()
    noise = (
        "\nRegistro repetido sin decisión nueva: saludos, nombres temporales, "
        "estimaciones descartadas y fragmentos de log no accionables."
    )
    return source + noise * max(0, (min_chars - len(source) + len(noise) - 1) // len(noise))


def run_canary(
    *, source: str, rubric: dict[str, Any], profile_id: str, workspace: Path, min_chars: int,
    model: str | None = None, reasoning_effort: str | None = None, seed: int = 1,
) -> dict[str, Any]:
    runtime = workspace / ".aiteam"
    runtime.mkdir(parents=True, exist_ok=True)
    write_project_adapter_policy(
        runtime, profile_ids=bootstrap_profile_ids(profile_id)
    )
    # solo_lead evita que el bootstrap cree el Tier 3 que este harness configura
    # explícitamente con el perfil bajo prueba.
    _initialize_project_runtime(workspace, run_profile="solo_lead")
    db = runtime / "aiteam.db"
    profiles = project_profiles(runtime)
    selection = choose_adapter_for_role("context_curator", "cheap", profiles)
    if not selection or selection.get("adapter_profile_id") != profile_id:
        raise RuntimeError(f"profile not available: {profile_id}")
    adapter_config = dict(selection.get("adapter_config") or {})
    if model:
        adapter_config["model"] = model
        selection["model"] = model
    if reasoning_effort:
        adapter_config["model_reasoning_effort"] = reasoning_effort
    elif profile_id.startswith("local_"):
        adapter_config["model_reasoning_effort"] = "none"
    create_agent(
        db,
        agent_id="role:context_curator",
        role="context_curator",
        name="Context Curator",
        seniority="cheap",
        adapter_type=str(selection["adapter_type"]),
        adapter_config=adapter_config,
        capabilities=default_capabilities_for_role("context_curator"),
        supervisor_agent_id="role:lead",
        metadata={
            "adapter_profile_id": selection.get("adapter_profile_id"),
            "source": "context_canary",
            "seed": seed,
        },
    )
    create_issue(
        db,
        issue_id="issue:curator",
        goal_id="goal:intake",
        parent_id="issue:intake",
        title="Curar contexto causal",
        description="Sintetiza exclusivamente payload.context_curation_target.",
        status="todo",
        role="context_curator",
        complexity="low",
        assignee_agent_id="role:context_curator",
    )
    expanded = _expanded_thread(source, min_chars=min_chars)
    for offset in range(0, len(expanded), 2_000):
        create_comment(
            db,
            issue_id="issue:intake",
            author_user_id="benchmark",
            body=expanded[offset:offset + 2_000],
        )
    durable_target = build_context_curation_target(db, issue_id="issue:intake")
    if not durable_target:
        raise RuntimeError("durable context slice was not created")
    durable_source = "".join(str(item["body"]) for item in durable_target["comments"])
    enqueue_wakeup(
        db,
        agent_id="role:context_curator",
        source="context_canary",
        reason="delegated_work",
        payload={"issue_id": "issue:curator", "wake_reason": "delegated_work"},
        idempotency_key=f"context-canary:curator:{seed}",
    )

    scheduler = HeartbeatScheduler(db)
    started = time.time()
    executor = RunExecutor(db, build_default_registry())
    attempts = 0
    for _ in range(2):
        dispatch = scheduler.dispatch_next(agent_id="role:context_curator")
        if dispatch is None:
            break
        attempts += 1
        executor.execute(dispatch)
        with sqlite3.connect(db) as conn:
            issue_status_now = conn.execute(
                "SELECT status FROM issues WHERE id='issue:curator'"
            ).fetchone()[0]
        if issue_status_now in {"done", "blocked", "cancelled"}:
            break
    if attempts == 0:
        raise RuntimeError("curator wakeup was not dispatchable")
    wall_seconds = round(time.time() - started, 1)

    summary_doc = get_context_summary(db, issue_id="issue:intake")
    summary = ""
    causal_units: list[dict[str, Any]] = []
    if summary_doc and summary_doc.get("blocks"):
        last_block = summary_doc["blocks"][-1]
        summary = str(last_block.get("summary_markdown") or "")
        if isinstance(last_block.get("causal_units"), list):
            causal_units = last_block["causal_units"]
    evaluation = evaluate_summary(durable_source, summary, rubric, causal_units=causal_units)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        issue_status = conn.execute(
            "SELECT status FROM issues WHERE id='issue:curator'"
        ).fetchone()[0]
        run = dict(conn.execute(
            "SELECT id,status,error_code,error,agent_id FROM runs WHERE issue_id='issue:curator' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone())
        usage = conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),"
            "COALESCE(SUM(cost_cents),0) FROM cost_events c "
            "JOIN runs r ON r.id=c.run_id WHERE r.issue_id='issue:curator'",
        ).fetchone()
    case_id = str(rubric.get("id") or "").split("_causal_")[0]
    usage_observed = bool(int(usage[0]) or int(usage[1]))
    return {
        **evaluation,
        "contract_version": CONTRACT_VERSION,
        "case_id": case_id,
        "seed": seed,
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "rubric_sha256": hashlib.sha256(
            json.dumps(rubric, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "profile_id": profile_id,
        "execution_config": {
            "role": "context_curator",
            "profile_id": profile_id,
            "model": adapter_config.get("model"),
            "reasoning_effort_override": adapter_config.get("model_reasoning_effort"),
            "source": "benchmark_runtime",
        },
        "adapter": {
            "type": selection.get("adapter_type"),
            "model": selection.get("model"),
            "channel": next((p.get("channel") for p in profiles if p.get("id") == profile_id), None),
        },
        "runtime": {
            "issue_status": issue_status,
            "run": run,
            "attempts": attempts,
            "wall_seconds": wall_seconds,
            "input_tokens": int(usage[0]),
            "output_tokens": int(usage[1]),
            "cost_cents": int(usage[2]),
            "telemetry_status": "observed" if usage_observed else "unknown",
            "db": str(db),
        },
        "summary": summary,
        "causal_units": causal_units,
    }


def aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    identities = {
        (
            report.get("profile_id"),
            (report.get("execution_config") or {}).get("model"),
            (report.get("execution_config") or {}).get("reasoning_effort_override"),
            report.get("contract_version"),
        )
        for report in reports
    }
    cells = {(report.get("case_id"), report.get("seed")) for report in reports}
    source_receipts = [str(report.get("_source_receipt") or "") for report in reports]
    sources_bound = (
        len(source_receipts) == 6
        and len(set(source_receipts)) == 6
        and all(source_receipts)
    )
    fixture_hashes_complete = (
        len({report.get("source_sha256") for report in reports}) == 2
        and len({report.get("rubric_sha256") for report in reports}) == 2
        and all(report.get("source_sha256") and report.get("rubric_sha256") for report in reports)
    )
    samples_passed = sum(
        report.get("accepted") is True
        and (report.get("runtime") or {}).get("issue_status") == "done"
        and ((report.get("runtime") or {}).get("run") or {}).get("status") == "completed"
        and (report.get("runtime") or {}).get("attempts") == 1
        for report in reports
    )
    matrix_complete = (
        len(reports) == 6
        and len(identities) == 1
        and cells == EXPECTED_CELLS
        and sources_bound
        and fixture_hashes_complete
    )
    seconds = [float((report.get("runtime") or {}).get("wall_seconds") or 0) for report in reports]
    usage_observed = any(
        (report.get("runtime") or {}).get("telemetry_status") == "observed"
        for report in reports
    )
    manifest = sorted(
        [
            {
                "receipt": report.get("_source_receipt"),
                "case_id": report.get("case_id"),
                "seed": report.get("seed"),
                "accepted": report.get("accepted") is True,
                "single_attempt": (report.get("runtime") or {}).get("attempts") == 1,
                "artifact_sha256": hashlib.sha256(
                    json.dumps(
                        {
                            "summary": report.get("summary"),
                            "causal_units": report.get("causal_units"),
                            "criteria": report.get("criteria"),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
                "source_sha256": report.get("source_sha256"),
                "rubric_sha256": report.get("rubric_sha256"),
            }
            for report in reports
        ],
        key=lambda row: (str(row["case_id"]), int(row["seed"] or 0)),
    )
    profile_id, model, reasoning_effort, contract_version = (
        next(iter(identities)) if len(identities) == 1 else (None, None, None, None)
    )
    calibrated = matrix_complete and samples_passed == 6
    return {
        "schema_version": 1,
        "benchmark": "context_curator_role_canary_aggregate",
        "profile_id": profile_id,
        "model": model,
        "role": "context_curator",
        "reasoning_effort": reasoning_effort,
        "contract_version": contract_version,
        "matrix_complete": matrix_complete,
        "samples_passed": samples_passed,
        "source_receipts": source_receipts,
        "sample_manifest": manifest,
        "integrity": {
            "sources_bound": sources_bound,
            "fixtures_hashed": fixture_hashes_complete,
            "artifacts_hashed": True,
        },
        "wall_seconds_median": (
            round(statistics.median(seconds), 3) if seconds else None
        ),
        "wall_seconds_range": (
            [round(min(seconds), 3), round(max(seconds), 3)] if seconds else []
        ),
        "usage": {
            "input_tokens": sum(
                int((report.get("runtime") or {}).get("input_tokens") or 0)
                for report in reports
            ),
            "output_tokens": sum(
                int((report.get("runtime") or {}).get("output_tokens") or 0)
                for report in reports
            ),
            "marginal_cost_cents": 0,
            "telemetry_status": "observed" if usage_observed else "unknown",
            "note": (
                "presión de cuota de suscripción; no coste API"
                if usage_observed
                else "el CLI no expone usage headless comparable"
            ),
        },
        "conclusion": {
            "exact_pair_calibrated": calibrated,
            "default_change_allowed": False,
            "decision": (
                "calibrate_exact_pair" if calibrated else "retain_requires_canary"
            ),
            "unmeasured_constructs": [
                "threads mayores de la ventana fixture",
                "ruido multilingüe adversarial",
                "reanudación tras fallo de persistencia",
            ],
        },
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--rubric", type=Path)
    parser.add_argument("--profile", default="codex_subscription")
    parser.add_argument("--model", default=None, help="override de modelo dentro del perfil")
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
        default=None,
        help="override explícito de razonamiento para Codex",
    )
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--min-chars", type=int, default=8_500)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--aggregate-from", type=Path, nargs="+")
    args = parser.parse_args()
    if args.aggregate_from:
        source_reports = []
        for path in args.aggregate_from:
            source_report = json.loads(path.read_text(encoding="utf-8"))
            try:
                source_report["_source_receipt"] = (
                    path.resolve().relative_to(REPO_ROOT).as_posix()
                )
            except ValueError:
                source_report["_source_receipt"] = path.resolve().as_posix()
            source_reports.append(source_report)
        report = aggregate_reports(source_reports)
        ok = bool(report["conclusion"]["exact_pair_calibrated"])
    else:
        if args.source is None or args.rubric is None or args.workdir is None:
            parser.error(
                "--source, --rubric and --workdir are required unless aggregate mode is used"
            )
        report = run_canary(
            source=args.source.read_text(encoding="utf-8"),
            rubric=json.loads(args.rubric.read_text(encoding="utf-8")),
            profile_id=args.profile,
            workspace=args.workdir.resolve(),
            min_chars=max(8_000, args.min_chars),
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            seed=args.seed,
        )
        ok = bool(report["accepted"] and report["runtime"]["issue_status"] == "done")
    serialized = json.dumps(report, indent=2, ensure_ascii=False)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
