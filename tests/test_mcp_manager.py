import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from aiteam.mcp_manager import MCPServerManager


class MCPManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_tempdir = tempfile.tempdir
        self._previous_temporary_directory = tempfile.TemporaryDirectory
        self._local_temp_root = Path.cwd() / ".tmp_test_mcp_manager"
        self._local_temp_root.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(self._local_temp_root)

        class _WorkspaceTemporaryDirectory:
            def __init__(
                inner_self,
                suffix: str | None = None,
                prefix: str | None = None,
                dir: str | Path | None = None,
                ignore_cleanup_errors: bool = False,
            ) -> None:
                inner_self._ignore_cleanup_errors = ignore_cleanup_errors
                inner_self._root = Path(dir) if dir else self._local_temp_root
                inner_self._prefix = prefix or "tmp"
                inner_self._suffix = suffix or ""
                inner_self.name = ""

            def __enter__(inner_self) -> str:
                candidate = (
                    inner_self._root
                    / f"{inner_self._prefix}{uuid4().hex}{inner_self._suffix}"
                )
                candidate.mkdir(parents=True, exist_ok=False)
                inner_self.name = str(candidate)
                return inner_self.name

            def __exit__(inner_self, exc_type, exc, tb) -> bool:
                shutil.rmtree(inner_self.name, ignore_errors=True)
                return False

            def cleanup(inner_self) -> None:
                shutil.rmtree(inner_self.name, ignore_errors=True)

        tempfile.TemporaryDirectory = _WorkspaceTemporaryDirectory

    def tearDown(self) -> None:
        tempfile.tempdir = self._previous_tempdir
        tempfile.TemporaryDirectory = self._previous_temporary_directory

    def test_classify_health_reason_categories(self) -> None:
        self.assertEqual(
            MCPServerManager.classify_health_reason("probe_failed:Error accessing directory C:\\Users\\she__\\x", "unhealthy"),
            "path_missing",
        )
        self.assertEqual(
            MCPServerManager.classify_health_reason("probe_failed:Please set SLACK_BOT_TOKEN", "unhealthy"),
            "credentials_missing",
        )
        self.assertEqual(
            MCPServerManager.classify_health_reason("probe_failed:Please provide a database URL as a command-line argument", "unhealthy"),
            "configuration_required",
        )
        self.assertEqual(
            MCPServerManager.classify_health_reason("probe_failed:npm error code E404", "unhealthy"),
            "package_unavailable",
        )

    def test_health_recommendation_maps_category_to_action(self) -> None:
        self.assertEqual(
            MCPServerManager.health_recommendation(
                category="path_missing",
                enabled=True,
                requires_approval=False,
                source_type="npm",
            ),
            "repair_path_or_workspace",
        )
        self.assertEqual(
            MCPServerManager.health_recommendation(
                category="package_unavailable",
                enabled=True,
                requires_approval=False,
                source_type="npm",
            ),
            "replace_or_disable_catalog_entry",
        )
        self.assertEqual(
            MCPServerManager.health_recommendation(
                category="healthy",
                enabled=False,
                requires_approval=True,
                source_type="npm",
            ),
            "enable_when_needed",
        )

    def test_portability_status_detects_placeholders_and_user_bound_paths(self) -> None:
        from aiteam.mcp_manager import MCPServerConfig

        with patch.dict("os.environ", {"USERNAME": "Max", "USERPROFILE": r"C:\Users\Max"}, clear=False):
            portable_status = MCPServerManager.portability_status(
                MCPServerConfig(
                    name="filesystem",
                    command="%USERPROFILE%\\AppData\\Roaming\\npm\\npx.cmd",
                    args=["-y", "@modelcontextprotocol/server-filesystem"],
                )
            )
            self.assertEqual(portable_status, ("portable", "uses_placeholders"))

            bound_status = MCPServerManager.portability_status(
                MCPServerConfig(
                    name="git",
                    command=r"C:\Users\she__\AppData\Roaming\npm\npx.cmd",
                    args=["-y", "@modelcontextprotocol/server-git"],
                )
            )
            self.assertEqual(bound_status, ("user_bound", "bound_to_other_user:she__"))

    def test_parse_opencode_mcp_list_extracts_servers_and_commands(self) -> None:
        text = """
        [34m•[39m  ✓ memory connected
              npx -y @modelcontextprotocol/server-memory

        [34m•[39m  ✕ filesystem failed
              MCP error -32000: Connection closed
              npx -y @modelcontextprotocol/server-filesystem "C:\\Users\\Max\\Antigravity Projects"

        [34m•[39m  ✓ puppeteer connected
              npx -y @modelcontextprotocol/server-puppeteer
        """

        rows = MCPServerManager.parse_opencode_mcp_list(text)
        by_name = {row["name"]: row for row in rows}

        self.assertIn("memory", by_name)
        self.assertIn("filesystem", by_name)
        self.assertIn("puppeteer", by_name)
        self.assertEqual(by_name["memory"]["command"].lower(), "npx")
        self.assertEqual(
            by_name["memory"]["source"],
            "@modelcontextprotocol/server-memory",
        )
        self.assertEqual(by_name["memory"]["health_status"], "healthy")
        self.assertEqual(by_name["filesystem"]["health_status"], "unhealthy")
        self.assertIn("repo_read", by_name["filesystem"]["capabilities"])
        self.assertIn("browser_test", by_name["puppeteer"]["capabilities"])

    def test_bootstrap_from_opencode_imports_new_servers_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            mcp_list = root / "mcp_list.txt"
            mcp_list.write_text(
                "\n".join(
                    [
                        "•  ✓ memory connected",
                        "    npx -y @modelcontextprotocol/server-memory",
                        "",
                        "•  ✕ puppeteer failed",
                        "    MCP error -32000: Connection closed",
                        "    npx -y @modelcontextprotocol/server-puppeteer",
                    ]
                ),
                encoding="utf-8",
            )
            (runtime / "mcp_servers.json").write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "memory",
                                "command": "npx",
                                "args": ["-y", "@modelcontextprotocol/server-memory"],
                                "enabled": True,
                                "transport": "stdio",
                                "source_type": "npm",
                                "source": "@modelcontextprotocol/server-memory",
                                "capabilities": ["external_mcp"],
                                "role_targets": [],
                                "health_status": "healthy",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {"AITEAM_OPENCODE_MCP_LIST_PATH": str(mcp_list)},
                clear=False,
            ):
                manager = MCPServerManager(runtime_dir=runtime)
                imported = manager.bootstrap_from_opencode()

            self.assertEqual(imported, 1)
            payload = json.loads((runtime / "mcp_servers.json").read_text(encoding="utf-8"))
            by_name = {row["name"]: row for row in payload["servers"]}
            self.assertIn("memory", by_name)
            self.assertIn("puppeteer", by_name)
            self.assertFalse(bool(by_name["puppeteer"]["enabled"]))
            self.assertEqual(by_name["puppeteer"]["health_status"], "unhealthy")
            self.assertIn("browser_test", list(by_name["puppeteer"]["capabilities"]))

    def test_auto_repair_user_paths_rewrites_wrong_windows_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "USERNAME": "Max",
                "USERPROFILE": r"C:\Users\Max",
            },
            clear=False,
        ):
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "mcp_servers.json").write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "filesystem",
                                "command": r"C:\Users\she__\AppData\Roaming\npm\npx.cmd",
                                "args": [
                                    "-y",
                                    "@modelcontextprotocol/server-filesystem",
                                    r"C:\Users\she__\Documents\Antigravity Projects",
                                ],
                                "env": {
                                    "WORKSPACE_ROOT": r"C:\Users\she__\Documents\Antigravity Projects\Ai_Teams",
                                },
                                "enabled": True,
                                "transport": "stdio",
                                "source_type": "custom",
                                "source": r"C:\Users\she__\Documents\Antigravity Projects\mcp\filesystem",
                                "capabilities": ["external_mcp", "repo_read"],
                                "role_targets": ["scout"],
                                "health_status": "unknown",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            manager = MCPServerManager(runtime_dir=runtime)
            config = manager._configs["filesystem"]

            self.assertEqual(config.command, r"C:\Users\Max\AppData\Roaming\npm\npx.cmd")
            self.assertEqual(
                config.args[-1],
                r"C:\Users\Max\Documents\Antigravity Projects",
            )
            self.assertEqual(
                config.env["WORKSPACE_ROOT"],
                r"C:\Users\Max\Documents\Antigravity Projects\Ai_Teams",
            )
            self.assertEqual(
                config.source,
                r"C:\Users\Max\Documents\Antigravity Projects\mcp\filesystem",
            )
            events = manager.event_history(server_name="filesystem", limit=10)
            repair_events = [entry for entry in events if entry.get("event") == "mcp_path_auto_repaired"]
            self.assertTrue(repair_events)

    def test_list_healthy_retries_old_unhealthy_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "mcp_servers.json").write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "memory",
                                "command": "npx",
                                "args": ["-y", "@modelcontextprotocol/server-memory"],
                                "enabled": True,
                                "transport": "stdio",
                                "source_type": "npm",
                                "source": "@modelcontextprotocol/server-memory",
                                "capabilities": ["external_mcp"],
                                "role_targets": [],
                                "health_status": "unhealthy",
                                "last_checked": "2026-03-30T00:00:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            manager = MCPServerManager(runtime_dir=runtime)

            def _fake_start(name: str, timeout: int = 30) -> tuple[bool, str]:
                manager._configs[name].health_status = "healthy"
                manager._configs[name].last_checked = "2026-03-31T00:00:00+00:00"
                return True, "started"

            with patch.object(manager, "start_server", side_effect=_fake_start) as mocked_start:
                healthy = manager.list_healthy(retry_unhealthy=True, retry_after_seconds=0)

            self.assertEqual(healthy, ["memory"])
            mocked_start.assert_called_once()
            events = manager.event_history(server_name="memory", limit=10)
            retry_events = [entry for entry in events if entry.get("event") == "mcp_health_retry_attempted"]
            self.assertTrue(retry_events)

    def test_load_and_save_preserves_health_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            config_path = runtime / "mcp_servers.json"
            config_path.write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "filesystem",
                                "command": "npx",
                                "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                                "enabled": True,
                                "transport": "stdio",
                                "source_type": "npm",
                                "source": "@modelcontextprotocol/server-filesystem",
                                "capabilities": ["external_mcp", "repo_read"],
                                "role_targets": ["scout"],
                                "health_status": "unhealthy",
                                "health_reason": "probe_failed: path missing",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            manager = MCPServerManager(runtime_dir=runtime)
            self.assertEqual(manager._configs["filesystem"].health_reason, "probe_failed: path missing")
            manager._save_configs()

            persisted = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                str(persisted["servers"][0].get("health_reason", "") or ""),
                "probe_failed: path missing",
            )


if __name__ == "__main__":
    unittest.main()
