from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.adapters.subscription_cli_adapter import _extract_codex_usage  # noqa: E402
from aiteam.session_continuity import (  # noqa: E402
    SessionScope,
    audit_session_experiment,
    extract_codex_session_id,
    validate_resume_scope,
)


SEED_CONTRACTS = {
    1: {
        "fact": "ORBIT-DELTA-731",
        "old_color": "amber",
        "new_color": "cobalt",
    },
    2: {
        "fact": "HARBOR-SIGMA-284",
        "old_color": "violet",
        "new_color": "jade",
    },
}


def run_codex_canary(
    *,
    seeds: list[int],
    model: str,
    output_path: Path,
    timeout_sec: int,
) -> dict[str, Any]:
    executable = shutil.which("codex.cmd") or shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex CLI no está instalado")

    reports: list[dict[str, Any]] = []
    setups: list[dict[str, Any]] = []
    scope = SessionScope(
        agent_id="benchmark:session-continuity",
        issue_id="benchmark:session-continuity",
        adapter_type="subscription_cli",
        profile_id="codex_subscription",
        provider="openai-codex",
        model=model,
        channel="subscription",
        workspace_id=str(REPO_ROOT.resolve()),
    )
    for seed in seeds:
        contract = SEED_CONTRACTS[seed]
        context = _context_payload(seed=seed, fact=contract["fact"], old_color=contract["old_color"])
        update = _update_prompt(
            fact=contract["fact"],
            old_color=contract["old_color"],
            new_color=contract["new_color"],
        )

        setup = _run_codex(
            executable=executable,
            model=model,
            prompt=context,
            timeout_sec=timeout_sec,
            persist=True,
        )
        session_id = extract_codex_session_id(setup["stdout"])
        setups.append({
            "seed": seed,
            "status": setup["status"],
            "session_id": session_id,
            "duration_seconds": setup["duration_seconds"],
            "usage": setup["usage"],
            "output": setup["output"],
            "error": setup.get("error"),
        })
        decision = validate_resume_scope(
            previous=scope,
            current=scope,
            session_id=session_id or "",
            previous_status=setup["status"],
            explicit_opt_in=True,
        )

        stateless = _run_codex(
            executable=executable,
            model=model,
            prompt=context + "\n\n" + update,
            timeout_sec=timeout_sec,
            persist=False,
        )
        reports.append(_arm_report(
            seed=seed,
            arm="stateless",
            run=stateless,
            contract=contract,
            explicit_session_id=False,
            scope_match=False,
            prompt_chars=len(context + "\n\n" + update),
        ))

        if decision["allowed"]:
            resumed = _run_codex(
                executable=executable,
                model=model,
                prompt=update,
                timeout_sec=timeout_sec,
                persist=False,
                resume_session_id=str(decision["session_id"]),
            )
            reports.append(_arm_report(
                seed=seed,
                arm="resumed",
                run=resumed,
                contract=contract,
                explicit_session_id=True,
                scope_match=True,
                prompt_chars=len(update),
                session_id=str(decision["session_id"]),
            ))
        else:
            reports.append({
                "seed": seed,
                "arm": "resumed",
                "provider": "openai-codex",
                "model": model,
                "status": "skipped",
                "explicit_session_id": bool(session_id),
                "scope_match": False,
                "gates": {},
                "resume_decision": decision,
            })

        _write_checkpoint(output_path, model=model, reports=reports, setups=setups)

    return _write_checkpoint(output_path, model=model, reports=reports, setups=setups)


def _run_codex(
    *,
    executable: str,
    model: str,
    prompt: str,
    timeout_sec: int,
    persist: bool,
    resume_session_id: str | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="aiteam-session-canary-") as tmp:
        output_file = Path(tmp) / "last-message.txt"
        if resume_session_id:
            command = [executable, "exec", "resume"]
        else:
            command = [executable, "exec"]
        command.append("--skip-git-repo-check")
        if resume_session_id:
            # Codex 0.128 resume no expone --sandbox aunque exec sí. El override
            # mantiene el mismo límite sin introducir un argumento rechazado.
            command.extend(["-c", 'sandbox_mode="read-only"'])
        else:
            command.extend(["--sandbox", "read-only"])
        if not persist:
            command.append("--ephemeral")
        command.extend([
            "-c", "notify=[]",
            "-c", f'model="{model}"',
            "--json",
            "--output-last-message", str(output_file),
        ])
        if resume_session_id:
            command.append(resume_session_id)
        command.append("-")
        started = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                cwd=REPO_ROOT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "status": "failed",
                "duration_seconds": round(time.monotonic() - started, 3),
                "output": "",
                "stdout": str(exc.stdout or ""),
                "stderr": str(exc.stderr or ""),
                "usage": {},
                "error": f"timeout_after_{timeout_sec}s",
            }
        duration = round(time.monotonic() - started, 3)
        output = output_file.read_text(encoding="utf-8") if output_file.exists() else ""
        usage = _extract_codex_usage(proc.stdout or "", proc.stderr or "") or {}
        return {
            "status": "completed" if proc.returncode == 0 else "failed",
            "duration_seconds": duration,
            "output": output.strip(),
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "usage": usage,
            "exit_code": proc.returncode,
            "error": None if proc.returncode == 0 else _failure_excerpt(proc.stdout, proc.stderr),
        }


def _context_payload(*, seed: int, fact: str, old_color: str) -> str:
    filler = "\n".join(
        f"reference_{index:03d}=stable-value-{seed}-{index:03d}"
        for index in range(180)
    )
    return f"""Session-continuity canary. Do not use tools and do not read or edit files.
Remember the following context for a later turn.
CANARY_FACT={fact}
ACTIVE_COLOR={old_color}
The color instruction may be replaced in a later turn; the latest instruction always wins.

Reference context:
{filler}

Reply with exactly: ACK {fact}
"""


def _update_prompt(*, fact: str, old_color: str, new_color: str) -> str:
    return f"""Do not use tools and do not read or edit files.
The latest instruction replaces ACTIVE_COLOR={old_color} with ACTIVE_COLOR={new_color}.
The previous color is revoked and must not be used as the active color.
Return ONLY this JSON object, with no Markdown:
{{"initial_fact":"{fact}","active_color":"{new_color}","revoked_color_used":false}}
"""


def _arm_report(
    *,
    seed: int,
    arm: str,
    run: dict[str, Any],
    contract: dict[str, str],
    explicit_session_id: bool,
    scope_match: bool,
    prompt_chars: int,
    session_id: str | None = None,
    model: str = "gpt-5.6-sol",
) -> dict[str, Any]:
    parsed = _parse_json_object(str(run.get("output") or ""))
    usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
    input_tokens = int(usage.get("input_tokens") or 0)
    cached_tokens = int(usage.get("cached_input_tokens") or 0)
    return {
        "seed": seed,
        "arm": arm,
        "provider": "openai-codex",
        "model": model,
        "status": run.get("status"),
        "session_id": session_id,
        "explicit_session_id": explicit_session_id,
        "scope_match": scope_match,
        "prompt_chars": prompt_chars,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "uncached_input_tokens": max(0, input_tokens - cached_tokens),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "duration_seconds": run.get("duration_seconds"),
        "output": run.get("output"),
        "error": run.get("error"),
        "gates": {
            "retains_initial_fact": parsed.get("initial_fact") == contract["fact"],
            "applies_new_instruction": parsed.get("active_color") == contract["new_color"],
            "revoked_instruction_absent": (
                parsed.get("revoked_color_used") is False
                and parsed.get("active_color") != contract["old_color"]
            ),
        },
    }


def _parse_json_object(text: str) -> dict[str, Any]:
    candidate = str(text or "").strip()
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(candidate[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except ValueError:
                pass
    return {}


def _failure_excerpt(stdout: str | None, stderr: str | None) -> str:
    combined = ((stdout or "") + "\n" + (stderr or "")).strip()
    return combined[-1000:] if combined else "codex_nonzero_exit"


def _write_checkpoint(
    output_path: Path,
    *,
    model: str,
    reports: list[dict[str, Any]],
    setups: list[dict[str, Any]],
) -> dict[str, Any]:
    audit = audit_session_experiment(reports, min_seeds=2)
    result = {
        "schema_version": 1,
        "provider": "openai-codex",
        "model": model,
        "production_changed": False,
        "setup_runs": setups,
        "reports": reports,
        "audit": audit,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def resume_existing_codex_arms(
    *,
    checkpoint: Path,
    timeout_sec: int,
) -> dict[str, Any]:
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    model = str(payload.get("model") or "gpt-5.6-sol")
    executable = shutil.which("codex.cmd") or shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex CLI no está instalado")
    reports = [
        dict(item)
        for item in payload.get("reports") or []
        if isinstance(item, dict) and str(item.get("arm") or "") != "resumed"
    ]
    setups = [dict(item) for item in payload.get("setup_runs") or [] if isinstance(item, dict)]
    for setup in setups:
        seed = int(setup["seed"])
        contract = SEED_CONTRACTS[seed]
        session_id = str(setup.get("session_id") or "")
        update = _update_prompt(
            fact=contract["fact"],
            old_color=contract["old_color"],
            new_color=contract["new_color"],
        )
        resumed = _run_codex(
            executable=executable,
            model=model,
            prompt=update,
            timeout_sec=timeout_sec,
            persist=False,
            resume_session_id=session_id,
        )
        reports.append(_arm_report(
            seed=seed,
            arm="resumed",
            run=resumed,
            contract=contract,
            explicit_session_id=True,
            scope_match=True,
            prompt_chars=len(update),
            session_id=session_id,
            model=model,
        ))
        _write_checkpoint(checkpoint, model=model, reports=reports, setups=setups)
    return _write_checkpoint(checkpoint, model=model, reports=reports, setups=setups)


def main() -> int:
    parser = argparse.ArgumentParser(description="Canario real y acotado stateless vs Codex resume")
    parser.add_argument("--seeds", default="1,2")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--timeout-sec", type=int, default=240)
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help="Repite solo los brazos resumed del checkpoint, sin nuevos setup/baseline",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "cli_sessions" / "codex-gpt-5.6-sol-seeds-1-2.json",
    )
    args = parser.parse_args()
    seeds = [int(value) for value in str(args.seeds).split(",") if value.strip()]
    unknown = [seed for seed in seeds if seed not in SEED_CONTRACTS]
    if unknown:
        parser.error(f"semillas no definidas: {unknown}")
    if args.resume_existing:
        result = resume_existing_codex_arms(
            checkpoint=args.output,
            timeout_sec=max(30, args.timeout_sec),
        )
    else:
        result = run_codex_canary(
            seeds=seeds,
            model=args.model,
            output_path=args.output,
            timeout_sec=max(30, args.timeout_sec),
        )
    print(json.dumps(result["audit"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["audit"]["production_activation_allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
