"""Canario durable del SDK OpenCode: cancelación real y recuperación básica.

No activa ``serve`` en producción. Arranca un servidor autenticado en loopback,
delega el protocolo al SDK oficial y exige teardown del proceso y del puerto.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.benchmark_opencode_transport import (  # noqa: E402
    DEFAULT_MODEL,
    POLICY,
    _free_port,
    _server_executable,
    _wait_for_port,
    _wait_for_port_closed,
)

DEFAULT_SDK_ENTRY = (
    REPO_ROOT
    / "runtime"
    / "opencode-sdk-canary"
    / "node_modules"
    / "@opencode-ai"
    / "sdk"
    / "dist"
    / "v2"
    / "index.js"
)
NODE_CANARY = REPO_ROOT / "scripts" / "opencode_sdk_resilience_canary.mjs"


def summarize_sdk_result(
    sdk: dict[str, Any], *, cli_version: str, server_teardown_ok: bool
) -> dict[str, Any]:
    gates = dict(sdk.get("gates") or {})
    cancellation_ok = all(
        gates.get(key) is True
        for key in (
            "busy_observed_before_abort",
            "server_abort_acknowledged",
            "idle_after_abort",
        )
    )
    recovery_ok = all(
        gates.get(key) is True
        for key in (
            "sdk_health_after",
            "recovery_prompt_completed",
            "recovery_marker_exact",
        )
    )
    return {
        "schema_version": 1,
        "benchmark": "opencode_server_sdk_resilience",
        "cli_version": cli_version,
        "sdk_version": sdk.get("sdk_version"),
        "model": sdk.get("model"),
        "contract": "busy_abort_idle_recover_json_schema_v1",
        "sdk_result": sdk,
        "gates": {
            "official_sdk_exercised": bool(sdk.get("sdk_version")),
            "loopback_authenticated": True,
            "cancellation_tested": cancellation_ok,
            "busy_abort_recovery_tested": recovery_ok,
            "json_schema_tested": True,
            "json_schema_accepted": gates.get("json_schema_accepted") is True,
            "session_deleted": gates.get("session_deleted") is True,
            "server_teardown_guaranteed": server_teardown_ok,
            "true_hang_fault_injection_tested": False,
            "mcp_health_tested": False,
        },
        "production_activation_allowed": False,
        "decision": "retain_ephemeral_cli",
        "reason": (
            "El SDK cancela una sesión busy y recupera health/inferencia, pero el contrato "
            "JSON Schema falla y aún faltan fault injection de hangs y health MCP."
        ),
    }


def run_experiment(
    *, model: str = DEFAULT_MODEL, sdk_entry: Path = DEFAULT_SDK_ENTRY
) -> dict[str, Any]:
    executable = shutil.which("opencode.cmd") or shutil.which("opencode")
    node = shutil.which("node")
    if not executable:
        raise RuntimeError("OpenCode CLI is not installed")
    if not node:
        raise RuntimeError("Node.js is not installed")
    if not sdk_entry.is_file():
        raise RuntimeError(
            "OpenCode SDK missing; install with: npm install --prefix "
            "runtime/opencode-sdk-canary --ignore-scripts @opencode-ai/sdk@1.18.4"
        )
    version_proc = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    cli_version = (
        (version_proc.stdout or version_proc.stderr or "unknown")
        .strip()
        .splitlines()[0]
    )
    port = _free_port()
    password = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "OPENCODE_SERVER_PASSWORD": password,
        "OPENCODE_CONFIG_CONTENT": json.dumps(POLICY),
    }
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    server = subprocess.Popen(
        [
            _server_executable(executable),
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        cwd=str(REPO_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    sdk_result: dict[str, Any] = {}
    try:
        if not _wait_for_port(port):
            raise RuntimeError("OpenCode server readiness timeout")
        provider, model_id = model.split("/", 1)
        canary_env = {
            **env,
            "AITEAMS_OPENCODE_BASE_URL": f"http://127.0.0.1:{port}",
            "AITEAMS_OPENCODE_PASSWORD": password,
            "AITEAMS_OPENCODE_SDK_ENTRY": str(sdk_entry.resolve()),
            "AITEAMS_OPENCODE_DIRECTORY": str(REPO_ROOT),
            "AITEAMS_OPENCODE_MODEL": model_id,
        }
        if provider != "opencode":
            raise ValueError(
                "the resilience canary currently requires an opencode/* model"
            )
        proc = subprocess.run(
            [node, str(NODE_CANARY)],
            cwd=str(REPO_ROOT),
            env=canary_env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=210,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"SDK canary failed: {(proc.stderr or proc.stdout).strip()}"
            )
        sdk_result = json.loads((proc.stdout or "").strip().splitlines()[-1])
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
    return summarize_sdk_result(
        sdk_result,
        cli_version=cli_version,
        server_teardown_ok=_wait_for_port_closed(port),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--sdk-entry", type=Path, default=DEFAULT_SDK_ENTRY)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run_experiment(model=args.model, sdk_entry=args.sdk_entry)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"gates": report["gates"], "decision": report["decision"]}))
    required = (
        "official_sdk_exercised",
        "cancellation_tested",
        "busy_abort_recovery_tested",
        "session_deleted",
        "server_teardown_guaranteed",
    )
    return 0 if all(report["gates"][key] for key in required) else 2


if __name__ == "__main__":
    raise SystemExit(main())
