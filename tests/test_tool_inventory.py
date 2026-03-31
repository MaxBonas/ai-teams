import json
import tempfile
import unittest
from pathlib import Path

from aiteam.tool_inventory import (
    canonical_tool_capabilities,
    derive_target_capabilities,
    normalize_lsp_targets,
    normalize_skill_targets,
    normalize_tool_capabilities,
    scan_tools,
    write_effective_inventory_snapshot,
    write_inventory,
)


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
            self.assertEqual(loaded["schema_version"], "tool_inventory_v2")
            self.assertTrue(isinstance(loaded.get("capability_catalog", []), list))
            self.assertTrue(any(item["adapter_name"] == "secretariawhatsapp" for item in loaded["tools"]))

    def test_normalize_tool_capabilities_maps_to_canonical_catalog(self) -> None:
        normalized = normalize_tool_capabilities(
            ["analysis", "browser_testing", "video_generation"],
            tags=["browser", "qa"],
            category="mcp",
        )
        self.assertIn("repo_read", normalized)
        self.assertIn("browser_test", normalized)
        self.assertIn("browser_nav", normalized)
        self.assertIn("build_execute", normalized)
        self.assertIn("external_mcp", normalized)

        catalog_names = {item["name"] for item in canonical_tool_capabilities()}
        self.assertTrue(set(normalized).issubset(catalog_names))

    def test_write_effective_inventory_snapshot_persists_task_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "runtime" / "effective_tool_inventory.json"
            snapshot = write_effective_inventory_snapshot(
                output_path=output,
                project_root=root,
                task_id="TASK-1",
                required_capabilities=["analysis", "browser_testing"],
                skill_targets=["playwright", "context7_research_skill"],
                lsp_targets=["symbols", "impact"],
                selected_tools=[
                    {
                        "name": "playwright_mcp",
                        "category": "mcp",
                        "source": "@playwright/mcp",
                        "capabilities": ["browser_testing"],
                        "tags": ["browser", "qa"],
                        "cost_tier": "low",
                        "latency_tier": "medium",
                        "risk_tier": "medium",
                        "environment_targets": ["dev", "stage"],
                    }
                ],
            )

            self.assertEqual(snapshot["task_id"], "TASK-1")
            self.assertTrue(output.exists())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "effective_tool_inventory_v1")
            self.assertEqual(len(payload["entries"]), 1)
            entry = payload["entries"][0]
            self.assertIn("repo_read", entry["required_capabilities"])
            self.assertIn("browser_test", entry["required_capabilities"])
            self.assertEqual(
                entry["skill_targets"],
                ["context7_research_skill", "playwright"],
            )
            self.assertEqual(entry["lsp_targets"], ["impact", "references", "symbols"])
            self.assertEqual(entry["selected_tools"][0]["name"], "playwright_mcp")
            self.assertIn("external_mcp", entry["selected_tools"][0]["canonical_capabilities"])

    def test_normalize_skill_and_lsp_targets_are_canonical(self) -> None:
        self.assertEqual(
            normalize_skill_targets([" Playwright ", "context7_research_skill", "playwright"]),
            ["context7_research_skill", "playwright"],
        )
        self.assertEqual(
            normalize_lsp_targets(["symbols", "impact"]),
            ["impact", "references", "symbols"],
        )
        self.assertEqual(
            derive_target_capabilities(
                skill_targets=["playwright"],
                lsp_targets=["impact", "symbols"],
            ),
            ["lsp_references", "lsp_symbols", "skill_run"],
        )


if __name__ == "__main__":
    unittest.main()
