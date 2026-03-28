from __future__ import annotations

import os
import re
import subprocess
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiteam.observability import EventLogger


@dataclass
class CommandResult:
    success: bool
    step_type: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    reason: str | None = None


class CommandPolicy:
    """Guardrails para ejecucion local de comandos."""

    MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10MB

    def __init__(
        self,
        allowed_prefixes: list[str] | None = None,
        blocked_patterns: list[str] | None = None,
        allow_unrestricted: bool = False,
        max_output_bytes: int | None = None,
    ) -> None:
        self.allowed_prefixes = allowed_prefixes or [
            "python",
            "pytest",
            "pip",
            "npm",
            "node",
            "git",
            "dir",
            "ls",
            "type",
            "echo",
            "Get-ChildItem",
            "Get-Content",
            "Write-Output",
            "pwsh",
            "powershell",
            "start",
        ]
        self.blocked_patterns = blocked_patterns or [
            r"rm\s+-rf",
            r"del\s+/f\s+/q",
            r"format\s+",
            r"shutdown\s+",
            r"reboot\s+",
            r"mkfs\.",
            r"reg\s+delete",
            r"cipher\s+/w",
            r"\|\s*iex",
        ]
        self.allow_unrestricted = allow_unrestricted
        self.max_output_bytes = max_output_bytes or self.MAX_OUTPUT_BYTES

    def allows(self, command: str) -> tuple[bool, str]:
        cmd = command.strip()
        if not cmd:
            return False, "empty_command"

        lower = cmd.lower()
        for pattern in self.blocked_patterns:
            if re.search(pattern, lower):
                return False, f"blocked_pattern:{pattern}"

        if self.allow_unrestricted:
            return True, "ok"

        first = cmd.split()[0]
        if first in self.allowed_prefixes:
            return True, "ok"
        return False, f"prefix_not_allowed:{first}"


class LocalCommandExecutor:
    def __init__(
        self,
        workspace_root: Path,
        policy: CommandPolicy,
        additional_roots: list[Path] | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.policy = policy
        self.allowed_roots = [self.workspace_root]
        for root in additional_roots or []:
            try:
                resolved = root.resolve()
            except OSError:
                continue
            if resolved not in self.allowed_roots:
                self.allowed_roots.append(resolved)

    def run_cmd(self, command: str, workdir: Path | None = None, timeout: int = 120) -> CommandResult:
        allowed, reason = self.policy.allows(command)
        if not allowed:
            return CommandResult(
                success=False,
                step_type="cmd",
                command=command,
                exit_code=1,
                stdout="",
                stderr="",
                reason=reason,
            )

        cwd = self._safe_workdir(workdir)
        env = self._build_sandbox_env()
        proc = subprocess.run(
            ["cmd", "/c", command],
            capture_output=True,
            text=True,
            cwd=str(cwd),
            env=env,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            success=proc.returncode == 0,
            step_type="cmd",
            command=command,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def run_powershell(
        self,
        command: str,
        workdir: Path | None = None,
        timeout: int = 120,
    ) -> CommandResult:
        allowed, reason = self.policy.allows(command)
        if not allowed:
            return CommandResult(
                success=False,
                step_type="powershell",
                command=command,
                exit_code=1,
                stdout="",
                stderr="",
                reason=reason,
            )

        cwd = self._safe_workdir(workdir)
        env = self._build_sandbox_env()
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            cwd=str(cwd),
            env=env,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            success=proc.returncode == 0,
            step_type="powershell",
            command=command,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def _safe_workdir(self, workdir: Path | None) -> Path:
        candidate = (workdir or self.workspace_root).resolve()
        for root in self.allowed_roots:
            if candidate == root or root in candidate.parents:
                return candidate
        return self.workspace_root

    def _build_sandbox_env(self) -> dict[str, str]:
        env = os.environ.copy()
        
        # Filtrado agresivo de secretos y tokens en el entorno de la IA
        blocked_keywords = [
            "API_KEY", "APIKEY", "API-KEY",
            "TOKEN", "_TOKEN", "ACCESS_TOKEN", "REFRESH_TOKEN", "AUTH_TOKEN",
            "SECRET", "_SECRET", "CLIENT_SECRET",
            "PASSWORD", "PASSWD", "_PASS",
            "CREDENTIALS", "CREDENTIAL",
            "AWS_ACCESS", "AWS_SECRET",
            "GITHUB_TOKEN", "GH_TOKEN",
            "ANTHROPIC_", "OPENAI_", "GOOGLE_API", "GROQ_",
            "STRIPE_", "TWILIO_", "SENDGRID_",
        ]
        
        keys_to_remove = []
        for key in env.keys():
            if key == "AITEAM_SANDBOX_STRICT":
                continue
            upper_key = key.upper()
            for blocked in blocked_keywords:
                if blocked in upper_key:
                    keys_to_remove.append(key)
                    break
                    
        for key in keys_to_remove:
            env.pop(key, None)
            
        return env


class BrowserController:
    """Control agéntico basico de navegador (fetch/open)."""

    @staticmethod
    def fetch(url: str, timeout: int = 20) -> CommandResult:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            return CommandResult(
                success=False,
                step_type="browser_fetch",
                command=url,
                exit_code=1,
                stdout="",
                stderr=str(exc),
                reason="browser_fetch_failed",
            )

        title_match = re.search(r"<title>(.*?)</title>", body, flags=re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""
        preview = body[:500]
        return CommandResult(
            success=True,
            step_type="browser_fetch",
            command=url,
            exit_code=0,
            stdout=f"title={title}\npreview={preview}",
            stderr="",
        )

    @staticmethod
    def open(url: str) -> CommandResult:
        ok = webbrowser.open(url)
        return CommandResult(
            success=bool(ok),
            step_type="browser_open",
            command=url,
            exit_code=0 if ok else 1,
            stdout="opened" if ok else "",
            stderr="" if ok else "could_not_open_browser",
            reason=None if ok else "browser_open_failed",
        )

    @staticmethod
    def script(url: str, actions: list[dict[str, Any]], timeout: int = 30) -> CommandResult:
        return CommandResult(
            success=False,
            step_type="browser_script",
            command=f"url={url}",
            exit_code=1,
            stdout="",
            stderr="playwright_not_enabled",
            reason="browser_script_unsupported",
        )


class PlaywrightBrowserController(BrowserController):
    """Control de navegador con Playwright (opcional)."""

    @staticmethod
    def script(url: str, actions: list[dict[str, Any]], timeout: int = 30) -> CommandResult:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        except ImportError:
            return CommandResult(
                success=False,
                step_type="browser_script",
                command=f"url={url}",
                exit_code=1,
                stdout="",
                stderr="playwright_not_installed",
                reason="browser_script_unsupported",
            )

        logs: list[str] = []
        evidence_files: list[str] = []
        timeout_ms = max(1000, timeout * 1000)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    if url:
                        page.goto(url, timeout=timeout_ms)
                        logs.append(f"goto:{url}")

                    for step in actions:
                        action_type = str(step.get("type", "")).strip().lower()
                        action_timeout = PlaywrightBrowserController._action_timeout_ms(step, timeout_ms)
                        if action_type == "goto":
                            target = str(step.get("url", "")).strip()
                            page.goto(target, timeout=action_timeout)
                            logs.append(f"goto:{target}")
                        elif action_type == "click":
                            selector = str(step.get("selector", "")).strip()
                            page.click(selector, timeout=action_timeout)
                            logs.append(f"click:{selector}")
                        elif action_type == "type":
                            selector = str(step.get("selector", "")).strip()
                            text = str(step.get("text", ""))
                            page.fill(selector, text, timeout=action_timeout)
                            logs.append(f"type:{selector}:{len(text)}")
                        elif action_type == "wait_for_selector":
                            selector = str(step.get("selector", "")).strip()
                            page.wait_for_selector(selector, timeout=action_timeout)
                            logs.append(f"wait_for_selector:{selector}")
                        elif action_type == "wait_for_url":
                            expected = str(step.get("url", "")).strip()
                            page.wait_for_url(expected, timeout=action_timeout)
                            logs.append(f"wait_for_url:{expected}")
                        elif action_type == "wait_for_timeout":
                            ms = int(step.get("ms", 500))
                            page.wait_for_timeout(ms)
                            logs.append(f"wait_for_timeout:{ms}")
                        elif action_type == "press":
                            selector = str(step.get("selector", "")).strip()
                            key = str(step.get("key", "Enter")).strip()
                            page.press(selector, key, timeout=action_timeout)
                            logs.append(f"press:{selector}:{key}")
                        elif action_type == "hover":
                            selector = str(step.get("selector", "")).strip()
                            page.hover(selector, timeout=action_timeout)
                            logs.append(f"hover:{selector}")
                        elif action_type == "select_option":
                            selector = str(step.get("selector", "")).strip()
                            value = str(step.get("value", "")).strip()
                            page.select_option(selector, value=value, timeout=action_timeout)
                            logs.append(f"select_option:{selector}:{value}")
                        elif action_type == "extract_text":
                            selector = str(step.get("selector", "")).strip()
                            label = str(step.get("label", selector or "text"))
                            value = page.locator(selector).first.inner_text(timeout=action_timeout).strip()
                            logs.append(f"extract_text:{label}:{PlaywrightBrowserController._compact(value, 120)}")
                        elif action_type == "assert_text":
                            selector = str(step.get("selector", "")).strip()
                            expected = str(step.get("text", "")).strip()
                            contains = bool(step.get("contains", True))
                            value = page.locator(selector).first.inner_text(timeout=action_timeout)
                            matched = expected in value if contains else expected == value
                            if not matched:
                                raise RuntimeError(
                                    f"assert_text_failed selector={selector} expected={expected}"
                                )
                            logs.append(f"assert_text:{selector}:ok")
                        elif action_type == "set_viewport":
                            width = int(step.get("width", 1280))
                            height = int(step.get("height", 720))
                            page.set_viewport_size({"width": width, "height": height})
                            logs.append(f"set_viewport:{width}x{height}")
                        elif action_type == "evaluate":
                            script = str(step.get("script", "")).strip()
                            result = page.evaluate(script)
                            logs.append(
                                f"evaluate:{PlaywrightBrowserController._compact(str(result), 80)}"
                            )
                        elif action_type == "screenshot":
                            path = PlaywrightBrowserController._resolve_screenshot_path(step)
                            path.parent.mkdir(parents=True, exist_ok=True)
                            page.screenshot(
                                path=str(path),
                                full_page=bool(step.get("full_page", True)),
                            )
                            evidence_files.append(str(path))
                            logs.append(f"screenshot:{path}")
                        else:
                            logs.append(f"ignored_action:{action_type}")

                    title = page.title()
                    current_url = page.url
                finally:
                    browser.close()
        except Exception as exc:  # noqa: BLE001
            return CommandResult(
                success=False,
                step_type="browser_script",
                command=f"url={url}",
                exit_code=1,
                stdout="\n".join(logs),
                stderr=str(exc),
                reason="browser_script_failed",
            )

        return CommandResult(
            success=True,
            step_type="browser_script",
            command=f"url={url}",
            exit_code=0,
            stdout=(
                f"title={title}\n"
                f"url={current_url}\n"
                f"logs={'|'.join(logs)}\n"
                f"evidence={'|'.join(evidence_files)}"
            ),
            stderr="",
        )

    @staticmethod
    def _resolve_screenshot_path(step: dict[str, Any]) -> Path:
        raw = str(step.get("path", "runtime/evidence/browser_script.png")).strip()
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate
        return Path.cwd() / candidate

    @staticmethod
    def _action_timeout_ms(step: dict[str, Any], default_timeout_ms: int) -> int:
        step_timeout = step.get("timeout")
        if step_timeout is None:
            return default_timeout_ms
        try:
            seconds = float(step_timeout)
        except (TypeError, ValueError):
            return default_timeout_ms
        return max(500, int(seconds * 1000))

    @staticmethod
    def _compact(value: str, max_chars: int) -> str:
        clean = value.replace("\n", " ").strip()
        if len(clean) <= max_chars:
            return clean
        if max_chars <= 3:
            return clean[:max_chars]
        return clean[: max_chars - 3] + "..."


class ExecutionEngine:
    def __init__(
        self,
        executor: LocalCommandExecutor,
        browser: BrowserController,
        event_logger: EventLogger | None = None,
    ) -> None:
        self.executor = executor
        self.browser = browser
        self.event_logger = event_logger

    def execute_plan(
        self,
        task_id: str,
        plan: list[dict[str, Any]],
        workspace: Path | None = None,
    ) -> list[CommandResult]:
        results: list[CommandResult] = []
        total_output_bytes = 0

        for step in plan:
            step_type = str(step.get("type", "")).strip().lower()
            step_workdir = self._step_workdir(workspace=workspace, step=step)
            if step_type == "cmd":
                result = self.executor.run_cmd(
                    command=str(step.get("command", "")),
                    workdir=step_workdir,
                    timeout=int(step.get("timeout", 120)),
                )
            elif step_type == "powershell":
                result = self.executor.run_powershell(
                    command=str(step.get("command", "")),
                    workdir=step_workdir,
                    timeout=int(step.get("timeout", 120)),
                )
            elif step_type == "browser_fetch":
                result = self.browser.fetch(
                    url=str(step.get("url", "")),
                    timeout=int(step.get("timeout", 20)),
                )
            elif step_type == "browser_open":
                result = self.browser.open(url=str(step.get("url", "")))
            elif step_type == "browser_script":
                result = self.browser.script(
                    url=str(step.get("url", "")),
                    actions=step.get("actions", []) if isinstance(step.get("actions", []), list) else [],
                    timeout=int(step.get("timeout", 30)),
                )
            else:
                result = CommandResult(
                    success=False,
                    step_type=step_type or "unknown",
                    command=str(step),
                    exit_code=1,
                    stdout="",
                    stderr="unsupported_step",
                    reason="unsupported_step",
                )
            results.append(result)
            self._event(task_id=task_id, result=result)

            output_bytes = len((result.stdout or "").encode()) + len((result.stderr or "").encode())
            total_output_bytes += output_bytes
            if total_output_bytes > self.executor.policy.max_output_bytes:
                truncated = CommandResult(
                    success=False,
                    step_type="limit",
                    command="output_size_limit",
                    exit_code=1,
                    stdout=f"Stopped: total output exceeded {self.executor.policy.max_output_bytes} bytes",
                    stderr="",
                    reason="output_limit_exceeded",
                )
                results.append(truncated)
                self._event(task_id=task_id, result=truncated)
                break
        return results

    def _step_workdir(self, workspace: Path | None, step: dict[str, Any]) -> Path | None:
        raw = step.get("workdir")
        if raw is None:
            return workspace
        text = str(raw).strip()
        if not text:
            return workspace
        path = Path(text)
        if path.is_absolute():
            return path
        base = workspace or self.executor.workspace_root
        return (base / path).resolve()

    def _event(self, task_id: str, result: CommandResult) -> None:
        if self.event_logger is None:
            return
        self.event_logger.emit(
            "execution_step",
            {
                "task_id": task_id,
                "success": result.success,
                "step_type": result.step_type,
                "command": result.command,
                "exit_code": result.exit_code,
                "reason": result.reason,
            },
        )
