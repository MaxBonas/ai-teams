"""Canario A/B OpenCode CLI efímero frente a ``serve`` + ``run --attach``.

No activa producción ni reanudación. Cada llamada crea una sesión nueva, usa
datos sintéticos públicos y exige el mismo contrato JSON sin tools.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.adapters.subscription_cli_adapter import (  # noqa: E402
    _extract_opencode_usage,
    _parse_opencode_output,
)

DEFAULT_MODEL = "opencode/deepseek-v4-flash-free"
POLICY = {"share": "disabled", "permission": {"*": "deny"}}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for_port(port: int, *, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _wait_for_port_closed(port: int, *, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                time.sleep(0.1)
        except OSError:
            return True
    return False


def _server_executable(cli_executable: str) -> str:
    """Resolve the native binary so teardown owns the actual server process."""
    cli_path = Path(cli_executable)
    if os.name == "nt" and cli_path.suffix.lower() == ".cmd":
        native = cli_path.parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
        if native.is_file():
            return str(native)
    return cli_executable


def _prompt(marker: str) -> str:
    return (
        "This is a synthetic public transport canary. Return exactly one JSON "
        "object with top-level keys status, summary, ops. Set status to completed, "
        f"summary to {json.dumps(marker)}, and ops to []. Return no other text."
    )


def evaluate_stream(*, arm: str, seed: int, marker: str, raw: str, seconds: float) -> dict[str, Any]:
    try:
        work = _parse_opencode_output(raw)
        parse_error = None
    except ValueError as exc:
        work = {}
        parse_error = str(exc)
    usage = _extract_opencode_usage(raw)
    session_id = str((usage or {}).get("provider_session_id") or "")
    gates = {
        "valid_submit_work": parse_error is None,
        "completed": work.get("status") == "completed",
        "marker_exact": work.get("summary") == marker,
        "no_ops": work.get("ops") == [],
        "explicit_session_id": bool(session_id),
    }
    return {
        "arm": arm,
        "seed": seed,
        "marker": marker,
        "seconds": round(seconds, 3),
        "status": work.get("status"),
        "summary": work.get("summary"),
        "ops_count": len(work.get("ops") or []),
        "session_id": session_id or None,
        "usage": usage,
        "parse_error": parse_error,
        "gates": gates,
        "ok": all(gates.values()),
    }


def _run_call(
    executable: str,
    *,
    model: str,
    seed: int,
    arm: str,
    env: dict[str, str],
    attach_url: str | None = None,
    timeout_sec: int = 180,
) -> dict[str, Any]:
    marker = f"OPENCODE_TRANSPORT_{seed}_{arm.upper()}"
    command = [executable, "run"]
    if attach_url:
        command.extend(["--attach", attach_url])
    command.extend(["--format", "json", "--model", model, _prompt(marker)])
    started = time.monotonic()
    proc = subprocess.run(
        command,
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        check=False,
    )
    raw = (proc.stdout or "") + (proc.stderr or "")
    report = evaluate_stream(
        arm=arm, seed=seed, marker=marker, raw=raw,
        seconds=time.monotonic() - started,
    )
    report.update({"exit_code": proc.returncode, "ok": report["ok"] and proc.returncode == 0})
    return report


def summarize(
    samples: list[dict[str, Any]], *, model: str, cli_version: str,
    server_teardown_ok: bool = True,
) -> dict[str, Any]:
    arms: list[dict[str, Any]] = []
    for arm in ("direct", "attached"):
        rows = [row for row in samples if row.get("arm") == arm]
        seconds = [float(row["seconds"]) for row in rows]
        tokens = [int((row.get("usage") or {}).get("total_tokens") or 0) for row in rows]
        arms.append({
            "arm": arm,
            "samples": len(rows),
            "passed": sum(bool(row.get("ok")) for row in rows),
            "seconds_median": round(statistics.median(seconds), 3) if seconds else None,
            "total_tokens_median": round(statistics.median(tokens), 1) if tokens else None,
        })
    sessions = [str(row.get("session_id") or "") for row in samples]
    matrix_complete = all(arm["samples"] == 3 for arm in arms)
    all_passed = all(arm["passed"] == arm["samples"] for arm in arms)
    isolated = bool(sessions) and all(sessions) and len(sessions) == len(set(sessions))
    return {
        "schema_version": 1,
        "benchmark": "opencode_transport_ab",
        "model": model,
        "cli_version": cli_version,
        "contract": "fresh_session_exact_marker_no_tools_v1",
        "samples": samples,
        "arms": arms,
        "gates": {
            "matrix_3x2": matrix_complete,
            "all_contracts_passed": all_passed,
            "fresh_sessions_isolated": isolated,
            "loopback_authenticated": True,
            "server_teardown_guaranteed": server_teardown_ok,
            "cancellation_tested": False,
            "hang_recovery_tested": False,
        },
        "production_activation_allowed": False,
        "decision": "retain_ephemeral_cli",
        "reason": (
            "El A/B solo compara transporte y aislamiento; cancelación, hangs y recovery "
            "HTTP/SDK siguen sin evidencia suficiente."
        ),
    }


def run_experiment(*, model: str = DEFAULT_MODEL) -> dict[str, Any]:
    executable = shutil.which("opencode.cmd") or shutil.which("opencode")
    if not executable:
        raise RuntimeError("OpenCode CLI is not installed")
    version_proc = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, check=False, timeout=20
    )
    cli_version = (version_proc.stdout or version_proc.stderr or "unknown").strip().splitlines()[0]
    port = _free_port()
    password = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "OPENCODE_SERVER_PASSWORD": password,
        "OPENCODE_CONFIG_CONTENT": json.dumps(POLICY),
    }
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    server_executable = _server_executable(executable)
    server = subprocess.Popen(
        [server_executable, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
        env=env,
        cwd=str(REPO_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    samples: list[dict[str, Any]] = []
    try:
        if not _wait_for_port(port):
            raise RuntimeError("OpenCode server readiness timeout")
        url = f"http://127.0.0.1:{port}"
        for seed in (1, 2, 3):
            samples.append(_run_call(executable, model=model, seed=seed, arm="direct", env=env))
            samples.append(
                _run_call(
                    executable, model=model, seed=seed, arm="attached", env=env, attach_url=url
                )
            )
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
    teardown_ok = _wait_for_port_closed(port)
    return summarize(
        samples, model=model, cli_version=cli_version,
        server_teardown_ok=teardown_ok,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run_experiment(model=args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"gates": report["gates"], "decision": report["decision"]}))
    return 0 if all(
        report["gates"][key]
        for key in (
            "matrix_3x2", "all_contracts_passed", "fresh_sessions_isolated",
            "server_teardown_guaranteed",
        )
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
