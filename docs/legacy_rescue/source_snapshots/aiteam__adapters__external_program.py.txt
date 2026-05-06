from __future__ import annotations

import shutil
import subprocess
import time
import os

from aiteam.adapters.base import ModelAdapter, messages_to_prompt
from aiteam.types import AdapterResponse, ChannelType


class ExternalProgramAdapter(ModelAdapter):
    """Adapter para reutilizar programas agenticos existentes via CLI."""

    def __init__(
        self,
        name: str,
        provider: str,
        model: str,
        command: list[str],
        capabilities: set[str] | None = None,
        channel: ChannelType = ChannelType.SUBSCRIPTION,
        timeout_seconds: int = 120,
        cost_tier: int = 1,
        role_targets: set[str] | None = None,
        routing_priority: int = 200,
        requires_approval: bool = False,
    ) -> None:
        super().__init__(
            name=name,
            provider=provider,
            model=model,
            channel=channel,
            capabilities=capabilities,
            cost_tier=cost_tier,
            role_targets=role_targets,
            routing_priority=routing_priority,
            requires_approval=requires_approval,
        )
        self.command = command
        self.timeout_seconds = timeout_seconds

    def available(self) -> bool:
        if not self.command:
            return False
        return self._resolve_executable(self.command[0]) is not None

    def invoke(
        self, prompt: str, messages: list[dict[str, str]] | None = None
    ) -> AdapterResponse:
        start = time.time()
        prompt_text = messages_to_prompt(messages, prompt)
        input_tokens = max(1, len(prompt_text) // 4)
        cmd = [part.replace("{prompt}", prompt_text) for part in self.command]
        if cmd:
            resolved = self._resolve_executable(cmd[0])
            if resolved is not None:
                cmd[0] = resolved

        try:
            completed = subprocess.run(
                cmd,
                input=prompt_text,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except OSError as exc:
            return AdapterResponse(
                success=False,
                content="",
                latency_ms=int((time.time() - start) * 1000),
                input_tokens=input_tokens,
                output_tokens=0,
                error=f"external_exec_error:{exc}",
            )
        except subprocess.TimeoutExpired:
            return AdapterResponse(
                success=False,
                content="",
                latency_ms=int((time.time() - start) * 1000),
                input_tokens=input_tokens,
                output_tokens=0,
                error="external_timeout",
            )

        output = (completed.stdout or "").strip()
        if completed.returncode != 0:
            error_text = (completed.stderr or "").strip() or "external_program_failed"
            return AdapterResponse(
                success=False,
                content=output,
                latency_ms=int((time.time() - start) * 1000),
                input_tokens=input_tokens,
                output_tokens=max(1, len(output) // 4) if output else 0,
                error=error_text,
            )

        return AdapterResponse(
            success=True,
            content=output or "ok",
            latency_ms=int((time.time() - start) * 1000),
            input_tokens=input_tokens,
            output_tokens=max(1, len(output) // 4) if output else 1,
            error=None,
        )

    @staticmethod
    def _resolve_executable(command: str) -> str | None:
        binary = str(command).strip()
        if not binary:
            return None
        candidates = [binary]
        if os.name == "nt" and not binary.lower().endswith((".cmd", ".exe", ".bat")):
            candidates.extend([f"{binary}.cmd", f"{binary}.exe", f"{binary}.bat"])
        for candidate in candidates:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        return None
