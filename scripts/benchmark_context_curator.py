"""Canario capa-2 de síntesis causal mediante un ``context_curator`` real.

Ejecuta exactamente una run durable scheduler→executor, persiste el bloque en
SQLite y lo evalúa después con una rúbrica oculta para el modelo.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
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


def _expanded_thread(source: str, *, min_chars: int) -> str:
    source = source.strip()
    noise = (
        "\nRegistro repetido sin decisión nueva: saludos, nombres temporales, "
        "estimaciones descartadas y fragmentos de log no accionables."
    )
    return source + noise * max(0, (min_chars - len(source) + len(noise) - 1) // len(noise))


def run_canary(
    *, source: str, rubric: dict[str, Any], profile_id: str, workspace: Path, min_chars: int,
    model: str | None = None,
) -> dict[str, Any]:
    runtime = workspace / ".aiteam"
    runtime.mkdir(parents=True, exist_ok=True)
    write_project_adapter_policy(runtime, profile_ids=[profile_id])
    # solo_lead evita que el bootstrap cree el Tier 3 que este harness configura
    # explícitamente con el perfil bajo prueba.
    _initialize_project_runtime(workspace, run_profile="solo_lead")
    db = runtime / "aiteam.db"
    profiles = project_profiles(runtime)
    selection = choose_adapter_for_role("context_curator", "cheap", profiles)
    if not selection:
        raise RuntimeError(f"profile not available: {profile_id}")
    adapter_config = dict(selection.get("adapter_config") or {})
    if model:
        adapter_config["model"] = model
        selection["model"] = model
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
        metadata={"adapter_profile_id": selection.get("adapter_profile_id"), "source": "context_canary"},
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
        idempotency_key="context-canary:curator",
    )

    scheduler = HeartbeatScheduler(db)
    dispatch = scheduler.dispatch_next(agent_id="role:context_curator")
    if dispatch is None:
        raise RuntimeError("curator wakeup was not dispatchable")
    started = time.time()
    RunExecutor(db, build_default_registry()).execute(dispatch)
    wall_seconds = round(time.time() - started, 1)

    summary_doc = get_context_summary(db, issue_id="issue:intake")
    summary = ""
    if summary_doc and summary_doc.get("blocks"):
        summary = str(summary_doc["blocks"][-1].get("summary_markdown") or "")
    evaluation = evaluate_summary(durable_source, summary, rubric)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        issue_status = conn.execute(
            "SELECT status FROM issues WHERE id='issue:curator'"
        ).fetchone()[0]
        run = dict(conn.execute(
            "SELECT id,status,error_code,error,agent_id FROM runs WHERE issue_id='issue:curator' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone())
        usage = conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),"
            "COALESCE(SUM(cost_cents),0) FROM cost_events WHERE run_id=?",
            (run["id"],),
        ).fetchone()
    return {
        **evaluation,
        "profile_id": profile_id,
        "adapter": {
            "type": selection.get("adapter_type"),
            "model": selection.get("model"),
            "channel": next((p.get("channel") for p in profiles if p.get("id") == profile_id), None),
        },
        "runtime": {
            "issue_status": issue_status,
            "run": run,
            "wall_seconds": wall_seconds,
            "input_tokens": int(usage[0]),
            "output_tokens": int(usage[1]),
            "cost_cents": int(usage[2]),
            "db": str(db),
        },
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, required=True)
    parser.add_argument("--profile", default="codex_subscription")
    parser.add_argument("--model", default=None, help="override de modelo dentro del perfil")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--min-chars", type=int, default=8_500)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_canary(
        source=args.source.read_text(encoding="utf-8"),
        rubric=json.loads(args.rubric.read_text(encoding="utf-8")),
        profile_id=args.profile,
        workspace=args.workdir.resolve(),
        min_chars=max(8_000, args.min_chars),
        model=args.model,
    )
    serialized = json.dumps(report, indent=2, ensure_ascii=False)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if report["accepted"] and report["runtime"]["issue_status"] == "done" else 2


if __name__ == "__main__":
    raise SystemExit(main())
