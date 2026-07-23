from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiteam.adapters.registry import AdapterDescriptor, ExecutionResult, StaticAdapterRuntime
from aiteam.platform_runtime import run_command


@dataclass
class SubprocessAdapterRuntime:
    """Executes an agent run by spawning a subprocess with AITEAM_* env vars."""

    descriptor: AdapterDescriptor
    command: list[str]
    timeout_sec: int = 300
    max_output_chars: int = 65536
    cwd: Path | None = None

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        base = StaticAdapterRuntime(self.descriptor).build_env(
            run_id=run_id, wake_context=wake_context
        )
        api_url = os.environ.get("AITEAM_API_URL", "http://localhost:8000")
        return {**base, "AITEAM_API_URL": api_url}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        merged_env = {**os.environ, **env}
        cwd = str(self.cwd) if self.cwd else None
        try:
            proc = run_command(
                self.command,
                env=merged_env,
                cwd=cwd,
                timeout=self.timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return ExecutionResult(
                status="failed",
                output=stdout[: self.max_output_chars] or None,
                exit_code=None,
                error=f"timeout after {self.timeout_sec}s",
            )
        except FileNotFoundError as exc:
            return ExecutionResult(
                status="failed",
                error=f"command not found: {self.command[0]!r} — {exc}",
                exit_code=127,
            )
        except Exception as exc:
            return ExecutionResult(
                status="failed",
                error=f"subprocess error: {exc}",
                exit_code=1,
            )

        output = (proc.stdout + proc.stderr)[: self.max_output_chars] or None
        if proc.returncode == 0:
            return ExecutionResult(
                status="completed",
                output=output,
                exit_code=proc.returncode,
            )
        return ExecutionResult(
            status="failed",
            output=output,
            exit_code=proc.returncode,
            error=f"exit code {proc.returncode}",
        )
