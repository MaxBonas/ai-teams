import tempfile
import unittest
from pathlib import Path

from aiteam.execution import (
    BrowserController,
    CommandPolicy,
    ExecutionEngine,
    LocalCommandExecutor,
    PlaywrightBrowserController,
)


class ExecutionTests(unittest.TestCase):
    def test_cmd_execution_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executor = LocalCommandExecutor(
                workspace_root=Path(tmp),
                policy=CommandPolicy(),
            )
            result = executor.run_cmd("python --version", timeout=30)
            self.assertTrue(result.success)
            self.assertEqual(result.step_type, "cmd")

    def test_blocked_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executor = LocalCommandExecutor(
                workspace_root=Path(tmp),
                policy=CommandPolicy(),
            )
            result = executor.run_cmd("rm -rf /", timeout=10)
            self.assertFalse(result.success)
            self.assertIsNotNone(result.reason)

    def test_execution_engine_supports_unknown_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = ExecutionEngine(
                executor=LocalCommandExecutor(
                    workspace_root=Path(tmp),
                    policy=CommandPolicy(),
                ),
                browser=BrowserController(),
            )
            results = engine.execute_plan(task_id="T-X", plan=[{"type": "unknown", "foo": "bar"}])
            self.assertEqual(len(results), 1)
            self.assertFalse(results[0].success)

    def test_browser_script_with_basic_controller_is_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = ExecutionEngine(
                executor=LocalCommandExecutor(
                    workspace_root=Path(tmp),
                    policy=CommandPolicy(),
                ),
                browser=BrowserController(),
            )
            results = engine.execute_plan(
                task_id="T-B",
                plan=[
                    {
                        "type": "browser_script",
                        "url": "https://example.com",
                        "actions": [{"type": "wait_for_selector", "selector": "body"}],
                    }
                ],
            )
            self.assertEqual(len(results), 1)
            self.assertFalse(results[0].success)
            self.assertEqual(results[0].reason, "browser_script_unsupported")

    def test_executor_allows_additional_workspace_roots(self) -> None:
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as tools:
            workspace_path = Path(workspace)
            tools_path = Path(tools)
            executor = LocalCommandExecutor(
                workspace_root=workspace_path,
                policy=CommandPolicy(),
                additional_roots=[tools_path],
            )
            result = executor.run_powershell(
                "Write-Output (Get-Location).Path",
                workdir=tools_path,
                timeout=30,
            )
            self.assertTrue(result.success)
            self.assertIn(str(tools_path).lower(), result.stdout.lower())

    def test_executor_rejects_workdir_outside_allowed_roots(self) -> None:
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as tools:
            workspace_path = Path(workspace)
            outsider = Path(tools).parent
            executor = LocalCommandExecutor(
                workspace_root=workspace_path,
                policy=CommandPolicy(),
                additional_roots=[],
            )
            result = executor.run_powershell(
                "Write-Output (Get-Location).Path",
                workdir=outsider,
                timeout=30,
            )
            self.assertTrue(result.success)
            self.assertIn(str(workspace_path).lower(), result.stdout.lower())

    def test_execution_plan_step_workdir_is_applied(self) -> None:
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as tools:
            workspace_path = Path(workspace)
            tools_path = Path(tools)
            engine = ExecutionEngine(
                executor=LocalCommandExecutor(
                    workspace_root=workspace_path,
                    policy=CommandPolicy(),
                    additional_roots=[tools_path],
                ),
                browser=BrowserController(),
            )
            results = engine.execute_plan(
                task_id="T-WORKDIR",
                workspace=workspace_path,
                plan=[
                    {
                        "type": "powershell",
                        "command": "Write-Output (Get-Location).Path",
                        "workdir": str(tools_path),
                    }
                ],
            )
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].success)
            self.assertIn(str(tools_path).lower(), results[0].stdout.lower())

    def test_playwright_helper_resolves_relative_screenshot_path(self) -> None:
        step = {"path": "runtime/evidence/test.png"}
        path = PlaywrightBrowserController._resolve_screenshot_path(step)
        self.assertTrue(path.is_absolute())
        self.assertTrue(str(path).lower().endswith("runtime\\evidence\\test.png"))

    def test_playwright_helper_timeout_conversion(self) -> None:
        timeout = PlaywrightBrowserController._action_timeout_ms({"timeout": 1.2}, 30000)
        self.assertEqual(timeout, 1200)


if __name__ == "__main__":
    unittest.main()
