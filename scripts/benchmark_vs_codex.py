"""Benchmark A/B: el equipo AI Teams contra una run única de codex exec.

"Mejor que codex solo" deja de ser narrativa: ambos brazos reciben el MISMO
goal (benchmarks/<caso>/goal.md) y se evalúan con la MISMA suite OCULTA
(benchmarks/<caso>/hidden_tests/) que ninguno vio — más ruff, tokens, coste y
tiempo de pared. El juez es un exit code, no una opinión.

Uso (gasta tokens reales — NUNCA corre en la suite de tests):
    venv/Scripts/python.exe scripts/benchmark_vs_codex.py --case benchmarks/cli_conversor
    venv/Scripts/python.exe scripts/benchmark_vs_codex.py --case ... --arm solo --model gpt-5.4
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VENV_PYTHON = sys.executable


def _portable_path(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(resolved)


# ── Scoring (compartido por ambos brazos) ─────────────────────────────────────

def score_workspace(workspace: Path, hidden_dir: Path, *, python: str = VENV_PYTHON) -> dict[str, Any]:
    """Evalúa un entregable: suite oculta + lint. Determinista, cero LLM."""
    workspace = Path(workspace).resolve()
    result: dict[str, Any] = {}

    # Lint ANTES de copiar la suite oculta (no debe puntuar contra el candidato).
    ruff = _run([python, "-m", "ruff", "check", "--output-format=concise", "."], cwd=workspace)
    result["ruff_issues"] = (
        len([line for line in (ruff.stdout or "").splitlines() if line.strip()])
        if ruff is not None and ruff.returncode in (0, 1) else None
    )

    dest = workspace / ".bench_hidden"
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(hidden_dir, dest)
    # Import pytest under isolated mode *before* exposing the candidate workspace
    # on sys.path.  Otherwise a deliverable named ``pytest.py`` can shadow the
    # real runner, ignore ``dest`` and turn its own tests into a false green.
    pytest_args = [str(dest), "-q", "-p", "no:cacheprovider"]
    isolated_runner = (
        "import sys, pytest; "
        f"sys.path.insert(0, {str(workspace)!r}); "
        f"raise SystemExit(pytest.main({pytest_args!r}))"
    )
    proc = _run(
        [python, "-I", "-c", isolated_runner],
        cwd=workspace,
        timeout=300,
    )
    out = (proc.stdout if proc else "") or ""
    result["hidden_exit"] = proc.returncode if proc else None
    result["hidden_passed"] = _count(out, "passed")
    result["hidden_failed"] = _count(out, "failed")
    result["hidden_errors"] = _count(out, "error")
    result["hidden_total"] = result["hidden_passed"] + result["hidden_failed"] + result["hidden_errors"]
    result["deliverable_files"] = sorted(
        str(p.relative_to(workspace)).replace("\\", "/")
        for p in workspace.rglob("*")
        if p.is_file() and not any(
            part.startswith(".") or part in {"__pycache__", ".bench_hidden"} for part in p.relative_to(workspace).parts
        )
    )[:30]
    return result


def _count(pytest_out: str, word: str) -> int:
    match = re.search(rf"(\d+) {word}", pytest_out)
    return int(match.group(1)) if match else 0


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 120,
         input_text: str | None = None) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, input=input_text,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except Exception:
        return None


# ── Brazo A: el equipo ────────────────────────────────────────────────────────

def run_team_arm(workspace: Path, goal: str, *, profile_ids: list[str],
                 run_profile: str, max_ticks: int, max_minutes: float) -> dict[str, Any]:
    from api.routers.workspace import _initialize_project_runtime
    from aiteam.adapters.registry import build_default_registry
    from aiteam.db.wakeups import enqueue_wakeup
    from aiteam.heartbeat.executor import RunExecutor
    from aiteam.heartbeat.scheduler import HeartbeatScheduler
    from aiteam.project_adapters import write_project_adapter_policy
    from aiteam.workspace_git import init_managed_repo

    workspace.mkdir(parents=True, exist_ok=True)
    runtime_dir = workspace / ".aiteam"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    write_project_adapter_policy(runtime_dir, profile_ids=profile_ids)
    _initialize_project_runtime(
        workspace,
        initial_task=goal,
        run_profile=run_profile,
    )
    init_managed_repo(workspace)
    db = runtime_dir / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        intake = conn.execute("SELECT id FROM issues WHERE id='issue:intake'").fetchone()
    if intake is None:
        raise RuntimeError("bootstrap sin issue:intake — revisa _initialize_project_runtime")
    enqueue_wakeup(
        db, agent_id="role:lead", source="project_bootstrap", reason="new_project",
        payload={
            "issue_id": "issue:intake",
            "wake_reason": "new_project",
            "profile": run_profile,
        },
        idempotency_key="bench:bootstrap",
    )

    executor = RunExecutor(db, build_default_registry())
    scheduler = HeartbeatScheduler(db)
    started = time.time()
    ticks = 0
    status = "in_progress"
    queue_exhausted = False
    while ticks < max_ticks and (time.time() - started) < max_minutes * 60:
        # Despacho acotado: HeartbeatLoop.run_once drena TODA la cola, por lo
        # que max_ticks/max_minutes no podían limitar un benchmark (seed 1 hizo
        # 24 runs en un solo tick). El camino durable scheduler→executor es el
        # mismo, pero aquí una iteración equivale exactamente a una run.
        dispatch = scheduler.dispatch_next()
        if dispatch is None:
            queue_exhausted = True
            break
        ticks += 1
        executor.execute(dispatch)
        with sqlite3.connect(str(db)) as conn:
            status = conn.execute("SELECT status FROM issues WHERE id='issue:intake'").fetchone()[0]
        if status in ("done", "cancelled"):
            break

    with sqlite3.connect(str(db)) as conn:
        tokens = conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            "COALESCE(SUM(cost_cents),0) FROM cost_events"
        ).fetchone()
        runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    return {
        "arm": "team",
        "final_status": status,
        "wall_seconds": round(time.time() - started, 1),
        "ticks": ticks,
        "dispatch_mode": "bounded_scheduler_executor",
        "queue_exhausted": queue_exhausted,
        "limit_reached": ticks >= max_ticks or (time.time() - started) >= max_minutes * 60,
        "runs": runs,
        "tokens_in": int(tokens[0]),
        "tokens_out": int(tokens[1]),
        "cost_cents": int(tokens[2]),
    }


# ── Brazo B: codex exec único ─────────────────────────────────────────────────

def run_solo_arm(workspace: Path, goal: str, *, model: str, max_minutes: float) -> dict[str, Any]:
    from aiteam.adapters.subscription_cli_adapter import _extract_codex_usage, _resolve_cli_cmd

    workspace.mkdir(parents=True, exist_ok=True)
    prompt = (
        f"{goal}\n\n"
        "Trabaja directamente en este directorio (es la raíz del workspace). "
        "Crea todos los entregables como archivos reales y ejecuta la suite pytest "
        "hasta dejarla en verde antes de terminar."
    )
    cmd = [
        _resolve_cli_cmd("codex"), "exec", "--skip-git-repo-check", "--ephemeral",
        "--sandbox", "workspace-write", "-c", "notify=[]", "--json",
        "-c", f'model="{model}"', "--cd", str(workspace), "-",
    ]
    started = time.time()
    proc = _run(cmd, cwd=workspace, timeout=int(max_minutes * 60), input_text=prompt)
    usage = _extract_codex_usage(
        proc.stdout if proc and isinstance(proc.stdout, str) else "",
        proc.stderr if proc and isinstance(proc.stderr, str) else "",
    ) or {}
    return {
        "arm": "solo",
        "final_status": "done" if proc is not None and proc.returncode == 0 else "failed",
        "wall_seconds": round(time.time() - started, 1),
        "runs": 1,
        "tokens_in": int(usage.get("input_tokens") or usage.get("total_tokens") or 0),
        "tokens_out": int(usage.get("output_tokens") or 0),
        "cost_cents": 0,
        "model": model,
    }


# ── Orquestación del benchmark ────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=REPO_ROOT / "benchmarks" / "cli_conversor")
    parser.add_argument("--arm", choices=["both", "team", "solo"], default="both")
    parser.add_argument("--workdir", type=Path, default=None)
    parser.add_argument("--model", default="gpt-5.4", help="modelo del brazo solo")
    parser.add_argument("--profiles", default="codex_subscription,openai_api")
    parser.add_argument(
        "--run-profile",
        choices=("solo_lead", "lead_quorum", "full_team"),
        default="full_team",
        help="perfil canónico de orquestación del brazo AI Teams",
    )
    parser.add_argument("--max-ticks", type=int, default=30)
    parser.add_argument("--max-minutes", type=float, default=25.0)
    parser.add_argument("--seed", type=int, default=None, help="etiqueta de repetición (no controla el muestreo del proveedor)")
    parser.add_argument("--output", type=Path, default=None, help="guarda el informe JSON además de imprimirlo")
    args = parser.parse_args()

    case_dir = args.case.resolve()
    goal = (case_dir / "goal.md").read_text(encoding="utf-8")
    hidden = case_dir / "hidden_tests"
    workdir = (args.workdir or (REPO_ROOT / "runtime" / "bench" / time.strftime("%Y%m%d-%H%M%S"))).resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "case": case_dir.name,
        "seed": args.seed,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "workdir": _portable_path(workdir),
        "config": {
            "harness_version": 3,
            "solo_model": args.model,
            "team_profiles": [p.strip() for p in args.profiles.split(",") if p.strip()],
            "team_run_profile": args.run_profile,
            "max_ticks": args.max_ticks,
            "max_minutes": args.max_minutes,
        },
        "arms": {},
    }
    if args.arm in ("both", "team"):
        ws = workdir / "team"
        metrics = run_team_arm(
            ws, goal, profile_ids=[p.strip() for p in args.profiles.split(",") if p.strip()],
            run_profile=args.run_profile,
            max_ticks=args.max_ticks, max_minutes=args.max_minutes,
        )
        metrics["score"] = score_workspace(ws, hidden)
        report["arms"]["team"] = metrics
    if args.arm in ("both", "solo"):
        ws = workdir / "solo"
        metrics = run_solo_arm(ws, goal, model=args.model, max_minutes=args.max_minutes)
        metrics["score"] = score_workspace(ws, hidden)
        report["arms"]["solo"] = metrics

    serialized = json.dumps(report, indent=2, ensure_ascii=False)
    print(serialized)
    if args.output is not None:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized + "\n", encoding="utf-8")
        print(f"\nInforme guardado en {output}")
    if len(report["arms"]) == 2:
        team, solo = report["arms"]["team"]["score"], report["arms"]["solo"]["score"]
        print(
            f"\nVEREDICTO suite oculta — equipo: {team['hidden_passed']}/{team['hidden_total']}"
            f" | codex solo: {solo['hidden_passed']}/{solo['hidden_total']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
