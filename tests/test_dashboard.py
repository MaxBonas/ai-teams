import tempfile
import unittest
from pathlib import Path

from aiteam.dashboard import build_dashboard_payload, render_dashboard_html
from aiteam.types import Complexity, Criticality, Role, TaskState, WorkTask


class DashboardTests(unittest.TestCase):
    def test_payload_and_html_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            events = runtime_dir / "events.jsonl"
            events.write_text(
                "\n".join(
                    [
                        '{"ts":"2026-01-01T00:00:00+00:00","event_type":"task_execution","payload":{"success":true,"assignee":"eng-1","latency_ms":320,"execution_round":1}}',
                        '{"ts":"2026-01-01T00:00:01+00:00","event_type":"task_execution","payload":{"success":true,"assignee":"eng-1","latency_ms":1200,"execution_round":2}}',
                        '{"ts":"2026-01-01T00:00:02+00:00","event_type":"gate_iteration","payload":{"task_id":"T-1","iteration":1,"execution_round":2,"execution_sub_iteration":1}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (runtime_dir / "provider_ops.json").write_text(
                '{"summary":{"operational_count":2,"degraded_count":1},"alerts":["Senior cloud degraded: claude_pro_cli"],"providers":[{"adapter_name":"openai_pro_cli","tier":"senior_cloud","provider":"openai","operational":true,"doctor_details":"ok","smoke_details":"smoke_ok"},{"adapter_name":"claude_pro_cli","tier":"senior_cloud","provider":"anthropic","operational":false,"doctor_details":"ok","smoke_details":"credit low"}]}',
                encoding="utf-8",
            )

            tasks = [
                WorkTask(
                    task_id="T-1",
                    title="A",
                    description="",
                    role=Role.ENGINEER,
                    complexity=Complexity.MEDIUM,
                    criticality=Criticality.MEDIUM,
                    state=TaskState.COMPLETED,
                    assignee="eng-1",
                    metadata={
                        "execution_round": 2,
                        "execution_sub_iteration": 1,
                        "gate_iteration": 1,
                    },
                )
            ]
            summary = {
                "task_execution_success_rate": 100.0,
                "api_share_percent": 0.0,
                "channels": {"subscription": 1},
                "providers": {"openai": 1},
                "compliance_violations": 0,
                "alerts": [],
            }
            pilot = {"pro_share_percent": 100.0}
            payload = build_dashboard_payload(
                runtime_dir=runtime_dir,
                tasks=tasks,
                summary=summary,
                pilot_metrics=pilot,
                budget_snapshot={"daily_api_spend_usd": 0, "daily_api_budget_usd": 10},
                memory_counts={"eng-1": 4},
            )

            html_doc = render_dashboard_html(payload)
            self.assertIn("AI Team Operations Dashboard", html_doc)
            self.assertIn("T-1", html_doc)
            self.assertIn("task_execution", html_doc)
            self.assertIn("Flow Timeline", html_doc)
            self.assertIn("Flow Summary", html_doc)
            self.assertIn("Provider Ops Summary", html_doc)
            self.assertIn("Provider Alerts", html_doc)
            self.assertIn("Provider Ops", html_doc)
            self.assertIn("r2 / s1 / g1", html_doc)
            self.assertIn("gate_iterations", html_doc)
            self.assertIn("operational_count", html_doc)
            self.assertIn("openai_pro_cli", html_doc)
            self.assertIn("Senior cloud degraded", html_doc)
            self.assertIn("Agent Latency", html_doc)
            self.assertIn("eng-1", html_doc)
            self.assertIn("200-499", html_doc)
            self.assertIn("Latency Trend (p95 by round)", html_doc)
            self.assertIn("r1:p95", html_doc)


if __name__ == "__main__":
    unittest.main()
