import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from aiteam import cli


class AutotuneDoctorTests(unittest.TestCase):
    def test_autotune_doctor_suggests_lower_parallel_for_poor_prod_health(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            now = datetime.now(timezone.utc).isoformat()
            events = [
                {
                    "ts": now,
                    "event_type": "task_execution",
                    "payload": {
                        "task_id": "A-1",
                        "success": True,
                        "latency_ms": 1500,
                        "assignee": "eng-1",
                    },
                },
                {
                    "ts": now,
                    "event_type": "task_execution",
                    "payload": {
                        "task_id": "A-2",
                        "success": False,
                        "latency_ms": 1800,
                        "assignee": "eng-2",
                    },
                },
            ]
            events_path = runtime_dir / "events.jsonl"
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")

            output = io.StringIO()
            with patch.dict("os.environ", {"AITEAM_MAX_PARALLEL_TASKS_PROD": "2"}, clear=False):
                with redirect_stdout(output):
                    cli.cmd_autotune_doctor(runtime_dir=runtime_dir, environment="prod", window_hours=6)

            text = output.getvalue()
            self.assertIn("Autotune doctor", text)
            self.assertIn("AITEAM_MAX_PARALLEL_TASKS_PROD=1", text)
            self.assertIn("AITEAM_PARALLEL_AUTOTUNE=1", text)

    def test_autotune_doctor_handles_missing_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            output = io.StringIO()
            with redirect_stdout(output):
                cli.cmd_autotune_doctor(runtime_dir=runtime_dir, environment="stage", window_hours=6)
            text = output.getvalue()
            self.assertIn("no recent task_execution events", text)
            self.assertIn("Suggested env overrides", text)


if __name__ == "__main__":
    unittest.main()
