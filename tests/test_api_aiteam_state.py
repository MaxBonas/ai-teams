import os
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import api.main as api_main
from fastapi.testclient import TestClient
from aiteam.context_curator import ContextCuratorStore


class APIAIStateNotebookLMTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_tempdir = tempfile.tempdir
        self._previous_temporary_directory = tempfile.TemporaryDirectory
        self._local_temp_root = Path.cwd() / ".tmp_test_api_aiteam_state"
        self._local_temp_root.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(self._local_temp_root)

        class _WorkspaceTemporaryDirectory:
            def __init__(
                inner_self,
                suffix: str | None = None,
                prefix: str | None = None,
                dir: str | Path | None = None,
                ignore_cleanup_errors: bool = False,
            ) -> None:
                inner_self._ignore_cleanup_errors = ignore_cleanup_errors
                inner_self._root = Path(dir) if dir else self._local_temp_root
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

        tempfile.TemporaryDirectory = _WorkspaceTemporaryDirectory

    def tearDown(self) -> None:
        tempfile.tempdir = self._previous_tempdir
        tempfile.TemporaryDirectory = self._previous_temporary_directory

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

    def test_state_and_conversations_include_delegate_economics_from_workflow_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            now_iso = datetime.now(timezone.utc).isoformat()
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-03-31T10:00:00+00:00",
                        "event_type": "chat_plan_created",
                        "payload": {
                            "task_id": "CHAT-econ01",
                            "chat_mode": "sprint5",
                            "round_budget": 6,
                            "phase_count": 4,
                            "delegated_count": 2,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (runtime_dir / "workflow_state.json").write_text(
                json.dumps(
                    {
                        "CHAT-econ01": {
                            "phase_outputs": {
                                "build": "resultado extenso de build con evidencia detallada y varias lineas de contexto crudo" * 8,
                                "review": "resultado de review con findings y contexto repetido" * 6,
                            },
                            "phase_evidence_plan": {
                                "build": {
                                    "delegate_intents": ["delegate_test_run"],
                                    "wait_policy": "quorum",
                                    "delegate_budget": 4,
                                }
                            },
                            "delegate_batches": [
                                {
                                    "intent": "delegate_browser_repro",
                                    "wait_policy": "quorum",
                                    "economics": {
                                        "economics_version": "delegate_economics_v1",
                                        "estimated": True,
                                        "specialist_tasks": 3,
                                        "estimated_lead_tokens_avoided": 2600,
                                        "estimated_operator_tokens_used": 820,
                                        "estimated_net_tokens_saved": 1780,
                                        "estimated_cost_units_saved": 18,
                                        "specialist_breakdown": {
                                            "browser_operator": {"count": 1, "completed": 1, "failed": 0}
                                        },
                                    },
                                }
                            ],
                            "delegate_economics_summary": {
                                "economics_version": "delegate_economics_v1",
                                "estimated": True,
                                "batch_count": 1,
                                "specialist_task_count": 3,
                                "quorum_met_ratio": 1.0,
                                "estimated_net_tokens_saved": 1780,
                            },
                            "context_pressure": {
                                "score": 5,
                                "level": "medium",
                                "signals": [
                                    "continuation_requested",
                                    "delegate_batches_accumulated",
                                    "phase_context_accumulated",
                                ],
                                "recommend_context_curator": True,
                            },
                            "context_curator_recommended": True,
                            "project_context_summary": "decisions: login flow auditado",
                            "chat_context_summary": "working_set: build: revisar auth selector",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            curator_store = ContextCuratorStore(runtime_dir)
            project_ctx = curator_store.load_project_context(str(workspace.resolve()))
            project_ctx["durable_facts"] = [{"text": "login flow auditado", "confidence": 0.7}]
            project_ctx["decisions"] = [{"text": "priorizar browser evidence", "confidence": 0.8}]
            project_ctx["updated_at"] = now_iso
            curator_store._write_project_context(str(workspace.resolve()), project_ctx)
            chat_ctx = curator_store.load_chat_context("CHAT-econ01", project_key=str(workspace.resolve()))
            chat_ctx["working_set"] = [{"text": "build: revisar auth selector", "confidence": 0.7}]
            chat_ctx["durable_facts"] = [{"text": "auth.py es hotspot", "confidence": 0.7}]
            chat_ctx["decisions"] = [{"text": "auditar login primero", "confidence": 0.8}]
            chat_ctx["open_questions"] = [{"text": "confirmar selector", "confidence": 0.6}]
            chat_ctx["invalidations"] = [{"text": "replan_partial", "confidence": 0.8}]
            chat_ctx["next_actions"] = [{"text": "delegate:delegate_browser_repro", "confidence": 0.6}]
            chat_ctx["source_task_ids"] = ["CHAT-econ01::lead_intake"]
            chat_ctx["updated_at"] = now_iso
            curator_store._write_chat_context("CHAT-econ01", chat_ctx)
            (runtime_dir / "tasks.json").write_text(
                json.dumps(
                    [
                        {
                            "task_id": "CHAT-econ01::build",
                            "state": "completed",
                            "metadata": {
                                "tool_rewiring_active": True,
                                "tool_rewiring_preferred_specialist": "skill_worker",
                                "tool_rewiring_replacement_for": ["semgrep_mcp"],
                                "specialist_reports": [
                                    {
                                        "specialist": "browser_operator",
                                        "summary": "Se reprodujo el flujo UI con evidencia compacta.",
                                        "recommendation": "revisar selector principal",
                                        "provider": "openai",
                                        "model": "gpt-4o-mini",
                                        "validation_status": "valid",
                                        "validation_errors": [],
                                        "report_version": "specialist_report_v1",
                                    }
                                ]
                            },
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                state_payload = client.get("/api/aiteam/state?environment=dev").json()
                last_chat = state_payload.get("last_chat_run", {})
                self.assertIn("build", last_chat.get("phase_evidence_plan", {}))
                self.assertEqual(
                    int((last_chat.get("delegate_economics", {}) or {}).get("estimated_net_tokens_saved", 0)),
                    1780,
                )
                self.assertEqual(
                    float((last_chat.get("delegate_economics", {}) or {}).get("quorum_met_ratio", 0.0)),
                    1.0,
                )
                self.assertTrue(isinstance(last_chat.get("specialist_reports", []), list))
                self.assertEqual(
                    str(((last_chat.get("specialist_reports", []) or [])[0] or {}).get("specialist", "")),
                    "browser_operator",
                )
                self.assertEqual(
                    int((last_chat.get("specialist_report_summary", {}) or {}).get("valid_count", 0)),
                    1,
                )
                self.assertEqual(
                    int((last_chat.get("tool_rewiring_summary", {}) or {}).get("count", 0)),
                    1,
                )
                self.assertEqual(
                    str((last_chat.get("context_pressure", {}) or {}).get("level", "")),
                    "medium",
                )
                curator_summary = last_chat.get("context_curator_summary", {}) or {}
                self.assertEqual(str(curator_summary.get("freshness_status", "")), "fresh")
                self.assertEqual(int(curator_summary.get("invalidation_count", 0)), 1)
                self.assertEqual(
                    int((curator_summary.get("chat_layer_counts", {}) or {}).get("working_set", 0)),
                    1,
                )
                self.assertGreater(
                    int(curator_summary.get("estimated_context_chars_saved", 0)),
                    0,
                )
                self.assertGreater(
                    int(curator_summary.get("estimated_context_tokens_saved", 0)),
                    0,
                )
                rewiring_by_specialist = (
                    (last_chat.get("tool_rewiring_summary", {}) or {}).get("by_specialist", {}) or {}
                )
                self.assertEqual(
                    int(rewiring_by_specialist.get("skill_worker", 0)),
                    1,
                )

                conv_payload = client.get("/api/aiteam/conversations?limit=10").json()
                conv_last = conv_payload.get("last_chat_run", {})
                self.assertEqual(
                    int((conv_last.get("delegate_economics", {}) or {}).get("estimated_net_tokens_saved", 0)),
                    1780,
                )
                self.assertEqual(
                    int((conv_last.get("specialist_report_summary", {}) or {}).get("count", 0)),
                    1,
                )
                self.assertEqual(
                    int((conv_last.get("tool_rewiring_summary", {}) or {}).get("count", 0)),
                    1,
                )
                self.assertEqual(
                    str((conv_last.get("context_pressure", {}) or {}).get("level", "")),
                    "medium",
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_aiteam_state_includes_mcp_overview_and_opencode_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            config_dir = workspace / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "tool_sources.catalog.json").write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "memory",
                                "category": "mcp",
                                "source_type": "npm",
                                "source": "@modelcontextprotocol/server-memory",
                                "enabled": False,
                                "fallback_strategy": "prefer_skill_or_cli",
                                "replacement_candidates": ["context7_research_skill"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (runtime_dir / "mcp_servers.json").write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "memory",
                                "command": "npx",
                                "args": ["-y", "@modelcontextprotocol/server-memory"],
                                "enabled": True,
                                "transport": "stdio",
                                "source_type": "npm",
                                "source": "@modelcontextprotocol/server-memory",
                                "capabilities": ["external_mcp"],
                                "role_targets": [],
                                "health_status": "healthy",
                                "bootstrap_source": "opencode_mcp_list",
                            },
                            {
                                "name": "filesystem",
                                "command": "npx",
                                "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                                "enabled": False,
                                "transport": "stdio",
                                "source_type": "npm",
                                "source": "@modelcontextprotocol/server-filesystem",
                                "capabilities": ["external_mcp", "repo_read"],
                                "role_targets": ["scout"],
                                "health_status": "unhealthy",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (runtime_dir / "mcp_events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-03-31T12:00:00+00:00",
                        "event": "opencode_bootstrap_imported",
                        "server": "opencode",
                        "count": 1,
                        "path": "C:\\Users\\Max\\AppData\\Local\\OpenCode\\mcp_list.txt",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.get("/api/aiteam/state?environment=dev")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                overview = payload.get("mcp_overview", {})
                self.assertGreaterEqual(int(overview.get("total_servers", 0)), 2)
                self.assertGreaterEqual(int(overview.get("enabled_servers", 0)), 1)
                self.assertGreaterEqual(int(overview.get("healthy_servers", 0)), 1)
                self.assertGreaterEqual(int(overview.get("bootstrapped_servers", 0)), 1)
                self.assertTrue(isinstance(overview.get("machine_profile", {}), dict))
                self.assertTrue(isinstance(overview.get("portability_counts", {}), dict))
                health_categories = overview.get("health_categories", {})
                self.assertGreaterEqual(int((health_categories or {}).get("healthy", 0)), 1)
                self.assertGreaterEqual(int((health_categories or {}).get("unknown", 0)), 1)
                self.assertIn("usable_now", dict(overview.get("health_recommendations", {}) or {}))
                server_names = {
                    str(item.get("name", "") or "")
                    for item in list(overview.get("servers", []) or [])
                    if isinstance(item, dict)
                }
                self.assertIn("memory", server_names)
                self.assertTrue(bool((overview.get("opencode", {}) or {}).get("bootstrapped_servers")))
                server_rows = {
                    str(item.get("name", "") or ""): item
                    for item in list(overview.get("servers", []) or [])
                    if isinstance(item, dict)
                }
                self.assertEqual(
                    str((server_rows.get("memory", {}) or {}).get("portability_status", "")),
                    "portable",
                )
                self.assertEqual(
                    str((server_rows.get("memory", {}) or {}).get("catalog_fallback_strategy", "")),
                    "prefer_skill_or_cli",
                )
                self.assertIn(
                    "context7_research_skill",
                    list((server_rows.get("memory", {}) or {}).get("catalog_replacement_candidates", []) or []),
                )
                self.assertGreaterEqual(
                    int((overview.get("fallback_counts", {}) or {}).get("prefer_skill_or_cli", 0)),
                    1,
                )
                self.assertGreaterEqual(
                    int((overview.get("replacement_counts", {}) or {}).get("context7_research_skill", 0)),
                    1,
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_mcp_refresh_health_endpoint_returns_report_and_overview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "mcp_servers.json").write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "filesystem",
                                "command": "npx",
                                "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                                "enabled": True,
                                "transport": "stdio",
                                "source_type": "npm",
                                "source": "@modelcontextprotocol/server-filesystem",
                                "capabilities": ["external_mcp", "repo_read"],
                                "role_targets": ["scout"],
                                "health_status": "unhealthy",
                                "health_reason": "probe_failed:Error accessing directory",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch("api.routers.aiteam.AutoToolIntegrator.mcp_doctor", return_value={"healthy": 0, "total": 1, "reports": []}):
                    response = client.post("/api/aiteam/mcp/refresh-health")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(bool(payload.get("refreshed")))
                self.assertEqual(int((payload.get("report", {}) or {}).get("total", 0)), 1)
                overview = payload.get("overview", {})
                self.assertEqual(int((overview.get("health_categories", {}) or {}).get("path_missing", 0)), 1)
                self.assertEqual(
                    int((overview.get("health_recommendations", {}) or {}).get("repair_path_or_workspace", 0)),
                    1,
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_mcp_bootstrap_opencode_endpoint_imports_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=False):
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            mcp_list_path = workspace / "mcp_list.txt"
            mcp_list_path.write_text(
                "\n".join(
                    [
                        "•  ✓ memory connected",
                        "    npx -y @modelcontextprotocol/server-memory",
                        "",
                        "•  ✓ puppeteer connected",
                        "    npx -y @modelcontextprotocol/server-puppeteer",
                    ]
                ),
                encoding="utf-8",
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.dict(os.environ, {"AITEAM_OPENCODE_MCP_LIST_PATH": str(mcp_list_path)}, clear=False):
                    response = client.post("/api/aiteam/mcp/bootstrap-opencode")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(int(payload.get("imported", 0)), 2)
                self.assertTrue(bool((payload.get("opencode", {}) or {}).get("available")))
                server_names = {
                    str(item.get("name", "") or "")
                    for item in list(payload.get("servers", []) or [])
                    if isinstance(item, dict)
                }
                self.assertIn("memory", server_names)
                self.assertIn("puppeteer", server_names)
            finally:
                api_main.set_current_workspace(previous_workspace)


if __name__ == "__main__":
    unittest.main()
