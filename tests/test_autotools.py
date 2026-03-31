import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from aiteam.autotools import AutoToolIntegrator


class AutoToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_root = Path.cwd() / ".tmp_test_autotools"
        self._temp_root.mkdir(parents=True, exist_ok=True)
        self._orig_tempdir = tempfile.tempdir
        tempfile.tempdir = str(self._temp_root)
        self._orig_tempdir_factory = tempfile.TemporaryDirectory

        class _LocalTemporaryDirectory:
            def __init__(
                inner_self,
                suffix: str | None = None,
                prefix: str | None = None,
                dir: str | Path | None = None,
                ignore_cleanup_errors: bool = False,
            ) -> None:
                inner_self._ignore_cleanup_errors = ignore_cleanup_errors
                inner_self._root = Path(dir) if dir else self._temp_root
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

        tempfile.TemporaryDirectory = _LocalTemporaryDirectory

    def tearDown(self) -> None:
        tempfile.tempdir = self._orig_tempdir
        tempfile.TemporaryDirectory = self._orig_tempdir_factory
        shutil.rmtree(self._temp_root, ignore_errors=True)

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

    def test_suggest_requirements_prefers_replacement_skill_for_replaceable_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            project = root / "project"
            project.mkdir()
            config = project / "config"
            config.mkdir()
            (config / "tool_sources.catalog.json").write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "semgrep_mcp",
                                "category": "mcp",
                                "capabilities": ["security_scan", "code_quality"],
                                "fallback_strategy": "prefer_skill_or_cli",
                                "replacement_candidates": ["semgrep_security_skill"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (config / "skills.library.json").write_text(
                json.dumps(
                    {
                        "skills": [
                            {
                                "name": "semgrep_security_skill",
                                "purpose": "Security scans compactos",
                                "roles": ["reviewer", "qa"],
                                "capabilities": ["security_scan", "sast", "code_quality"],
                                "description": "Deteccion temprana de vulnerabilidades",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            integrator = AutoToolIntegrator(runtime_dir=runtime, project_root=project)
            suggestions = integrator.suggest_requirements({"security_scan"}, limit=2)
            self.assertTrue(suggestions)
            self.assertEqual(suggestions[0]["name"], "semgrep_security_skill")
            self.assertEqual(suggestions[0]["category"], "skill")
            self.assertEqual(suggestions[0]["replacement_for"], "semgrep_mcp")

    def test_suggest_requirements_keeps_mcp_when_replacement_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            project = root / "project"
            project.mkdir()
            config = project / "config"
            config.mkdir()
            (config / "tool_sources.catalog.json").write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "playwright_mcp",
                                "category": "mcp",
                                "capabilities": ["browser_testing"],
                                "fallback_strategy": "prefer_skill_or_cli",
                                "replacement_candidates": ["playwright_qa_skill"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            integrator = AutoToolIntegrator(runtime_dir=runtime, project_root=project)
            suggestions = integrator.suggest_requirements({"browser_testing"}, limit=1)
            self.assertTrue(suggestions)
            self.assertEqual(suggestions[0]["name"], "playwright_mcp")

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

    def test_guidance_for_task_compacts_targeted_context_for_coordinator(self) -> None:
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
                                "purpose": "Ejecutar browser flows con evidencia",
                                "roles": ["qa", "team_lead"],
                                "capabilities": ["browser_testing", "web_automation"],
                                "keywords": ["browser", "playwright", "e2e"],
                                "mcp_servers": ["playwright_mcp"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (runtime / "mcp_servers.json").write_text(
                json.dumps({"servers": [{"name": "playwright_mcp", "enabled": True}]}),
                encoding="utf-8",
            )

            integrator = AutoToolIntegrator(runtime_dir=runtime, project_root=project)
            guidance = integrator.guidance_for_task(
                role="team_lead",
                description="Coordinar reproduccion browser y revisar impacto",
                required_capabilities=set(),
                preferred_skills=["playwright_qa_skill"],
                lsp_targets=["impact"],
                guidance_mode="coordinator",
            )

            self.assertEqual(guidance["guidance_mode"], "coordinator")
            self.assertIn("playwright_qa_skill", guidance["skills"])
            self.assertIn("Coordina mediante especialistas", guidance["text"])
            self.assertIn("Skills objetivo para delegar", guidance["text"])
            self.assertIn("Objetivos LSP para delegar", guidance["text"])
            self.assertNotIn("Skills aplicables:", guidance["text"])

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

    def test_mcp_doctor_quarantines_package_unavailable_servers(self) -> None:
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
                                "name": "broken_npm_mcp",
                                "command": "npx",
                                "args": ["-y", "@totally/fake-mcp-package-does-not-exist"],
                                "enabled": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            integrator = AutoToolIntegrator(runtime_dir=runtime, project_root=project)
            with patch.object(
                AutoToolIntegrator,
                "_probe_mcp_command",
                return_value=(
                    False,
                    "probe_failed:npm error code E404 npm error 404 Not Found - GET https://registry.npmjs.org/@totally/fake-mcp-package-does-not-exist",
                ),
            ):
                report = integrator.mcp_doctor(timeout=10, quarantine_package_unavailable=True)
            self.assertEqual(report["auto_disabled"], 1)
            self.assertEqual(report["reports"][0]["category"], "package_unavailable")

            payload = json.loads((runtime / "mcp_servers.json").read_text(encoding="utf-8"))
            row = payload["servers"][0]
            self.assertFalse(row["enabled"])
            self.assertEqual(row["health_category"], "package_unavailable")

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

    def test_integrate_from_metadata_persists_effective_inventory_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            project = root / "project"
            project.mkdir()

            integrator = AutoToolIntegrator(runtime_dir=runtime, project_root=project)
            report = integrator.integrate_from_metadata(
                task_id="T-E10",
                metadata={
                    "required_capabilities": ["documentation", "browser_testing"],
                    "skill_targets": ["playwright"],
                    "lsp_targets": ["symbols", "impact"],
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
                        },
                        {
                            "name": "playwright_skill",
                            "category": "skill",
                            "source_type": "builtin",
                            "description": "Skill browser QA",
                            "capabilities": ["browser_testing"],
                        },
                    ],
                },
                internet_allowed=True,
            )

            self.assertTrue(report.success)
            inventory_path = runtime / "effective_tool_inventory.json"
            self.assertTrue(inventory_path.exists())
            payload = json.loads(inventory_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "effective_tool_inventory_v1")
            self.assertEqual(len(payload["entries"]), 1)
            entry = payload["entries"][0]
            self.assertEqual(entry["task_id"], "T-E10")
            self.assertIn("repo_read", entry["required_capabilities"])
            self.assertIn("browser_test", entry["required_capabilities"])
            self.assertEqual(entry["skill_targets"], ["playwright"])
            self.assertEqual(entry["lsp_targets"], ["impact", "references", "symbols"])
            selected_names = {item["name"] for item in entry["selected_tools"]}
            self.assertIn("context7_mcp", selected_names)
            self.assertIn("playwright_skill", selected_names)


if __name__ == "__main__":
    unittest.main()
