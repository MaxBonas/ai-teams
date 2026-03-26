import json
import tempfile
import unittest
from pathlib import Path

from aiteam.autotools import AutoToolIntegrator


class AutoToolsTests(unittest.TestCase):
    def test_integrates_mcp_and_adapter_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            project = root / "project"
            project.mkdir()

            integrator = AutoToolIntegrator(runtime_dir=runtime, project_root=project)
            report = integrator.integrate_from_metadata(
                task_id="T-1",
                metadata={
                    "tool_requirements": [
                        {
                            "name": "context7_mcp",
                            "category": "mcp",
                            "source_type": "npm",
                            "source": "context7-server",
                            "command": ["python", "-c", "print('ok')"],
                            "capabilities": ["documentation"],
                            "role_targets": ["researcher"],
                            "enabled": True,
                        }
                    ]
                },
                internet_allowed=True,
            )

            self.assertTrue(report.success)
            self.assertIn("context7_mcp", report.integrated_adapters)
            self.assertIn("context7_mcp", report.integrated_mcp_servers)

            adapters = json.loads((runtime / "adapters.json").read_text(encoding="utf-8"))
            adapter_names = {item["name"] for item in adapters["external_adapters"]}
            self.assertIn("context7_mcp", adapter_names)

            mcp = json.loads((runtime / "mcp_servers.json").read_text(encoding="utf-8"))
            server_names = {item["name"] for item in mcp["servers"]}
            self.assertIn("context7_mcp", server_names)

    def test_internet_blocked_fails_required_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            project = root / "project"
            project.mkdir()

            integrator = AutoToolIntegrator(runtime_dir=runtime, project_root=project)
            report = integrator.integrate_from_metadata(
                task_id="T-2",
                metadata={
                    "tool_requirements": [
                        {
                            "name": "remote_tool",
                            "category": "cli",
                            "source_type": "npm",
                            "source": "remote-tool",
                            "required": True,
                            "uses_internet": True,
                        }
                    ]
                },
                internet_allowed=False,
            )
            self.assertFalse(report.success)
            self.assertTrue(any("internet_tool_blocked" in item for item in report.errors))

    def test_optional_acquire_failure_disables_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            project = root / "project"
            project.mkdir()

            integrator = AutoToolIntegrator(runtime_dir=runtime, project_root=project)
            report = integrator.integrate_from_metadata(
                task_id="T-2B",
                metadata={
                    "tool_requirements": [
                        {
                            "name": "optional_bad_source",
                            "category": "cli",
                            "source_type": "unsupported",
                            "source": "something",
                            "required": False,
                            "uses_internet": True,
                            "enabled": True,
                            "acquire": True,
                        }
                    ]
                },
                internet_allowed=True,
            )
            self.assertTrue(report.success)
            self.assertTrue(any("auto_disabled_due_to_acquire_failure" in m for m in report.messages))

            payload = json.loads((runtime / "adapters.json").read_text(encoding="utf-8"))
            adapter = payload["external_adapters"][0]
            self.assertFalse(adapter["enabled"])

    def test_integrates_builtin_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            project = root / "project"
            project.mkdir()

            integrator = AutoToolIntegrator(runtime_dir=runtime, project_root=project)
            report = integrator.integrate_from_metadata(
                task_id="T-3",
                metadata={
                    "tool_requirements": [
                        {
                            "name": "remotion_skill",
                            "category": "skill",
                            "source_type": "builtin",
                            "description": "Skill de video con remotion",
                            "capabilities": ["video_generation"],
                        }
                    ]
                },
                internet_allowed=True,
            )
            self.assertTrue(report.success)
            self.assertIn("remotion_skill", report.integrated_skills)

            skill_path = project / ".cloud" / "skills" / "remotion_skill" / "skill.md"
            self.assertTrue(skill_path.exists())

    def test_suggest_requirements_from_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            project = root / "project"
            project.mkdir()
            config = project / "config"
            config.mkdir()
            catalog = config / "tool_sources.catalog.json"
            catalog.write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "semgrep_mcp",
                                "category": "mcp",
                                "capabilities": ["security_scan", "code_quality"],
                            },
                            {
                                "name": "context7_mcp",
                                "category": "mcp",
                                "capabilities": ["documentation"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            integrator = AutoToolIntegrator(
                runtime_dir=runtime,
                project_root=project,
                catalog_path=catalog,
            )
            suggestions = integrator.suggest_requirements({"security_scan"})
            self.assertTrue(suggestions)
            self.assertEqual(suggestions[0]["name"], "semgrep_mcp")

    def test_skill_library_sync_and_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            project = root / "project"
            project.mkdir()
            config = project / "config"
            config.mkdir()

            (config / "skills.library.json").write_text(
                json.dumps(
                    {
                        "skills": [
                            {
                                "name": "playwright_qa_skill",
                                "purpose": "QA browser con evidencia",
                                "roles": ["qa"],
                                "capabilities": ["browser_testing"],
                                "keywords": ["browser", "e2e"],
                                "mcp_servers": ["playwright_mcp"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            (runtime / "mcp_servers.json").write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "playwright_mcp",
                                "enabled": True,
                                "transport": "stdio",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            integrator = AutoToolIntegrator(runtime_dir=runtime, project_root=project)
            created = integrator.sync_skill_library(force=False)
            self.assertIn("playwright_qa_skill", created)

            guidance = integrator.guidance_for_task(
                role="qa",
                description="Ejecutar browser e2e con assertions",
                required_capabilities={"browser_testing"},
            )
            self.assertIn("playwright_qa_skill", guidance["skills"])
            self.assertIn("playwright_mcp", guidance["recommended_mcp"])
            self.assertIn("playwright_mcp", guidance["active_mcp"])
            self.assertIn("Skills aplicables", guidance["text"])

    def test_mcp_doctor_marks_health_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            project = root / "project"
            project.mkdir()

            (runtime / "mcp_servers.json").write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "python_mcp",
                                "command": "python",
                                "args": ["-V"],
                                "enabled": False,
                            },
                            {
                                "name": "missing_mcp",
                                "command": "missing-command-xyz",
                                "args": [],
                                "enabled": True,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            integrator = AutoToolIntegrator(runtime_dir=runtime, project_root=project)
            report = integrator.mcp_doctor(timeout=10, enable_healthy=True)
            self.assertEqual(report["total"], 2)
            self.assertGreaterEqual(report["healthy"], 1)

            payload = json.loads((runtime / "mcp_servers.json").read_text(encoding="utf-8"))
            rows = {item["name"]: item for item in payload["servers"]}
            self.assertEqual(rows["python_mcp"].get("health_status"), "healthy")
            self.assertTrue(rows["python_mcp"].get("enabled"))
            self.assertEqual(rows["missing_mcp"].get("health_status"), "unhealthy")

    def test_mcp_doctor_does_not_auto_enable_sensitive_without_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            project = root / "project"
            project.mkdir()

            (runtime / "mcp_servers.json").write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "python_sensitive_mcp",
                                "command": "python",
                                "args": ["-V"],
                                "enabled": False,
                                "requires_approval": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            integrator = AutoToolIntegrator(runtime_dir=runtime, project_root=project)
            report = integrator.mcp_doctor(timeout=10, enable_healthy=True, enable_sensitive=False)
            self.assertEqual(report["healthy"], 1)
            self.assertEqual(report["auto_enabled"], 0)
            self.assertEqual(report["skipped_sensitive"], 1)

            payload = json.loads((runtime / "mcp_servers.json").read_text(encoding="utf-8"))
            row = payload["servers"][0]
            self.assertFalse(row["enabled"])

    def test_skill_coverage_counts_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            project = root / "project"
            project.mkdir()
            events = runtime / "events.jsonl"
            events.write_text(
                "\n".join(
                    [
                        json.dumps({"event_type": "task_execution", "payload": {}}),
                        json.dumps({"event_type": "task_execution", "payload": {}}),
                        json.dumps({"event_type": "skill_mcp_guidance", "payload": {}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            integrator = AutoToolIntegrator(runtime_dir=runtime, project_root=project)
            coverage = integrator.skill_coverage()
            self.assertEqual(coverage["total_task_execution"], 2)
            self.assertEqual(coverage["skill_guidance_events"], 1)
            self.assertEqual(coverage["coverage_percent"], 50.0)


if __name__ == "__main__":
    unittest.main()
