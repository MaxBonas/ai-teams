import unittest

from aiteam.pilot import PilotThresholds, compute_pilot_metrics, evaluate_pilot
from aiteam.types import Complexity, Criticality, Role, TaskState, WorkTask


class PilotTests(unittest.TestCase):
    def test_compute_pilot_metrics(self) -> None:
        tasks = [
            WorkTask(
                task_id="T-1",
                title="Main",
                description="",
                role=Role.ENGINEER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                state=TaskState.COMPLETED,
            ),
            WorkTask(
                task_id="T-2",
                title="Main2",
                description="",
                role=Role.QA,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                state=TaskState.FAILED,
            ),
            WorkTask(
                task_id="T-1::review",
                title="Gate",
                description="",
                role=Role.REVIEWER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                state=TaskState.COMPLETED,
                metadata={"is_gate": True},
            ),
        ]
        event_summary = {
            "task_execution_total": 4,
            "channels": {"subscription": 3, "api": 1},
            "compliance_violations": 0,
        }

        metrics = compute_pilot_metrics(tasks, event_summary)
        self.assertEqual(metrics["parent_total"], 2)
        self.assertEqual(metrics["parent_completed"], 1)
        self.assertEqual(metrics["task_success_rate"], 50.0)
        self.assertEqual(metrics["gate_total"], 1)
        self.assertEqual(metrics["gate_pass_rate"], 100.0)
        self.assertEqual(metrics["pro_share_percent"], 75.0)
        self.assertEqual(metrics["api_fallback_rate_percent"], 25.0)

    def test_evaluate_pilot_detects_threshold_failures(self) -> None:
        result = evaluate_pilot(
            {
                "task_success_rate": 80.0,
                "gate_pass_rate": 90.0,
                "pro_share_percent": 55.0,
                "compliance_violations": 1,
            },
            PilotThresholds(),
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("task_success_rate" in message for message in result.messages))
        self.assertTrue(any("pro_share_percent" in message for message in result.messages))
        self.assertTrue(any("compliance_violations" in message for message in result.messages))


if __name__ == "__main__":
    unittest.main()
