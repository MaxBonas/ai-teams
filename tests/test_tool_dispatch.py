"""Tests para ToolDispatcher y el arranque lazy de filesystem_mcp."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4


class EnsureFilesystemMcpRunningTests(unittest.TestCase):
    """Tests para ToolDispatcher._ensure_filesystem_mcp_running."""

    def setUp(self) -> None:
        self._previous_tempdir = tempfile.tempdir
        self._local_temp_root = Path.cwd() / ".tmp_test_tool_dispatch"
        self._local_temp_root.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(self._local_temp_root)

    def tearDown(self) -> None:
        tempfile.tempdir = self._previous_tempdir
        shutil.rmtree(self._local_temp_root, ignore_errors=True)

    def _make_dispatcher(self, runtime_dir: Path, catalog_path: Path | None = None):
        from aiteam.tool_dispatch import ToolDispatcher
        cat = catalog_path or (runtime_dir / "tool_sources.catalog.json")
        return ToolDispatcher(
            catalog_path=cat,
            runtime_dir=runtime_dir,
            environment="dev",
        )

    def _fake_mgr(self, *, enabled: bool = True, has_workspace: bool = True, already_running: bool = False):
        """Construye un MCPServerManager mock con filesystem_mcp."""
        from aiteam.mcp_manager import MCPServerConfig

        config = MCPServerConfig(
            name="filesystem_mcp",
            command="npx.cmd",
            args=["-y", "@modelcontextprotocol/server-filesystem"] + (
                ["C:/projects/my_project"] if has_workspace else []
            ),
            enabled=enabled,
        )
        mgr = MagicMock()
        mgr._configs = {"filesystem_mcp": config}

        if already_running:
            proc = MagicMock()
            proc.is_running = True
            mgr._servers = {"filesystem_mcp": proc}
        else:
            mgr._servers = {}

        mgr.start_server = MagicMock(return_value=(True, "started"))
        return mgr

    def test_starts_server_when_workspace_configured_and_not_running(self) -> None:
        """Debe llamar start_server si el workspace está en args y el server no corre."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            disp = self._make_dispatcher(runtime_dir)
            mgr = self._fake_mgr(enabled=True, has_workspace=True, already_running=False)

            disp._ensure_filesystem_mcp_running(mgr)

            mgr.start_server.assert_called_once_with(
                "filesystem_mcp", timeout=disp._FILESYSTEM_MCP_START_TIMEOUT
            )

    def test_no_op_when_already_running(self) -> None:
        """No debe intentar arrancar si el servidor ya está corriendo."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            disp = self._make_dispatcher(runtime_dir)
            mgr = self._fake_mgr(enabled=True, has_workspace=True, already_running=True)

            disp._ensure_filesystem_mcp_running(mgr)

            mgr.start_server.assert_not_called()

    def test_no_op_when_disabled(self) -> None:
        """No debe arrancar si filesystem_mcp está deshabilitado."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            disp = self._make_dispatcher(runtime_dir)
            mgr = self._fake_mgr(enabled=False, has_workspace=True, already_running=False)

            disp._ensure_filesystem_mcp_running(mgr)

            mgr.start_server.assert_not_called()

    def test_no_op_when_no_workspace_path_in_args(self) -> None:
        """No debe arrancar si no hay workspace path inyectado en los args."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            disp = self._make_dispatcher(runtime_dir)
            mgr = self._fake_mgr(enabled=True, has_workspace=False, already_running=False)

            disp._ensure_filesystem_mcp_running(mgr)

            mgr.start_server.assert_not_called()

    def test_no_op_when_filesystem_mcp_absent_from_configs(self) -> None:
        """No debe fallar si filesystem_mcp no está en _configs."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            disp = self._make_dispatcher(runtime_dir)
            mgr = MagicMock()
            mgr._configs = {}
            mgr._servers = {}
            mgr.start_server = MagicMock()

            disp._ensure_filesystem_mcp_running(mgr)

            mgr.start_server.assert_not_called()

    def test_exception_in_start_server_is_swallowed(self) -> None:
        """Un fallo en start_server no debe propagar excepción."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            disp = self._make_dispatcher(runtime_dir)
            mgr = self._fake_mgr(enabled=True, has_workspace=True, already_running=False)
            mgr.start_server.side_effect = RuntimeError("npx not found")

            # No debe lanzar
            disp._ensure_filesystem_mcp_running(mgr)

    def test_build_tool_context_triggers_filesystem_mcp_for_engineer(self) -> None:
        """build_tool_context_for_agent llama _ensure_filesystem_mcp_running para engineer."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            disp = self._make_dispatcher(runtime_dir)

            started = []

            def fake_ensure(mgr):
                started.append(True)

            disp._ensure_filesystem_mcp_running = fake_ensure

            fake_mgr = MagicMock()
            fake_mgr.list_tools.return_value = []
            disp._mcp_manager = fake_mgr

            disp.build_tool_context_for_agent(role="engineer")

            self.assertEqual(len(started), 1, "debe llamar _ensure_filesystem_mcp_running para engineer")

    def test_build_tool_context_does_not_trigger_for_team_lead(self) -> None:
        """build_tool_context_for_agent NO llama _ensure_filesystem_mcp_running para team_lead."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            disp = self._make_dispatcher(runtime_dir)

            started = []

            def fake_ensure(mgr):
                started.append(True)

            disp._ensure_filesystem_mcp_running = fake_ensure

            fake_mgr = MagicMock()
            fake_mgr.list_tools.return_value = []
            disp._mcp_manager = fake_mgr

            disp.build_tool_context_for_agent(role="team_lead")

            self.assertEqual(len(started), 0, "team_lead no debe arrancar filesystem_mcp")


class EngineerPathAnnotationInstructionsTests(unittest.TestCase):
    """Tests para verificar que las instrucciones path= siempre aparecen para engineer."""

    def setUp(self) -> None:
        self._previous_tempdir = tempfile.tempdir
        self._local_temp_root = Path.cwd() / ".tmp_test_tool_dispatch_path"
        self._local_temp_root.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(self._local_temp_root)

    def tearDown(self) -> None:
        tempfile.tempdir = self._previous_tempdir
        shutil.rmtree(self._local_temp_root, ignore_errors=True)

    def _make_dispatcher(self, runtime_dir: Path):
        from aiteam.tool_dispatch import ToolDispatcher
        cat = runtime_dir / "tool_sources.catalog.json"
        return ToolDispatcher(catalog_path=cat, runtime_dir=runtime_dir, environment="dev")

    def test_engineer_always_sees_path_annotation_instructions(self) -> None:
        """Engineer debe ver instrucciones path= incluso cuando filesystem_mcp está activo."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / ".aiteam"
            runtime_dir.mkdir()
            disp = self._make_dispatcher(runtime_dir)

            fake_mgr = MagicMock()
            # Simular filesystem_mcp activo con 14 herramientas
            mock_tool = MagicMock()
            mock_tool.server_name = "filesystem_mcp"
            mock_tool.name = "write_file"
            fake_mgr.list_tools.return_value = [mock_tool]
            fake_mgr._configs = {}
            disp._mcp_manager = fake_mgr
            disp._ensure_filesystem_mcp_running = lambda mgr: None

            context = disp.build_tool_context_for_agent(role="engineer")

            self.assertIn("path=", context)
            self.assertIn("ESCRITURA DE ARCHIVOS", context)

    def test_engineer_sees_path_annotation_when_mcp_inactive(self) -> None:
        """Engineer ve instrucciones path= también cuando MCP no está activo."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / ".aiteam"
            runtime_dir.mkdir()
            disp = self._make_dispatcher(runtime_dir)

            fake_mgr = MagicMock()
            fake_mgr.list_tools.return_value = []  # sin herramientas activas
            fake_mgr._configs = {}
            disp._mcp_manager = fake_mgr
            disp._ensure_filesystem_mcp_running = lambda mgr: None

            context = disp.build_tool_context_for_agent(role="engineer")

            self.assertIn("path=", context)
            self.assertIn("ESCRITURA DE ARCHIVOS", context)

    def test_team_lead_does_not_see_path_annotation_instructions(self) -> None:
        """team_lead no recibe instrucciones de escritura de archivos."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / ".aiteam"
            runtime_dir.mkdir()
            disp = self._make_dispatcher(runtime_dir)

            fake_mgr = MagicMock()
            fake_mgr.list_tools.return_value = []
            fake_mgr._configs = {}
            disp._mcp_manager = fake_mgr

            context = disp.build_tool_context_for_agent(role="team_lead")

            self.assertNotIn("ESCRITURA DE ARCHIVOS", context)

    def test_use_tool_write_file_not_shown_as_example(self) -> None:
        """filesystem_mcp:write_file (formato roto) no debe aparecer en instrucciones."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / ".aiteam"
            runtime_dir.mkdir()
            disp = self._make_dispatcher(runtime_dir)

            fake_mgr = MagicMock()
            fake_mgr.list_tools.return_value = []
            fake_mgr._configs = {}
            disp._mcp_manager = fake_mgr
            disp._ensure_filesystem_mcp_running = lambda mgr: None

            context = disp.build_tool_context_for_agent(role="engineer")

            self.assertNotIn("filesystem_mcp:write_file", context)


if __name__ == "__main__":
    unittest.main()
