import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import api.main as api_main


class APITeamChatTests(unittest.TestCase):
    def test_chat_is_led_by_team_lead_and_returns_delegation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Implement tests and refactor auth flow",
                        "role": "qa",
                        "complexity": "medium",
                        "criticality": "medium",
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload.get("role"), "team_lead")
                self.assertTrue(str(payload.get("lead_task_id", "")).endswith("::lead_intake"))
                self.assertGreaterEqual(len(payload.get("delegated_task_ids", [])), 4)
                phase_task_ids = payload.get("phase_task_ids", {})
                self.assertIn("lead_intake", phase_task_ids)
                self.assertIn("lead_close", phase_task_ids)
                self.assertIn("Lead summary", payload.get("response", ""))
                self.assertIn("Workflow phases", payload.get("response", ""))
                self.assertGreaterEqual(int(payload.get("productivity_score", 0)), 0)
                self.assertLessEqual(int(payload.get("productivity_score", 0)), 100)
                self.assertGreaterEqual(int(payload.get("reasoning_score", 0)), 0)
                self.assertLessEqual(int(payload.get("reasoning_score", 0)), 100)
                self.assertIn(str(payload.get("productivity_status", "")), {"weak", "moderate", "strong"})
                self.assertIn(str(payload.get("execution_mode", "")), {"simulated", "hybrid", "live"})
                self.assertGreaterEqual(int(payload.get("placeholder_outputs", 0)), 0)
                self.assertTrue(isinstance(payload.get("evidence_gate_applied"), bool))
                self.assertTrue(isinstance(payload.get("evidence_gate_failures", []), list))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_persists_lead_and_delegated_tasks_to_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Plan architecture for a new project and then implement core module",
                        "role": "engineer",
                        "complexity": "high",
                        "criticality": "high",
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                tasks_file = workspace / "runtime" / "tasks.json"
                self.assertTrue(tasks_file.exists())
                tasks_text = tasks_file.read_text(encoding="utf-8")
                self.assertIn(payload.get("lead_task_id"), tasks_text)
                for phase_id in payload.get("phase_task_ids", {}).values():
                    self.assertIn(phase_id, tasks_text)
                for delegated_id in payload.get("delegated_task_ids", []):
                    self.assertIn(delegated_id, tasks_text)

                mailbox_file = workspace / "runtime" / "mailbox.jsonl"
                self.assertTrue(mailbox_file.exists())
                mailbox_text = mailbox_file.read_text(encoding="utf-8")
                self.assertIn('"sender": "user"', mailbox_text)
                self.assertIn('"body": "Plan architecture for a new project and then implement core module"', mailbox_text)
                self.assertIn('"sender": "team_lead"', mailbox_text)
                self.assertIn('"recipient": "user"', mailbox_text)
                self.assertIn("Resumen del Team Lead para ti:", mailbox_text)

                events_file = workspace / "runtime" / "events.jsonl"
                self.assertTrue(events_file.exists())
                events_text = events_file.read_text(encoding="utf-8")
                self.assertIn('"event_type": "user_input"', events_text)
                self.assertIn('"event_type": "chat_execution_mode_assessed"', events_text)
                self.assertIn('"event_type": "routing_decision"', events_text)
                event_rows = [json.loads(line) for line in events_text.splitlines() if line.strip()]
                routing_rows = [
                    row
                    for row in event_rows
                    if str(row.get("event_type", "")) == "routing_decision"
                ]
                self.assertTrue(routing_rows)
                self.assertTrue(
                    all(
                        isinstance(row.get("payload", {}), dict)
                        and str(row.get("payload", {}).get("task_id", "")).startswith("CHAT-")
                        for row in routing_rows
                    )
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_state_and_conversation_include_lead_user_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Implement a focused refactor and explain outcomes",
                        "mode": "sprint5",
                        "max_rounds": 4,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                task_id = str(payload.get("task_id", ""))

                state = client.get("/api/aiteam/state?environment=dev")
                self.assertEqual(state.status_code, 200)
                state_payload = state.json()
                lead_summary = state_payload.get("last_lead_user_summary", {})
                self.assertEqual(str(lead_summary.get("task_id", "")), task_id)
                self.assertIn("Resumen del Team Lead para ti", str(lead_summary.get("body", "")))
                last_chat = state_payload.get("last_chat_run", {})
                self.assertIn(str(last_chat.get("execution_mode", "")), {"unknown", "simulated", "hybrid", "live"})
                self.assertGreaterEqual(int(last_chat.get("placeholder_outputs", 0)), 0)
                self.assertTrue(isinstance(last_chat.get("successful_check_count", 0), int))
                self.assertTrue(isinstance(last_chat.get("live_mode_required", False), bool))
                self.assertTrue(isinstance(last_chat.get("live_mode_rejected", False), bool))

                conv = client.get("/api/aiteam/conversations?limit=120")
                self.assertEqual(conv.status_code, 200)
                conv_payload = conv.json()
                items = conv_payload.get("items", [])
                conv_last = conv_payload.get("last_chat_run", {})
                self.assertIn(str(conv_last.get("execution_mode", "")), {"unknown", "simulated", "hybrid", "live"})
                self.assertGreaterEqual(int(conv_last.get("placeholder_outputs", 0)), 0)
                self.assertTrue(isinstance(conv_last.get("successful_check_count", 0), int))
                self.assertTrue(isinstance(conv_last.get("live_mode_required", False), bool))
                matching = [
                    row
                    for row in items
                    if str(row.get("task_id", "")) == task_id
                    and str(row.get("sender", "")).lower() == "team_lead"
                    and str(row.get("recipient", "")).lower() == "user"
                ]
                self.assertGreaterEqual(len(matching), 1)
                self.assertIn("Resumen del Team Lead para ti", str(matching[0].get("body", "")))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_respects_explicit_round_budget_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create plan and execute first slice in bounded rounds",
                        "role": "engineer",
                        "complexity": "medium",
                        "criticality": "medium",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "auto_extend_weak_runs": False,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload.get("chat_mode"), "sprint5")
                self.assertEqual(int(payload.get("round_budget", 0)), 4)
                self.assertGreaterEqual(int(payload.get("rounds_used", 0)), 1)
                self.assertLessEqual(int(payload.get("rounds_used", 0)), 4)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_auto_extends_round_budget_when_run_is_weak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Analyze and propose architecture options for auth module",
                        "mode": "sprint5",
                        "max_rounds": 3,
                        "strict_mode": False,
                        "auto_extend_weak_runs": True,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertGreaterEqual(int(payload.get("round_budget", 0)), 6)
                self.assertGreaterEqual(int(payload.get("auto_extended_rounds", 0)), 3)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_strict_mode_blocks_close_without_minimum_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create a concise implementation proposal for logging improvements",
                        "mode": "sprint5",
                        "max_rounds": 5,
                        "strict_mode": True,
                        "auto_extend_weak_runs": False,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(bool(payload.get("strict_mode")))
                if (
                    int(payload.get("execution_steps", 0)) == 0
                    and int(payload.get("artifact_created", 0)) + int(payload.get("artifact_modified", 0)) == 0
                ):
                    self.assertNotEqual(str(payload.get("state", "")), "completed")
                self.assertIn("Strict mode", str(payload.get("response", "")))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_low_productivity_gate_rejects_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create a conceptual architecture note for telemetry improvements",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "strict_mode": False,
                        "auto_extend_weak_runs": False,
                        "allow_low_productivity_override": False,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertLess(int(payload.get("productivity_score", 100)), int(payload.get("productivity_threshold", 35)))
                state_value = str(payload.get("state", ""))
                self.assertIn(state_value, {"rejected", "failed"})
                if state_value == "rejected":
                    self.assertTrue(bool(payload.get("low_productivity_rejected")))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_low_productivity_override_allows_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create a conceptual architecture note for telemetry improvements",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "strict_mode": False,
                        "auto_extend_weak_runs": False,
                        "allow_low_productivity_override": True,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(bool(payload.get("low_productivity_override")))
                self.assertFalse(bool(payload.get("low_productivity_rejected")))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_evidence_gate_rejects_placeholder_build_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Plan and implement a robust auth module",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "strict_mode": False,
                        "auto_extend_weak_runs": False,
                        "allow_low_productivity_override": True,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(bool(payload.get("evidence_gate_applied")))
                self.assertIn(str(payload.get("state", "")), {"rejected", "failed"})
                failures = payload.get("evidence_gate_failures", [])
                self.assertTrue(any("build" in str(item) for item in failures))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_requires_execution_plan_for_build_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Implement backend endpoint with tests",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "strict_mode": False,
                        "auto_extend_weak_runs": False,
                        "allow_low_productivity_override": True,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(bool(payload.get("evidence_gate_applied")))
                failures = [str(item) for item in payload.get("evidence_gate_failures", [])]
                self.assertTrue(any("build" in item for item in failures))

                events_file = workspace / "runtime" / "events.jsonl"
                self.assertTrue(events_file.exists())
                events_rows = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line.strip()]
                compliance_rows = [
                    row
                    for row in events_rows
                    if str(row.get("event_type", "")) == "compliance_violation"
                ]
                self.assertTrue(compliance_rows)
                reasons = [str((row.get("payload", {}) or {}).get("reason", "")) for row in compliance_rows]
                self.assertIn("missing_execution_plan_required", reasons)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_can_require_live_mode_via_env_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            previous_env = os.environ.get("AITEAM_REQUIRE_LIVE_MODE")
            # Force mock mode so live_mode_rejected can trigger (test checks gate, not real API)
            previous_live_api = os.environ.get("AITEAM_ENABLE_LIVE_API")
            try:
                os.environ["AITEAM_REQUIRE_LIVE_MODE"] = "1"
                os.environ["AITEAM_ENABLE_LIVE_API"] = "0"
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Implement endpoint and validate behavior",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "strict_mode": False,
                        "allow_low_productivity_override": True,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(bool(payload.get("live_mode_required")))
                self.assertTrue(bool(payload.get("live_mode_rejected")))
                self.assertIn(str(payload.get("state", "")), {"rejected", "failed"})
            finally:
                if previous_env is None:
                    os.environ.pop("AITEAM_REQUIRE_LIVE_MODE", None)
                else:
                    os.environ["AITEAM_REQUIRE_LIVE_MODE"] = previous_env
                if previous_live_api is None:
                    os.environ.pop("AITEAM_ENABLE_LIVE_API", None)
                else:
                    os.environ["AITEAM_ENABLE_LIVE_API"] = previous_live_api
                api_main.set_current_workspace(previous_workspace)

    def test_operator_timeline_endpoint_returns_key_events_for_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                client_task_id = "CHAT-1122AABB"
                chat = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create an original arcade game and implement first playable version",
                        "mode": "sprint5",
                        "max_rounds": 5,
                        "client_task_id": client_task_id,
                    },
                )
                self.assertEqual(chat.status_code, 200)

                timeline = client.get(
                    f"/api/aiteam/operator/timeline?task_id={client_task_id}&limit=60&key_only=true"
                )
                self.assertEqual(timeline.status_code, 200)
                payload = timeline.json()
                self.assertEqual(str(payload.get("selected_task_id", "")), client_task_id)
                self.assertGreaterEqual(int(payload.get("total", 0)), 1)
                self.assertTrue(isinstance(payload.get("items", []), list))
                self.assertTrue(isinstance(payload.get("available_runs", []), list))
                progress = payload.get("progress", {})
                self.assertEqual(str(progress.get("task_id", "")), client_task_id)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_continue_from_explicit_chat_root_keeps_continuity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                first = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create and design an original game",
                        "mode": "sprint5",
                        "max_rounds": 5,
                    },
                )
                self.assertEqual(first.status_code, 200)
                first_payload = first.json()
                first_root = str(first_payload.get("task_id", ""))
                self.assertTrue(first_root.startswith("CHAT-"))

                second = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": f"Continue from {first_root}.",
                        "mode": "sprint5",
                        "max_rounds": 5,
                    },
                )
                self.assertEqual(second.status_code, 200)
                second_payload = second.json()
                self.assertTrue(bool(second_payload.get("continuation_requested")))
                self.assertEqual(str(second_payload.get("continuation_of", "")), first_root)
                self.assertIn(f"continuation_of={first_root}", str(second_payload.get("response", "")))

                state = client.get("/api/aiteam/state?environment=dev").json()
                last_run = state.get("last_chat_run", {})
                self.assertTrue(bool(last_run.get("continuation_requested")))
                self.assertEqual(str(last_run.get("continuation_of", "")), first_root)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_progress_endpoint_tracks_client_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                client_task_id = "CHAT-ABCDEF12"

                initial_progress = client.get(f"/api/aiteam/chat/progress/{client_task_id}")
                self.assertEqual(initial_progress.status_code, 200)
                initial_payload = initial_progress.json()
                self.assertFalse(bool(initial_payload.get("exists")))

                chat_response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Build a small game prototype with real files",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "client_task_id": client_task_id,
                    },
                )
                self.assertEqual(chat_response.status_code, 200)
                chat_payload = chat_response.json()
                self.assertEqual(str(chat_payload.get("task_id", "")), client_task_id)

                progress = client.get(f"/api/aiteam/chat/progress/{client_task_id}")
                self.assertEqual(progress.status_code, 200)
                payload = progress.json()
                self.assertTrue(bool(payload.get("exists")))
                self.assertEqual(str(payload.get("task_id", "")), client_task_id)
                self.assertEqual(int(payload.get("round_budget", 0)), 4)
                self.assertGreaterEqual(int(payload.get("rounds_used", 0)), 1)
                self.assertIn("lead_intake", payload.get("phase_states", {}))
                self.assertGreaterEqual(int(payload.get("execution_attempts", 0)), 1)
                self.assertGreaterEqual(int(payload.get("execution_steps_success", 0)), 0)
                self.assertTrue(isinstance(payload.get("successful_checks", []), list))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_game_request_creates_artifacts_in_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create an original arcade game and start implementation now",
                        "mode": "sprint5",
                        "max_rounds": 4,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()

                self.assertGreaterEqual(int(payload.get("artifact_created", 0)), 1)
                artifact_files = payload.get("artifact_files", [])
                self.assertIn("index.html", artifact_files)
                self.assertIn("game.js", artifact_files)

                self.assertTrue((workspace / "index.html").exists())
                self.assertTrue((workspace / "styles.css").exists())
                self.assertTrue((workspace / "game.js").exists())
                self.assertTrue((workspace / "README.md").exists())

                progress_path = workspace / ".aiteam_game_progress.json"
                self.assertTrue(progress_path.exists())
                progress_payload = json.loads(progress_path.read_text(encoding="utf-8"))
                self.assertGreaterEqual(int(progress_payload.get("iteration", 0)), 1)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_game_followup_without_game_keyword_does_not_rebootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                first = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create an arcade game with an original style",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "strict_mode": False,
                    },
                )
                self.assertEqual(first.status_code, 200)
                first_payload = first.json()
                first_root = str(first_payload.get("task_id", ""))

                second = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": f"Continue from {first_root}. Start the next highest-impact slice and improve visual design.",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "strict_mode": False,
                        "allow_low_productivity_override": True,
                    },
                )
                self.assertEqual(second.status_code, 200)
                second_payload = second.json()
                self.assertEqual(int(second_payload.get("artifact_created", 0)), 0)
                self.assertEqual(int(second_payload.get("artifact_modified", 0)), 0)
                self.assertTrue(bool(second_payload.get("evidence_gate_applied")))
                failures = second_payload.get("evidence_gate_failures", [])
                self.assertTrue(any("no_followup_artifact_delta" in str(item) for item in failures))
                progress_path = workspace / ".aiteam_game_progress.json"
                progress_payload = json.loads(progress_path.read_text(encoding="utf-8"))
                self.assertEqual(int(progress_payload.get("iteration", 0)), 1)
            finally:
                api_main.set_current_workspace(previous_workspace)


if __name__ == "__main__":
    unittest.main()
