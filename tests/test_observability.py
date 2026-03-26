import tempfile
import unittest
from pathlib import Path

from aiteam.observability import EventLogger


class ObservabilityTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
