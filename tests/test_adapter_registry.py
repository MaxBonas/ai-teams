import json
import tempfile
import unittest
from pathlib import Path

from aiteam.adapters import build_external_adapter_template, load_external_adapters


class AdapterRegistryTests(unittest.TestCase):
    def test_build_template_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "adapters.json"
            build_external_adapter_template(path)
            self.assertTrue(path.exists())
            adapters = load_external_adapters(path)
            self.assertTrue(adapters)

    def test_load_invalid_entries_skips_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "adapters.json"
            payload = {
                "external_adapters": [
                    {"type": "external_program", "name": "bad", "command": "not-list"},
                    {
                        "type": "external_program",
                        "name": "ok",
                        "provider": "custom",
                        "model": "v1",
                        "command": ["python", "-c", "print('ok')"],
                        "role_targets": ["engineer"],
                    },
                ]
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            adapters = load_external_adapters(path)
            self.assertEqual(len(adapters), 1)
            self.assertEqual(adapters[0].name, "ok")

    def test_secondary_priority_and_disabled_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "adapters.json"
            payload = {
                "external_adapters": [
                    {
                        "type": "external_program",
                        "name": "disabled_tool",
                        "provider": "custom",
                        "model": "v1",
                        "enabled": False,
                        "command": ["python", "-c", "print('disabled')"],
                    },
                    {
                        "type": "external_program",
                        "name": "secondary_tool",
                        "provider": "custom",
                        "model": "v1",
                        "priority": "secondary",
                        "requires_approval": True,
                        "command": ["python", "-c", "print('ok')"],
                    },
                ]
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            adapters = load_external_adapters(path)

            self.assertEqual(len(adapters), 1)
            self.assertEqual(adapters[0].name, "secondary_tool")
            self.assertEqual(adapters[0].routing_priority, 200)
            self.assertTrue(adapters[0].requires_approval)


if __name__ == "__main__":
    unittest.main()
