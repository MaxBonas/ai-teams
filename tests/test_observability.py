import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from aiteam.observability import EventLogger


class ObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_root = Path(".tmp_test_observability")
        self._tmp_root.mkdir(parents=True, exist_ok=True)

        def _temporary_directory(*args, **kwargs):
            class _TempDir:
                def __init__(self, root: Path):
                    self.name = str(root / f"tmp_{uuid4().hex}")
                    Path(self.name).mkdir(parents=True, exist_ok=True)

                def __enter__(self):
                    return self.name

                def __exit__(self, exc_type, exc, tb):
                    shutil.rmtree(self.name, ignore_errors=True)

            return _TempDir(self._tmp_root)

        self._tempdir_patch = patch("tests.test_observability.tempfile.TemporaryDirectory", _temporary_directory)
        self._tempdir_patch.start()

    def tearDown(self) -> None:
        self._tempdir_patch.stop()
        shutil.rmtree(self._tmp_root, ignore_errors=True)

    def test_summary_includes_kpis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = EventLogger(Path(tmp))
            logger.emit(
                "task_execution",
                {
                    "task_id": "T-1",
                    "provider": "openai",
                    "channel": "subscription",
                    "success": True,
                },
            )
            logger.emit(
                "task_execution",
                {
                    "task_id": "T-2",
                    "provider": "openai",
                    "channel": "api",
                    "success": False,
                },
            )
            logger.emit("quality_gates_opened", {"task_id": "T-1"})
            logger.emit("compliance_violation", {"task_id": "T-3"})

            summary = logger.summary()
            self.assertEqual(summary["task_execution_total"], 2)
            self.assertEqual(summary["task_execution_success"], 1)
            self.assertEqual(summary["task_execution_success_rate"], 50.0)
            self.assertEqual(summary["providers"].get("openai"), 2)
            self.assertEqual(summary["channels"].get("subscription"), 1)
            self.assertEqual(summary["channels"].get("api"), 1)
            self.assertEqual(summary["api_share_percent"], 50.0)
            self.assertEqual(summary["quality_gates_opened"], 1)
            self.assertEqual(summary["compliance_violations"], 1)
            self.assertGreaterEqual(summary["alert_count"], 1)
            self.assertTrue(any("compliance_violations_detected" in a for a in summary["alerts"]))

    def test_summary_detects_high_api_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = EventLogger(Path(tmp))
            for index in range(6):
                logger.emit(
                    "task_execution",
                    {
                        "task_id": f"T-{index}",
                        "provider": "openai",
                        "channel": "api",
                        "success": True,
                    },
                )
            summary = logger.summary()
            self.assertEqual(summary["api_share_percent"], 100.0)
            self.assertTrue(any("high_api_dependency" in a for a in summary["alerts"]))

    def test_summary_aggregates_delegate_economics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = EventLogger(Path(tmp))
            logger.emit(
                "delegate_economics_estimated",
                {
                    "task_id": "CHAT-1",
                    "estimated_lead_tokens_avoided": 2200,
                    "estimated_operator_tokens_used": 700,
                    "estimated_cost_units_saved": 16,
                },
            )

            summary = logger.summary()
            self.assertEqual(summary["delegate_economics_events"], 1)
            self.assertEqual(summary["delegate_estimated_lead_tokens_avoided"], 2200)
            self.assertEqual(summary["delegate_estimated_operator_tokens_used"], 700)
            self.assertEqual(summary["delegate_estimated_net_tokens_saved"], 1500)
            self.assertEqual(summary["delegate_estimated_cost_units_saved"], 16)

    def test_summary_aggregates_tool_rewiring_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = EventLogger(Path(tmp))
            logger.emit(
                "tool_rewiring_applied",
                {
                    "task_id": "CHAT-1::review",
                    "preferred_specialist": "skill_worker",
                },
            )
            logger.emit(
                "tool_rewiring_applied",
                {
                    "task_id": "CHAT-1::qa",
                    "preferred_specialist": "browser_operator",
                },
            )

            summary = logger.summary()
            self.assertEqual(summary["tool_rewiring_events"], 2)
            self.assertEqual(summary["tool_rewiring_by_specialist"].get("skill_worker"), 1)
            self.assertEqual(summary["tool_rewiring_by_specialist"].get("browser_operator"), 1)


if __name__ == "__main__":
    unittest.main()
