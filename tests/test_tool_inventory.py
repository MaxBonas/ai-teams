import json
import tempfile
import unittest
from pathlib import Path

from aiteam.tool_inventory import scan_tools, write_inventory


class ToolInventoryTests(unittest.TestCase):
    def test_scan_tools_detects_known_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            android = root / "AndroidWeb"
            android.mkdir()
            (android / "README.md").write_text("Android Web Controller", encoding="utf-8")
            (android / "package.json").write_text("{}", encoding="utf-8")

            video = root / "VideoGenerator"
            video.mkdir()
            (video / "package.json").write_text('{"dependencies":{"remotion":"4"}}', encoding="utf-8")

            tools = scan_tools(root)
            adapter_names = {item.adapter_name for item in tools}
            self.assertIn("android_browser_auditor", adapter_names)
            self.assertIn("video_editor_remotion", adapter_names)

    def test_write_inventory_creates_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            whatsapp = root / "Secretaria_Whatsapp"
            whatsapp.mkdir()
            (whatsapp / "README.md").write_text("WhatsApp assistant", encoding="utf-8")

            output = root / "inventory.json"
            payload = write_inventory(root=root, output_path=output)

            self.assertTrue(output.exists())
            loaded = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(loaded["total"], payload["total"])
            self.assertTrue(any(item["adapter_name"] == "secretariawhatsapp" for item in loaded["tools"]))


if __name__ == "__main__":
    unittest.main()
