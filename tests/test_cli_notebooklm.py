import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiteam import cli


class CliNotebookLMTests(unittest.TestCase):
    def test_notebooklm_connect_creates_enabled_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            cli.cmd_notebooklm_connect(runtime_dir)

            payload = json.loads((runtime_dir / "adapters.json").read_text(encoding="utf-8"))
            adapters = payload.get("external_adapters", [])
            adapter = next(item for item in adapters if item.get("name") == "notebooklm_bridge")
            self.assertTrue(bool(adapter.get("enabled")))
            self.assertIn("notebooklm-sync", adapter.get("command", []))

    def test_notebooklm_sync_uses_default_local_bridge_when_no_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            runtime_dir = Path(tmp) / "runtime"
            status = cli.cmd_notebooklm_sync(
                runtime_dir=runtime_dir,
                notebook_id="",
                title="Sync Test",
                source="tests",
                content_file="",
                from_prompt="hello notebooklm",
                export_format="markdown",
                days=7,
                dry_run=False,
                quiet=True,
            )

            self.assertEqual(status.get("mode"), "command")
            self.assertTrue(bool(status.get("connected")))
            self.assertTrue((runtime_dir / "notebooklm_sync_status.json").exists())
            self.assertTrue((runtime_dir / "notebooklm_outbox").exists())
            self.assertTrue((runtime_dir / "notebooklm_ready").exists())

    def test_notebooklm_sync_uses_command_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "NOTEBOOKLM_INGEST_COMMAND": '["python","-c","import pathlib,sys;print(pathlib.Path(sys.argv[1]).exists())","{payload_path}"]',
            },
            clear=True,
        ):
            runtime_dir = Path(tmp) / "runtime"
            status = cli.cmd_notebooklm_sync(
                runtime_dir=runtime_dir,
                notebook_id="",
                title="Sync Command",
                source="tests",
                content_file="",
                from_prompt="hello command",
                export_format="markdown",
                days=7,
                dry_run=False,
                quiet=True,
            )

            self.assertEqual(status.get("mode"), "command")
            self.assertTrue(bool(status.get("connected")))


if __name__ == "__main__":
    unittest.main()
