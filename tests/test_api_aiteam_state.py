import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import api.main as api_main
from fastapi.testclient import TestClient


class APIAIStateNotebookLMTests(unittest.TestCase):
    def test_notebooklm_status_reports_manual_export_when_no_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            runtime_dir = Path(tmp) / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)

            status = api_main._detect_notebooklm_status(runtime_dir, api_main.PROJECT_ROOT)

            self.assertFalse(bool(status.get("connected")))
            self.assertEqual(status.get("mode"), "manual_export")

    def test_notebooklm_status_reports_enabled_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            runtime_dir = Path(tmp) / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            adapters_path = runtime_dir / "adapters.json"
            adapters_path.write_text(
                '{"external_adapters":[{"name":"notebooklm_sync","provider":"custom","enabled":true}]}',
                encoding="utf-8",
            )

            status = api_main._detect_notebooklm_status(runtime_dir, api_main.PROJECT_ROOT)

            self.assertFalse(bool(status.get("connected")))
            self.assertEqual(status.get("mode"), "adapter")

    def test_notebooklm_status_reports_configured_disabled_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            runtime_dir = Path(tmp) / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            adapters_path = runtime_dir / "adapters.json"
            adapters_path.write_text(
                '{"external_adapters":[{"name":"notebooklm_sync","provider":"custom","enabled":false}]}',
                encoding="utf-8",
            )

            status = api_main._detect_notebooklm_status(runtime_dir, api_main.PROJECT_ROOT)

            self.assertFalse(bool(status.get("connected")))
            self.assertEqual(status.get("mode"), "configured_disabled")

    def test_notebooklm_status_prefers_last_sync_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            runtime_dir = Path(tmp) / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            sync_status_path = runtime_dir / "notebooklm_sync_status.json"
            sync_status_path.write_text(
                '{"ts":"2026-02-21T00:00:00+00:00","mode":"command","success":true,"details":"ok"}',
                encoding="utf-8",
            )

            status = api_main._detect_notebooklm_status(runtime_dir, api_main.PROJECT_ROOT)

            self.assertTrue(bool(status.get("connected")))
            self.assertEqual(status.get("mode"), "command")

    def test_notebooklm_sync_endpoint_writes_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/notebooklm/sync",
                    json={
                        "title": "Endpoint sync test",
                        "content": "hello notebook",
                        "dry_run": True,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload.get("mode"), "dry_run")
                self.assertTrue((workspace / "runtime" / "notebooklm_sync_status.json").exists())
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_aiteam_conversations_endpoint_returns_mailbox_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            mailbox_path = runtime_dir / "mailbox.jsonl"
            mailbox_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": "2026-02-21T01:00:00+00:00",
                                "sender": "team_lead",
                                "recipient": "engineer",
                                "subject": "Peer input",
                                "body": "Please implement feature X",
                                "task_id": "CHAT-abc::build",
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": "2026-02-21T01:01:00+00:00",
                                "sender": "engineer",
                                "recipient": "team_lead",
                                "subject": "Task completed",
                                "body": "Done",
                                "task_id": "CHAT-abc::build",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.get("/api/aiteam/conversations?limit=10")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload.get("total"), 2)
                self.assertEqual(len(payload.get("items", [])), 2)
                self.assertEqual(payload.get("items", [])[0].get("sender"), "engineer")
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_aiteam_logs_endpoint_returns_events_and_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)

            events_path = runtime_dir / "events.jsonl"
            events_path.write_text(
                json.dumps(
                    {
                        "ts": "2026-02-21T01:10:00+00:00",
                        "event_type": "execution_step",
                        "payload": {
                            "task_id": "T-100",
                            "step_type": "cmd",
                            "command": "python --version",
                            "exit_code": 0,
                            "success": True,
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "ts": "2026-02-21T01:11:00+00:00",
                        "event_type": "task_execution",
                        "payload": {
                            "task_id": "T-100",
                            "role": "engineer",
                            "assignee": "eng-1",
                            "success": True,
                            "latency_ms": 120,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            tasks_path = runtime_dir / "tasks.json"
            tasks_path.write_text(
                json.dumps(
                    [
                        {
                            "task_id": "T-100",
                            "role": "engineer",
                            "state": "completed",
                            "metadata": {
                                "result": "Implemented and validated",
                            },
                        }
                    ]
                ),
                encoding="utf-8",
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.get("/api/aiteam/logs?limit=20")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertGreaterEqual(len(payload.get("event_logs", [])), 1)
                self.assertEqual(len(payload.get("task_outputs", [])), 1)
                self.assertEqual(payload.get("task_outputs", [])[0].get("task_id"), "T-100")
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_api_key_protects_aiteam_state_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"AITEAM_API_KEY": "test-key"}, clear=False):
            workspace = Path(tmp)
            (workspace / "runtime").mkdir(parents=True, exist_ok=True)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                blocked = client.get("/api/aiteam/state?environment=dev")
                self.assertEqual(blocked.status_code, 401)

                allowed = client.get(
                    "/api/aiteam/state?environment=dev",
                    headers={"x-api-key": "test-key"},
                )
                self.assertEqual(allowed.status_code, 200)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_api_key_accepts_bearer_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"AITEAM_API_KEY": "token-1"}, clear=False):
            workspace = Path(tmp)
            (workspace / "runtime").mkdir(parents=True, exist_ok=True)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.get(
                    "/api/aiteam/conversations?limit=2",
                    headers={"Authorization": "Bearer token-1"},
                )
                self.assertEqual(response.status_code, 200)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_workspace_header_isolates_project_runtime_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_a = root / "proj_a"
            workspace_b = root / "proj_b"
            (workspace_a / "runtime").mkdir(parents=True, exist_ok=True)
            (workspace_b / "runtime").mkdir(parents=True, exist_ok=True)

            (workspace_a / "runtime" / "mailbox.jsonl").write_text(
                '{"timestamp":"2026-02-21T01:00:00+00:00","sender":"agent-a","recipient":"team_lead","subject":"A","body":"only-a","task_id":"A-1"}\n',
                encoding="utf-8",
            )
            (workspace_b / "runtime" / "mailbox.jsonl").write_text(
                '{"timestamp":"2026-02-21T01:00:00+00:00","sender":"agent-b","recipient":"team_lead","subject":"B","body":"only-b","task_id":"B-1"}\n',
                encoding="utf-8",
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace_a)
                client = TestClient(api_main.app)

                a_payload = client.get("/api/aiteam/conversations?limit=5").json()
                self.assertEqual(a_payload.get("items", [])[0].get("sender"), "agent-a")

                b_payload = client.get(
                    "/api/aiteam/conversations?limit=5",
                    headers={"x-workspace-path": str(workspace_b)},
                ).json()
                self.assertEqual(b_payload.get("items", [])[0].get("sender"), "agent-b")
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_create_project_endpoint_creates_folder_under_antigravity_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_projects_root = Path(tmp)
            fake_project_root = fake_projects_root / "Ai_Teams"
            fake_project_root.mkdir(parents=True, exist_ok=True)

            with patch.object(api_main, "PROJECT_ROOT", fake_project_root):
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/projects/new",
                    json={"name": "My Fresh Project"},
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(bool(payload.get("success")))
                created = Path(str(payload.get("workspace", "")))
                self.assertTrue(created.exists())
                self.assertEqual(created.parent.resolve(), fake_projects_root.resolve())

    def test_conversations_and_outputs_include_user_input_from_lead_intake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)

            (runtime_dir / "tasks.json").write_text(
                json.dumps(
                    [
                        {
                            "task_id": "CHAT-demo::lead_intake",
                            "title": "Lead intake and project framing",
                            "description": "Eres Team Lead senior.\nSolicitud original:\ncrea un videojuego 2D\nEntrega: objetivos",
                            "role": "team_lead",
                            "complexity": "medium",
                            "criticality": "medium",
                            "dependencies": [],
                            "state": "completed",
                            "assignee": "lead-1",
                            "metadata": {"phase": "lead_intake"},
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-02-21T02:00:00+00:00",
                        "event_type": "task_started",
                        "payload": {
                            "task_id": "CHAT-demo::lead_intake",
                            "role": "team_lead",
                            "assignee": "lead-1",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (runtime_dir / "mailbox.jsonl").write_text("", encoding="utf-8")

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)

                conv = client.get("/api/aiteam/conversations?limit=20").json()
                user_items = [item for item in conv.get("items", []) if item.get("sender") == "user"]
                self.assertTrue(user_items)
                self.assertEqual(user_items[0].get("body"), "crea un videojuego 2D")

                logs = client.get("/api/aiteam/logs?limit=20").json()
                user_log_rows = [item for item in logs.get("event_logs", []) if item.get("event_type") == "user_input"]
                user_outputs = [item for item in logs.get("task_outputs", []) if item.get("role") == "user"]
                self.assertTrue(user_log_rows)
                self.assertTrue(user_outputs)
                self.assertIn("videojuego", str(user_outputs[0].get("output", "")))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_aiteam_state_includes_project_continuity_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "tasks.json").write_text(
                json.dumps(
                    [
                        {
                            "task_id": "CHAT-ctx001::lead_intake",
                            "title": "Lead intake and project framing",
                            "description": "Solicitud original:\ncrear prototipo 2D\nEntrega: objetivos",
                            "role": "team_lead",
                            "complexity": "medium",
                            "criticality": "medium",
                            "dependencies": [],
                            "state": "completed",
                            "assignee": "lead-1",
                            "metadata": {"phase": "lead_intake"},
                        },
                        {
                            "task_id": "CHAT-ctx001::lead_close",
                            "title": "Lead synthesis and response",
                            "description": "close",
                            "role": "team_lead",
                            "complexity": "medium",
                            "criticality": "medium",
                            "dependencies": ["CHAT-ctx001::lead_intake"],
                            "state": "completed",
                            "assignee": "lead-1",
                            "metadata": {"result": "Se definio plan de prototipo"},
                        },
                    ]
                ),
                encoding="utf-8",
            )
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.get("/api/aiteam/state?environment=dev")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                continuity = str(payload.get("project_continuity", ""))
                self.assertIn("CHAT-ctx001", continuity)
                self.assertIn("crear prototipo 2D", continuity)
            finally:
                api_main.set_current_workspace(previous_workspace)


if __name__ == "__main__":
    unittest.main()
