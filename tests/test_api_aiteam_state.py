import os
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
import api.main as api_main
from api import chat_observability
from fastapi.testclient import TestClient
from api.utils import PROJECT_ROOT, resolve_runtime_dir
from aiteam.context_curator import ContextCuratorStore
from aiteam.sqlite_store import SqliteStore

pytestmark = pytest.mark.slow


class RunVerdictCoercionTests(unittest.TestCase):
    def test_coerce_run_verdict_preserves_failure_origin(self) -> None:
        verdict = chat_observability._coerce_run_verdict(
            {
                "state": "failed",
                "result": "fallido",
                "failure_origin": "preplanning_support",
                "reason_codes": ["phase_failed:scout_context_curator"],
            }
        )
        self.assertEqual(verdict.get("failure_origin"), "preplanning_support")


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

    @staticmethod
    def _write_runtime_tasks_sqlite(runtime_dir: Path, tasks: list[dict]) -> None:
        SqliteStore(runtime_dir / "aiteam.db").save_all_tasks(tasks)

    @staticmethod
    def _write_runtime_workflow_sqlite(runtime_dir: Path, workflow_state: dict) -> None:
        SqliteStore(runtime_dir / "aiteam.db").save_workflow_state(workflow_state)

    @staticmethod
    def _flatten_tree_paths(node: dict | None) -> list[str]:
        if not node:
            return []
        paths = [str(node.get("path", ""))]
        for child in list(node.get("children", []) or []):
            paths.extend(APIAIStateNotebookLMTests._flatten_tree_paths(child))
        return paths

    def test_external_project_uses_aiteam_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)

            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)

            self.assertEqual(runtime_dir, workspace / ".aiteam")
            self.assertFalse(runtime_dir.exists())

    def test_self_project_uses_runtime_dir(self) -> None:
        runtime_dir = resolve_runtime_dir(PROJECT_ROOT, PROJECT_ROOT)

        self.assertEqual(runtime_dir, PROJECT_ROOT / "runtime")

    def test_api_state_missing_runtime_mentions_aiteam_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.get("/api/aiteam/state?environment=dev")
                self.assertEqual(response.status_code, 404)
                self.assertIn(".aiteam/", str(response.json().get("detail", "")))
                self.assertIn("runtime/", str(response.json().get("detail", "")))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_dashboard_missing_runtime_mentions_aiteam_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.get("/api/dashboard")
                self.assertEqual(response.status_code, 404)
                self.assertIn(".aiteam/", str(response.json().get("detail", "")))
                self.assertIn("runtime/", str(response.json().get("detail", "")))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_state_last_chat_run_surfaces_product_artifacts_from_probe_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "events.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "ts": "2026-04-02T10:00:00+00:00",
                                "event_type": "chat_plan_created",
                                "payload": {
                                    "task_id": "CHAT-art001",
                                    "chat_mode": "probe",
                                    "round_budget": 4,
                                    "phase_count": 2,
                                    "delegated_count": 0,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "ts": "2026-04-02T10:00:03+00:00",
                                "event_type": "chat_probe_completed",
                                "payload": {
                                    "task_id": "CHAT-art001",
                                    "chat_mode": "probe",
                                    "artifact_created": 1,
                                    "artifact_modified": 1,
                                    "artifact_files": ["docs/aiteam/plan.md", "src/app.py"],
                                },
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
                payload = client.get("/api/aiteam/state?environment=dev").json()
                artifacts = ((payload.get("last_chat_run", {}) or {}).get("product_artifacts", {}) or {})
                self.assertTrue(bool(artifacts.get("has_artifacts")))
                self.assertEqual(int(artifacts.get("created", 0)), 1)
                self.assertEqual(int(artifacts.get("modified", 0)), 1)
                self.assertEqual(
                    list(artifacts.get("files", []) or []),
                    ["docs/aiteam/plan.md", "src/app.py"],
                )
                self.assertIn("artefactos de producto", str(artifacts.get("message", "")))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_state_last_chat_run_explicitly_reports_when_no_product_artifacts_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-04-02T11:00:00+00:00",
                        "event_type": "chat_plan_created",
                        "payload": {
                            "task_id": "CHAT-art002",
                            "chat_mode": "sprint5",
                            "round_budget": 5,
                            "phase_count": 3,
                            "delegated_count": 1,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                payload = client.get("/api/aiteam/state?environment=dev").json()
                artifacts = ((payload.get("last_chat_run", {}) or {}).get("product_artifacts", {}) or {})
                self.assertFalse(bool(artifacts.get("has_artifacts")))
                self.assertEqual(list(artifacts.get("files", []) or []), [])
                self.assertEqual(
                    str(artifacts.get("message", "")),
                    "Esta run no genero artefactos de producto.",
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_state_last_chat_run_treats_plan_mode_completion_as_terminal_without_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "events.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "ts": "2026-04-02T11:30:00+00:00",
                                "event_type": "chat_plan_created",
                                "payload": {
                                    "task_id": "CHAT-plan01",
                                    "chat_mode": "plan",
                                    "round_budget": 4,
                                    "phase_count": 0,
                                    "delegated_count": 0,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "ts": "2026-04-02T11:30:03+00:00",
                                "event_type": "chat_plan_mode_completed",
                                "payload": {
                                    "task_id": "CHAT-plan01",
                                    "chat_mode": "plan",
                                    "artifact_created": 0,
                                    "artifact_modified": 0,
                                    "artifact_files": [],
                                },
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
                payload = client.get("/api/aiteam/state?environment=dev").json()
                last_chat_run = dict(payload.get("last_chat_run", {}) or {})
                artifacts = dict(last_chat_run.get("product_artifacts", {}) or {})
                self.assertEqual(str(last_chat_run.get("mode", "")), "plan")
                self.assertEqual(str(last_chat_run.get("status", "")), "completed")
                self.assertFalse(bool(artifacts.get("has_artifacts")))
                self.assertEqual(int(artifacts.get("created", 0)), 0)
                self.assertEqual(int(artifacts.get("modified", 0)), 0)
                self.assertEqual(list(artifacts.get("files", []) or []), [])
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_state_last_chat_run_preserves_exact_artifact_count_when_files_are_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            preview_files = [f"src/file_{idx:02d}.py" for idx in range(16)]
            (runtime_dir / "events.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "ts": "2026-04-02T12:00:00+00:00",
                                "event_type": "chat_plan_created",
                                "payload": {
                                    "task_id": "CHAT-art003",
                                    "chat_mode": "sprint5",
                                    "round_budget": 5,
                                    "phase_count": 3,
                                    "delegated_count": 1,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "ts": "2026-04-02T12:00:10+00:00",
                                "event_type": "chat_artifacts_detected",
                                "payload": {
                                    "task_id": "CHAT-art003",
                                    "created": 12,
                                    "modified": 9,
                                    "file_count": 21,
                                    "files_truncated": True,
                                    "files": preview_files,
                                },
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
                payload = client.get("/api/aiteam/state?environment=dev").json()
                artifacts = ((payload.get("last_chat_run", {}) or {}).get("product_artifacts", {}) or {})
                self.assertTrue(bool(artifacts.get("has_artifacts")))
                self.assertEqual(int(artifacts.get("file_count", 0)), 21)
                self.assertTrue(bool(artifacts.get("files_preview_truncated")))
                self.assertEqual(list(artifacts.get("files", []) or []), preview_files)
                self.assertIn("21 artefactos", str(artifacts.get("message", "")))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_migration_from_runtime_to_aiteam(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            legacy_runtime = workspace / "runtime"
            legacy_runtime.mkdir(parents=True, exist_ok=True)
            marker = legacy_runtime / "events.jsonl"
            marker.write_text("migrated", encoding="utf-8")

            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)

            self.assertEqual(runtime_dir, workspace / ".aiteam")
            self.assertTrue(runtime_dir.exists())
            self.assertFalse(legacy_runtime.exists())
            self.assertEqual(
                (runtime_dir / "events.jsonl").read_text(encoding="utf-8"),
                "migrated",
            )

    def test_migration_from_runtime_to_aiteam_recovers_after_permission_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            legacy_runtime = workspace / "runtime"
            legacy_runtime.mkdir(parents=True, exist_ok=True)
            (legacy_runtime / "events.jsonl").write_text("migrated", encoding="utf-8")
            original_rename = Path.rename
            attempts = {"count": 0}

            def _rename_once_denied(path_obj: Path, target: Path | str) -> Path:
                target_path = Path(target)
                if (
                    path_obj == legacy_runtime
                    and target_path == workspace / ".aiteam"
                    and attempts["count"] == 0
                ):
                    attempts["count"] += 1
                    raise PermissionError(5, "Acceso denegado")
                return original_rename(path_obj, target)

            with patch("pathlib.Path.rename", autospec=True, side_effect=_rename_once_denied):
                runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)

            self.assertEqual(attempts["count"], 1)
            self.assertEqual(runtime_dir, workspace / ".aiteam")
            self.assertTrue(runtime_dir.exists())
            self.assertEqual(
                (runtime_dir / "events.jsonl").read_text(encoding="utf-8"),
                "migrated",
            )

    def test_existing_aiteam_dir_absorbs_missing_legacy_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            dotdir = workspace / ".aiteam"
            dotdir.mkdir(parents=True, exist_ok=True)
            (dotdir / "events.jsonl").write_text("current", encoding="utf-8")

            legacy_runtime = workspace / "runtime"
            legacy_runtime.mkdir(parents=True, exist_ok=True)
            (legacy_runtime / "mailbox.jsonl").write_text("legacy-mailbox", encoding="utf-8")
            (legacy_runtime / "nested").mkdir(parents=True, exist_ok=True)
            (legacy_runtime / "nested" / "ledger.jsonl").write_text("legacy-ledger", encoding="utf-8")

            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)

            self.assertEqual(runtime_dir, dotdir)
            self.assertEqual((dotdir / "events.jsonl").read_text(encoding="utf-8"), "current")
            self.assertEqual((dotdir / "mailbox.jsonl").read_text(encoding="utf-8"), "legacy-mailbox")
            self.assertEqual(
                (dotdir / "nested" / "ledger.jsonl").read_text(encoding="utf-8"),
                "legacy-ledger",
            )
            self.assertFalse(legacy_runtime.exists())

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
                runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
                self.assertTrue((runtime_dir / "notebooklm_sync_status.json").exists())
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
                expected_ts = datetime.fromisoformat(
                    "2026-02-21T01:01:00+00:00"
                ).astimezone().isoformat()
                self.assertEqual(
                    str(payload.get("items", [])[0].get("timestamp", "")),
                    expected_ts,
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_fs_tree_ignores_workspace_temp_noise_and_runtime_tmp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "src").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (workspace / ".tmp").mkdir(parents=True, exist_ok=True)
            (workspace / ".tmp" / "scratch.txt").write_text("noise\n", encoding="utf-8")
            (workspace / ".tmp_pytest_runtime").mkdir(parents=True, exist_ok=True)
            (workspace / ".tmp_pytest_runtime" / "cache.txt").write_text("noise\n", encoding="utf-8")
            (workspace / "runtime" / "tmp").mkdir(parents=True, exist_ok=True)
            (workspace / "runtime" / "tmp" / "trace.log").write_text("noise\n", encoding="utf-8")
            (workspace / "runtime" / "events.jsonl").write_text("{}\n", encoding="utf-8")

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.get("/api/fs/tree")
                self.assertEqual(response.status_code, 200)
                paths = self._flatten_tree_paths(response.json())
                self.assertIn("src", paths)
                self.assertIn("src/app.py", paths)
                self.assertIn("runtime", paths)
                self.assertIn("runtime/events.jsonl", paths)
                self.assertNotIn(".tmp", paths)
                self.assertNotIn(".tmp/scratch.txt", paths)
                self.assertNotIn(".tmp_pytest_runtime", paths)
                self.assertNotIn(".tmp_pytest_runtime/cache.txt", paths)
                self.assertNotIn("runtime/tmp", paths)
                self.assertNotIn("runtime/tmp/trace.log", paths)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_fs_tree_ignores_external_project_aiteam_tmp_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".aiteam" / "tmp").mkdir(parents=True, exist_ok=True)
            (workspace / ".aiteam" / "tmp" / "scratch.txt").write_text("noise\n", encoding="utf-8")
            (workspace / ".aiteam" / "events.jsonl").write_text("{}\n", encoding="utf-8")

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.get("/api/fs/tree")
                self.assertEqual(response.status_code, 200)
                paths = self._flatten_tree_paths(response.json())
                self.assertIn(".aiteam", paths)
                self.assertIn(".aiteam/events.jsonl", paths)
                self.assertNotIn(".aiteam/tmp", paths)
                self.assertNotIn(".aiteam/tmp/scratch.txt", paths)
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

            self._write_runtime_tasks_sqlite(
                runtime_dir,
                [
                    {
                        "task_id": "T-100",
                        "role": "engineer",
                        "state": "completed",
                        "metadata": {
                            "result": "Implemented and validated",
                        },
                    }
                ],
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.get("/api/aiteam/logs?limit=20")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertGreaterEqual(len(payload.get("event_logs", [])), 1)
                expected_ts = datetime.fromisoformat(
                    "2026-02-21T01:11:00+00:00"
                ).astimezone().isoformat()
                self.assertEqual(
                    str(payload.get("event_logs", [])[0].get("ts", "")),
                    expected_ts,
                )
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

            self._write_runtime_tasks_sqlite(
                runtime_dir,
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
                ],
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
            self._write_runtime_tasks_sqlite(
                runtime_dir,
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
                ],
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

    def test_aiteam_state_includes_project_continuity_summary_from_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            self._write_runtime_tasks_sqlite(
                runtime_dir,
                [
                    {
                        "task_id": "CHAT-ctxsql::lead_intake",
                        "title": "Lead intake and project framing",
                        "description": "Solicitud original:\ncrear prototipo sqlite\nEntrega: objetivos",
                        "role": "team_lead",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"phase": "lead_intake"},
                    },
                    {
                        "task_id": "CHAT-ctxsql::lead_close",
                        "title": "Lead synthesis and response",
                        "description": "close",
                        "role": "team_lead",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": ["CHAT-ctxsql::lead_intake"],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"result": "Se definio plan persistido en sqlite"},
                    },
                ],
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.get("/api/aiteam/state?environment=dev")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                continuity = str(payload.get("project_continuity", ""))
                self.assertIn("CHAT-ctxsql", continuity)
                self.assertIn("crear prototipo sqlite", continuity)
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
            self._write_runtime_workflow_sqlite(
                runtime_dir,
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
            )
            curator_store = ContextCuratorStore(runtime_dir)
            project_ctx = curator_store.load_project_context(str(workspace.resolve()))
            project_ctx["durable_facts"] = [{"text": "login flow auditado", "confidence": 0.7}]
            project_ctx["decisions"] = [{"text": "priorizar browser evidence", "confidence": 0.8}]
            project_ctx["updated_at"] = now_iso
            curator_store._write_project_context(str(workspace.resolve()), project_ctx)
            reloaded_project_ctx = curator_store.load_project_context(str(workspace.resolve()))
            self.assertEqual(len(list(reloaded_project_ctx.get("durable_facts", []) or [])), 1)
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
            reloaded_chat_ctx = curator_store.load_chat_context("CHAT-econ01", project_key=str(workspace.resolve()))
            self.assertEqual(len(list(reloaded_chat_ctx.get("invalidations", []) or [])), 1)
            legacy_project_files = list((runtime_dir / "context" / "projects").glob("*.json"))
            legacy_chat_files = list((runtime_dir / "context" / "chats").glob("*.json"))
            self.assertTrue(legacy_project_files)
            self.assertTrue(legacy_chat_files)
            self.assertEqual(
                len(list((json.loads(legacy_project_files[0].read_text(encoding="utf-8")) or {}).get("durable_facts", []) or [])),
                1,
            )
            self.assertEqual(
                len(list((json.loads(legacy_chat_files[0].read_text(encoding="utf-8")) or {}).get("invalidations", []) or [])),
                1,
            )
            self._write_runtime_tasks_sqlite(
                runtime_dir,
                [
                    {
                        "task_id": "CHAT-econ01::build",
                        "state": "completed",
                        "metadata": {
                            "tool_rewiring_active": True,
                            "tool_rewiring_preferred_specialist": "skill_worker",
                            "tool_rewiring_replacement_for": ["semgrep_mcp"],
                            "consulted_roles": ["scout", "reviewer"],
                            "consulted_providers": ["anthropic", "openai"],
                            "peer_diversity_observed": True,
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
                peer_summary = last_chat.get("peer_consultation_summary", {}) or {}
                self.assertEqual(
                    list(peer_summary.get("consulted_roles", [])),
                    ["scout", "reviewer"],
                )
                self.assertEqual(
                    list(peer_summary.get("consulted_providers", [])),
                    ["anthropic", "openai"],
                )
                self.assertTrue(bool(peer_summary.get("diversity_observed", False)))
                curator_summary = last_chat.get("context_curator_summary", {}) or {}
                self.assertEqual(str(curator_summary.get("freshness_status", "")), "fresh")
                self.assertEqual(
                    int(curator_summary.get("invalidation_count", 0)),
                    1,
                    msg=json.dumps(curator_summary, ensure_ascii=False, indent=2),
                )
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
                conv_peer_summary = conv_last.get("peer_consultation_summary", {}) or {}
                self.assertEqual(int(conv_peer_summary.get("provider_count", 0)), 2)
                self.assertTrue(bool(conv_peer_summary.get("diversity_observed", False)))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_specialist_insight_summary_keeps_total_count_when_reports_are_truncated(self) -> None:
        from api.utils import _specialist_insight_fields

        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)

            reports = [
                {
                    "specialist": f"browser_operator_{idx}",
                    "summary": f"Resumen {idx}",
                    "validation_status": "valid" if idx < 8 else "invalid",
                }
                for idx in range(10)
            ]
            self._write_runtime_tasks_sqlite(
                runtime_dir,
                [
                    {
                        "task_id": "CHAT-COUNT::build",
                        "title": "Build",
                        "description": "desc",
                        "role": "engineer",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "eng-1",
                        "metadata": {"specialist_reports": reports},
                    }
                ],
            )

            payload = _specialist_insight_fields(runtime_dir, "CHAT-COUNT")
            summary = dict(payload.get("specialist_report_summary", {}) or {})

            self.assertEqual(len(payload.get("specialist_reports", []) or []), 8)
            self.assertEqual(int(summary.get("count", 0)), 10)
            self.assertEqual(int(summary.get("displayed_count", 0)), 8)
            self.assertTrue(bool(summary.get("truncated", False)))
            self.assertEqual(int(summary.get("valid_count", 0)), 8)
            self.assertEqual(int(summary.get("invalid_count", 0)), 2)

    def test_chat_endpoints_prefer_sqlite_runtime_over_legacy_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)

            self._write_runtime_tasks_sqlite(
                runtime_dir,
                [
                    {
                        "task_id": "CHAT-A1B2C3D4::lead_intake",
                        "title": "Lead intake and project framing",
                        "description": "Solicitud original:\nleer desde sqlite\nEntrega: objetivos",
                        "role": "team_lead",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"phase": "lead_intake", "execution_round": 1},
                    },
                    {
                        "task_id": "CHAT-A1B2C3D4::build",
                        "title": "Build highest-impact slice",
                        "description": "Implementar lectura sqlite",
                        "role": "engineer",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": ["CHAT-A1B2C3D4::lead_intake"],
                        "state": "completed",
                        "assignee": "eng-1",
                        "metadata": {
                            "phase": "build",
                            "execution_round": 2,
                            "last_provider": "anthropic",
                            "last_model": "claude-sonnet-4-5",
                            "last_channel": "subscription",
                            "result": "Lectura SQLite completada y validada.",
                        },
                    },
                    {
                        "task_id": "CHAT-A1B2C3D4::delegate_build_repo_scout_0",
                        "title": "Scout repository before build",
                        "description": "Inspeccionar el repo antes de la implementacion",
                        "role": "scout",
                        "complexity": "low",
                        "criticality": "low",
                        "dependencies": ["CHAT-A1B2C3D4::build"],
                        "state": "pending",
                        "assignee": "scout-1",
                        "metadata": {"phase": "build"},
                    },
                    {
                        "task_id": "CHAT-A1B2C3D4::lead_close",
                        "title": "Lead synthesis and response",
                        "description": "close",
                        "role": "team_lead",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": ["CHAT-A1B2C3D4::build"],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"result": "Respuesta desde sqlite", "execution_round": 3},
                    },
                ],
            )
            self._write_runtime_workflow_sqlite(
                runtime_dir,
                {
                    "CHAT-A1B2C3D4": {
                        "phase_outputs": {"build": "resultado sqlite"},
                        "phase_evidence_plan": {
                            "build": {
                                "delegate_intents": ["delegate_test_run"],
                                "wait_policy": "quorum",
                                "delegate_budget": 2,
                            }
                        },
                        "delegate_economics_summary": {
                            "economics_version": "delegate_economics_v1",
                            "estimated": True,
                            "batch_count": 1,
                            "specialist_task_count": 2,
                            "estimated_net_tokens_saved": 900,
                        },
                    }
                },
            )

            (runtime_dir / "tasks.json").write_text(
                json.dumps(
                    [
                        {
                            "task_id": "CHAT-0BADF00D::lead_intake",
                            "title": "Legacy root",
                            "description": "Solicitud original:\nlegacy json",
                            "role": "team_lead",
                            "state": "completed",
                            "metadata": {"phase": "lead_intake"},
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (runtime_dir / "workflow_state.json").write_text(
                json.dumps(
                    {
                        "CHAT-0BADF00D": {
                            "phase_evidence_plan": {
                                "build": {"delegate_budget": 99}
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-03-31T10:00:00+00:00",
                        "event_type": "task_started",
                        "payload": {
                            "task_id": "CHAT-A1B2C3D4::lead_intake",
                            "role": "team_lead",
                            "assignee": "lead-1",
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "ts": "2026-03-31T10:00:01+00:00",
                        "event_type": "task_execution",
                        "payload": {
                            "task_id": "CHAT-A1B2C3D4::build",
                            "role": "engineer",
                            "assignee": "eng-1",
                            "success": True,
                            "thread_id": "thread-abcd1234",
                            "thread_provider": "anthropic",
                            "thread_channel": "subscription",
                            "thread_model_family": "claude_sonnet_4_5",
                            "thread_generation": 2,
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "ts": "2026-03-31T10:00:02+00:00",
                        "event_type": "decision_recorded",
                        "payload": {
                            "task_id": "CHAT-econ01::build",
                            "role": "team_lead",
                            "assignee": "lead-1",
                            "decision_rank": 5,
                            "consulted_roles": ["scout", "reviewer"],
                            "consulted_providers": ["anthropic", "openai"],
                            "peer_diversity_observed": True,
                            "provider": "openai",
                            "model": "gpt-5.4",
                            "channel": "subscription",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)

                state_payload = client.get("/api/aiteam/state?environment=dev").json()
                continuity = str(state_payload.get("project_continuity", ""))
                self.assertIn("CHAT-A1B2C3D4", continuity)
                self.assertNotIn("CHAT-0BADF00D", continuity)

                load_payload = client.get("/api/aiteam/chat/load/CHAT-A1B2C3D4").json()
                messages = list(load_payload.get("messages", []) or [])
                self.assertEqual(messages[0].get("sender"), "user")
                self.assertIn("leer desde sqlite", str(messages[0].get("text", "")))
                self.assertEqual(messages[-1].get("sender"), "team")
                self.assertIn("Respuesta desde sqlite", str(messages[-1].get("text", "")))

                progress_payload = client.get("/api/aiteam/chat/progress/CHAT-A1B2C3D4").json()
                progress_build_plan = (
                    ((progress_payload.get("phase_evidence_plan", {}) or {}).get("build", {}) or {})
                )
                self.assertTrue(bool(progress_payload.get("exists")))
                self.assertEqual(
                    str(((progress_payload.get("phase_states", {}) or {}).get("build", ""))),
                    "completed",
                )
                self.assertEqual(
                    int(progress_build_plan.get("delegate_budget", 0)),
                    2,
                )
                task_summaries = list(progress_payload.get("task_summaries", []) or [])
                self.assertTrue(task_summaries)
                build_summary = next(
                    item for item in task_summaries if item.get("task_id") == "CHAT-A1B2C3D4::build"
                )
                self.assertEqual(str(build_summary.get("category", "")), "phase")
                self.assertEqual(str(build_summary.get("provider", "")), "anthropic")
                self.assertEqual(str(build_summary.get("model", "")), "claude-sonnet-4-5")
                self.assertEqual(str(build_summary.get("thread_provider", "")), "anthropic")
                self.assertEqual(str(build_summary.get("thread_model_family", "")), "claude_sonnet_4_5")
                self.assertEqual(int(build_summary.get("thread_generation", 0)), 2)
                self.assertIn("SQLite", str(build_summary.get("preview", "")))
                self.assertIn("SQLite", str(build_summary.get("full_text", "")))
                thread_summary = dict(progress_payload.get("thread_summary", {}) or {})
                self.assertEqual(str(thread_summary.get("provider", "")), "anthropic")
                self.assertEqual(int(thread_summary.get("generation", 0)), 2)
                self.assertEqual(str(thread_summary.get("thread_id", "")), "thread-abcd1234")
                delegate_summary = next(
                    item
                    for item in task_summaries
                    if item.get("task_id") == "CHAT-A1B2C3D4::delegate_build_repo_scout_0"
                )
                self.assertEqual(str(delegate_summary.get("category", "")), "delegate")
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_conversations_logs_and_workflow_state_prefer_sqlite_over_legacy_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)

            self._write_runtime_tasks_sqlite(
                runtime_dir,
                [
                    {
                        "task_id": "CHAT-SQLITE1::lead_intake",
                        "title": "Lead intake and project framing",
                        "description": "Solicitud original:\ncontinuar desde sqlite\nEntrega: objetivos",
                        "role": "team_lead",
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"phase": "lead_intake"},
                    },
                    {
                        "task_id": "TASK-SQLITE-1",
                        "role": "engineer",
                        "state": "completed",
                        "metadata": {"result": "Salida principal desde sqlite"},
                    },
                ],
            )
            self._write_runtime_workflow_sqlite(
                runtime_dir,
                {
                    "CHAT-SQLITE1": {
                        "phase_evidence_plan": {
                            "build": {"delegate_budget": 3}
                        },
                        "delegate_economics_summary": {
                            "estimated_net_tokens_saved": 420
                        },
                    }
                },
            )

            (runtime_dir / "tasks.json").write_text(
                json.dumps(
                    [
                        {
                            "task_id": "CHAT-LEGACY99::lead_intake",
                            "title": "Legacy root",
                            "description": "Solicitud original:\nlegacy json",
                            "role": "team_lead",
                            "state": "completed",
                            "metadata": {"phase": "lead_intake"},
                        },
                        {
                            "task_id": "TASK-LEGACY-1",
                            "role": "engineer",
                            "state": "completed",
                            "metadata": {"result": "Salida legacy"},
                        },
                    ]
                ),
                encoding="utf-8",
            )
            (runtime_dir / "workflow_state.json").write_text(
                json.dumps(
                    {
                        "CHAT-LEGACY99": {
                            "phase_evidence_plan": {
                                "build": {"delegate_budget": 99}
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-03-31T10:00:00+00:00",
                        "event_type": "chat_plan_created",
                        "payload": {
                            "task_id": "CHAT-SQLITE1",
                            "chat_mode": "multi_phase",
                            "round_budget": 4,
                            "phase_count": 3,
                            "delegated_count": 0,
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "ts": "2026-03-31T10:01:00+00:00",
                        "event_type": "task_started",
                        "payload": {
                            "task_id": "CHAT-SQLITE1::lead_intake",
                            "role": "team_lead",
                            "assignee": "lead-1",
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "ts": "2026-03-31T10:02:00+00:00",
                        "event_type": "task_execution",
                        "payload": {
                            "task_id": "TASK-SQLITE-1",
                            "role": "engineer",
                            "assignee": "eng-1",
                            "success": True,
                            "latency_ms": 90,
                        },
                    }
                )
                + "\n"
                ,
                encoding="utf-8",
            )
            (runtime_dir / "mailbox.jsonl").write_text("", encoding="utf-8")

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)

                conv_payload = client.get("/api/aiteam/conversations?limit=20").json()
                user_rows = [item for item in conv_payload.get("items", []) if item.get("sender") == "user"]
                self.assertTrue(user_rows)
                self.assertIn("continuar desde sqlite", str(user_rows[0].get("body", "")))
                self.assertNotIn("legacy json", str(user_rows[0].get("body", "")))
                self.assertEqual(
                    int(
                        (((conv_payload.get("last_chat_run", {}) or {}).get("phase_evidence_plan", {}) or {}).get("build", {}) or {}).get("delegate_budget", 0)
                    ),
                    3,
                )

                logs_payload = client.get("/api/aiteam/logs?limit=20").json()
                task_output_ids = {
                    str(item.get("task_id", ""))
                    for item in list(logs_payload.get("task_outputs", []) or [])
                }
                self.assertIn("TASK-SQLITE-1", task_output_ids)
                self.assertNotIn("TASK-LEGACY-1", task_output_ids)

                workflow_payload = client.get("/api/aiteam/workflow-state").json()
                workflows = workflow_payload.get("workflows", {}) or {}
                self.assertIn("CHAT-SQLITE1", workflows)
                self.assertNotIn("CHAT-LEGACY99", workflows)
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

    def _fetch_routing_catalog_payload(self) -> dict:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=False):
            workspace = Path(tmp)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
            runtime_dir.mkdir(parents=True, exist_ok=True)

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.get("/api/aiteam/routing/catalog")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertIn("team_lead", list(payload.get("roles", []) or []))
                self.assertIn("engineer", list(payload.get("roles", []) or []))
                self.assertGreaterEqual(int((payload.get("summary", {}) or {}).get("adapter_count", 0)), 1)
                self.assertEqual(
                    dict(payload.get("policy", {}) or {}).get("source"),
                    "defaults_repo",
                )
                return payload
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_routing_catalog_has_payload_version(self) -> None:
        payload = self._fetch_routing_catalog_payload()

        self.assertEqual(payload.get("payload_version"), 1)
        self.assertEqual(
            str(dict(payload.get("cache", {}) or {}).get("status", "")),
            "fresh",
        )

    def test_routing_catalog_uses_cache_between_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=False):
            workspace = Path(tmp)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
            runtime_dir.mkdir(parents=True, exist_ok=True)

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch("api.routers.aiteam._build_routing_catalog") as mocked_build:
                    mocked_build.return_value = {
                        "payload_version": 1,
                        "roles": ["team_lead", "engineer"],
                        "providers": [],
                        "adapters": [],
                        "role_matrix": [],
                        "summary": {"adapter_count": 1},
                        "policy": {"source": "defaults_repo"},
                    }
                    first = client.get("/api/aiteam/routing/catalog")
                    self.assertEqual(first.status_code, 200)
                    second = client.get("/api/aiteam/routing/catalog")
                    self.assertEqual(second.status_code, 200)

                self.assertEqual(mocked_build.call_count, 1)
                self.assertEqual(
                    str(dict(first.json().get("cache", {}) or {}).get("status", "")),
                    "fresh",
                )
                self.assertEqual(
                    str(dict(second.json().get("cache", {}) or {}).get("status", "")),
                    "hit",
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_routing_catalog_separates_defaults_and_effective(self) -> None:
        payload = self._fetch_routing_catalog_payload()

        self.assertFalse(
            bool(dict(payload.get("policy", {}) or {}).get("override_local_present", True))
        )
        matrix_rows = {
            str(item.get("role", "") or ""): item
            for item in list(payload.get("role_matrix", []) or [])
            if isinstance(item, dict)
        }
        engineer = dict(matrix_rows.get("engineer", {}) or {})
        self.assertEqual(
            list(dict(engineer.get("defaults", {}) or {}).get("providers", []) or []),
            ["openai", "google", "groq"],
        )
        self.assertEqual(
            list(dict(engineer.get("defaults", {}) or {}).get("models", []) or []),
            list(engineer.get("configured_model_order", []) or []),
        )
        self.assertIsNone(engineer.get("override_local"))
        effective = dict(engineer.get("effective", {}) or {})
        self.assertIn("primary", effective)
        self.assertIn("fallbacks", effective)
        self.assertEqual(
            effective.get("primary"),
            engineer.get("primary") if engineer.get("primary") else None,
        )
        self.assertEqual(
            list(effective.get("fallbacks", []) or []),
            list(engineer.get("fallbacks", []) or []),
        )

    def test_routing_catalog_blockers_have_stable_codes(self) -> None:
        payload = self._fetch_routing_catalog_payload()

        stable_codes = {
            "role_targets",
            "team_lead_guard",
            "adapter_unavailable",
            "provider_unhealthy",
            "cost_exceeded",
            "capability_missing",
            "channel_excluded",
        }
        matrix_rows = {
            str(item.get("role", "") or ""): item
            for item in list(payload.get("role_matrix", []) or [])
            if isinstance(item, dict)
        }
        engineer = dict(matrix_rows.get("engineer", {}) or {})
        engineer_adapters = {
            str(item.get("adapter_name", "") or ""): item
            for item in list(engineer.get("adapters", []) or [])
            if isinstance(item, dict)
        }
        self.assertIn("claude_pro", engineer_adapters)
        blockers = list((engineer_adapters.get("claude_pro", {}) or {}).get("blockers", []) or [])
        self.assertTrue(blockers)
        self.assertTrue(set(blockers).issubset(stable_codes))
        blocker_details = list(
            (engineer_adapters.get("claude_pro", {}) or {}).get("blocker_details", []) or []
        )
        self.assertTrue(blocker_details)
        self.assertTrue(
            {
                str(item.get("code", "") or "")
                for item in blocker_details
                if isinstance(item, dict)
            }.issubset(stable_codes)
        )
        self.assertEqual(blocker_details[0].get("code"), "role_targets")
        self.assertIn(
            "adapter restringido a otros roles",
            str(blocker_details[0].get("label", "")),
        )

    def test_routing_catalog_exposes_capabilities(self) -> None:
        payload = self._fetch_routing_catalog_payload()

        adapter_rows = {
            str(item.get("adapter_name", "") or ""): item
            for item in list(payload.get("adapters", []) or [])
            if isinstance(item, dict)
        }
        self.assertIn("claude_pro", adapter_rows)
        claude_pro = dict(adapter_rows.get("claude_pro", {}) or {})
        capability_profile = dict(claude_pro.get("capability_profile", {}) or {})
        self.assertIn("channel", capability_profile)
        self.assertIn("tier", capability_profile)
        self.assertIn("cost_class", capability_profile)
        self.assertIn("tool_support", capability_profile)
        self.assertIn("stream_support", capability_profile)
        self.assertIn("vision", capability_profile)
        self.assertIn("thinking", capability_profile)
        self.assertIn("long_context", capability_profile)
        self.assertEqual(capability_profile.get("channel"), "subscription")
        self.assertEqual(capability_profile.get("tier"), claude_pro.get("tier"))
        self.assertEqual(capability_profile.get("thinking"), claude_pro.get("supports_thinking"))

    def test_api_update_overrides_validates_before_saving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=False):
            workspace = Path(tmp)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.put(
                    "/api/aiteam/routing/overrides",
                    json={
                        "overrides_by_role": {
                            "engineer": {
                                "excluded_providers": ["openai", "google", "groq"],
                            }
                        }
                    },
                )
                self.assertEqual(response.status_code, 400)
                detail = dict(response.json().get("detail", {}) or {})
                self.assertIn("errors", detail)
                self.assertFalse((runtime_dir / "routing_overrides.json").exists())
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_api_routing_overrides_roundtrip_and_catalog_reflects_effective_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=False):
            workspace = Path(tmp)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                update_response = client.put(
                    "/api/aiteam/routing/overrides",
                    json={
                        "overrides_by_role": {
                            "engineer": {
                                "providers": ["google", "groq"],
                                "primary_provider": "google",
                                "excluded_providers": ["openai"],
                            }
                        }
                    },
                )
                self.assertEqual(update_response.status_code, 200)
                updated = update_response.json()
                self.assertTrue(bool(updated.get("override_local_present")))
                self.assertEqual(
                    (
                        dict(updated.get("override_local", {}) or {})
                        .get("overrides_by_role", {})
                        .get("engineer", {})
                        .get("primary_provider")
                    ),
                    "google",
                )
                self.assertTrue((runtime_dir / "routing_overrides.json").exists())

                get_response = client.get("/api/aiteam/routing/overrides")
                self.assertEqual(get_response.status_code, 200)
                current = get_response.json()
                self.assertTrue(bool(current.get("override_local_present")))

                catalog_response = client.get("/api/aiteam/routing/catalog")
                self.assertEqual(catalog_response.status_code, 200)
                catalog = catalog_response.json()
                self.assertTrue(
                    bool(dict(catalog.get("policy", {}) or {}).get("override_local_present"))
                )
                engineer = next(
                    item
                    for item in list(catalog.get("role_matrix", []) or [])
                    if str((item or {}).get("role", "") or "") == "engineer"
                )
                self.assertEqual(
                    dict(engineer.get("override_local", {}) or {}).get("primary_provider"),
                    "google",
                )
                self.assertEqual(
                    list(engineer.get("configured_provider_order", []) or [])[:2],
                    ["google", "groq"],
                )
                self.assertEqual(
                    dict(dict(engineer.get("effective", {}) or {}).get("primary", {}) or {}).get(
                        "provider"
                    ),
                    "google",
                )
                self.assertEqual(
                    str(dict(catalog.get("cache", {}) or {}).get("status", "")),
                    "fresh",
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_api_reset_overrides_deletes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=False):
            workspace = Path(tmp)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                client.put(
                    "/api/aiteam/routing/overrides",
                    json={
                        "overrides_by_role": {
                            "engineer": {
                                "providers": ["google", "groq"],
                                "primary_provider": "google",
                            }
                        }
                    },
                )
                self.assertTrue((runtime_dir / "routing_overrides.json").exists())

                response = client.delete("/api/aiteam/routing/overrides")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(bool(payload.get("ok")))
                self.assertFalse(bool(payload.get("override_local_present")))
                self.assertFalse((runtime_dir / "routing_overrides.json").exists())
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_state_last_chat_run_exposes_operational_task_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-04-02T14:00:00+00:00",
                        "event_type": "chat_plan_created",
                        "payload": {
                            "task_id": "CHAT-A1B2C3D4",
                            "chat_mode": "sprint5",
                            "round_budget": 5,
                            "phase_count": 3,
                            "delegated_count": 1,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self._write_runtime_tasks_sqlite(
                runtime_dir,
                [
                    {
                        "task_id": "CHAT-A1B2C3D4::lead_intake",
                        "title": "Lead intake",
                        "description": "desc",
                        "role": "team_lead",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {},
                    },
                    {
                        "task_id": "CHAT-A1B2C3D4::plan_research",
                        "title": "Plan research",
                        "description": "desc",
                        "role": "scout",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "blocked",
                        "assignee": "scout-1",
                        "metadata": {"blocked_reason": "dependency_failed"},
                    },
                    {
                        "task_id": "CHAT-A1B2C3D4::build",
                        "title": "Build",
                        "description": "desc",
                        "role": "engineer",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "blocked",
                        "assignee": "eng-1",
                        "metadata": {"blocked_reason": "no_eligible_adapter"},
                    },
                    {
                        "task_id": "CHAT-A1B2C3D4::qa",
                        "title": "QA",
                        "description": "desc",
                        "role": "qa",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "pending",
                        "assignee": "qa-1",
                        "metadata": {},
                    },
                    {
                        "task_id": "CHAT-0BADF00D::build",
                        "title": "Old build",
                        "description": "desc",
                        "role": "engineer",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "pending",
                        "assignee": "eng-2",
                        "metadata": {},
                    },
                ],
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                payload = client.get("/api/aiteam/state?environment=dev").json()
                summary = (
                    ((payload.get("last_chat_run", {}) or {}).get("task_operational_summary", {}) or {})
                )
                counts = dict(summary.get("counts", {}) or {})
                self.assertTrue(bool(summary.get("has_actionable_items")))
                self.assertEqual(int(summary.get("active_total", 0)), 4)
                self.assertEqual(int(counts.get("blocked_by_dependency", 0)), 1)
                self.assertEqual(int(counts.get("blocked_by_no_eligible_adapter", 0)), 1)
                self.assertEqual(int(counts.get("pending", 0)), 1)
                self.assertEqual(int(counts.get("carried_over_from_previous_run", 0)), 1)

                blocked_reasons = {
                    str(item.get("code", "")): int(item.get("count", 0))
                    for item in list(summary.get("blocked_reasons", []) or [])
                    if isinstance(item, dict)
                }
                self.assertEqual(blocked_reasons.get("dependency_failed"), 1)
                self.assertEqual(blocked_reasons.get("no_eligible_adapter"), 1)
                self.assertIn("CHAT-0BADF00D", list(summary.get("carryover_roots", []) or []))

                sample_items = list(summary.get("sample_items", []) or [])
                self.assertTrue(
                    any(
                        str(item.get("operational_state", "")) == "carried_over_from_previous_run"
                        and str(item.get("source_root", "")) == "CHAT-0BADF00D"
                        for item in sample_items
                        if isinstance(item, dict)
                    )
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_conversations_last_chat_run_exposes_operational_task_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-04-02T14:30:00+00:00",
                        "event_type": "chat_plan_created",
                        "payload": {
                            "task_id": "CHAT-B2C3D4E5",
                            "chat_mode": "sprint5",
                            "round_budget": 4,
                            "phase_count": 2,
                            "delegated_count": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self._write_runtime_tasks_sqlite(
                runtime_dir,
                [
                    {
                        "task_id": "CHAT-B2C3D4E5::lead_intake",
                        "title": "Lead intake",
                        "description": "## User Request\ncrear MVP",
                        "role": "team_lead",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {},
                    },
                    {
                        "task_id": "CHAT-B2C3D4E5::review",
                        "title": "Review",
                        "description": "desc",
                        "role": "reviewer",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "blocked",
                        "assignee": "review-1",
                        "metadata": {"blocked_reason": "specialist_quorum_not_met"},
                    },
                    {
                        "task_id": "CHAT-B2C3D4E5::qa",
                        "title": "QA",
                        "description": "desc",
                        "role": "qa",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "waiting_user",
                        "assignee": "qa-1",
                        "metadata": {},
                    },
                    {
                        "task_id": "CHAT-DEADBEEF::scout",
                        "title": "Old scout",
                        "description": "desc",
                        "role": "scout",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "blocked",
                        "assignee": "scout-2",
                        "metadata": {"blocked_reason": "dependency_failed"},
                    },
                ],
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                payload = client.get("/api/aiteam/conversations?limit=10").json()
                summary = (
                    ((payload.get("last_chat_run", {}) or {}).get("task_operational_summary", {}) or {})
                )
                counts = dict(summary.get("counts", {}) or {})
                self.assertTrue(
                    bool(summary.get("has_actionable_items")),
                    msg=json.dumps(payload, ensure_ascii=False, indent=2),
                )
                self.assertEqual(int(summary.get("active_total", 0)), 3)
                self.assertEqual(int(counts.get("blocked_by_quorum", 0)), 1)
                self.assertEqual(int(counts.get("waiting_user", 0)), 1)
                self.assertEqual(int(counts.get("carried_over_from_previous_run", 0)), 1)
                self.assertEqual(
                    list(summary.get("carryover_roots", []) or []),
                    ["CHAT-DEADBEEF"],
                )

                blocked_reasons = {
                    str(item.get("code", "")): int(item.get("count", 0))
                    for item in list(summary.get("blocked_reasons", []) or [])
                    if isinstance(item, dict)
                }
                self.assertEqual(blocked_reasons.get("specialist_quorum_not_met"), 1)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_state_and_conversations_last_chat_run_expose_authoritative_run_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            root_id = "CHAT-ABCD1234"
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-04-03T18:00:00+00:00",
                        "event_type": "chat_plan_created",
                        "payload": {
                            "task_id": root_id,
                            "chat_mode": "sprint5",
                            "round_budget": 5,
                            "phase_count": 3,
                            "delegated_count": 1,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self._write_runtime_workflow_sqlite(
                runtime_dir,
                {
                    root_id: {
                        "phase_contracts": {
                            "build": {
                                "phase_id": "build",
                                "role": "ENGINEER",
                                "objective": "Ejecuta exactamente el slice aprobado.",
                                "depends_on": ["plan_engineering", "plan_risks"],
                            },
                            "review": {
                                "phase_id": "review",
                                "role": "REVIEWER",
                                "objective": "Valida si build respetó el slice y la evidencia.",
                                "depends_on": ["build"],
                            }
                        },
                        "phase_context_summaries": {
                            "build": "Resumen build: se ejecutó un slice distinto del aprobado.",
                        },
                        "phase_outputs": {
                            "review": "Review output extenso con hallazgos de drift y rechazo del slice ejecutado.",
                        },
                        "phase_verdicts": {
                            "lead_intake": {
                                "phase_id": "lead_intake",
                                "status": "completed",
                                "slice_id": "2",
                                "source": "structured",
                            },
                            "build": {
                                "phase_id": "build",
                                "status": "completed",
                                "contract_status": "drift",
                                "slice_id": "4",
                                "reason_codes": ["slice_drift"],
                                "source": "structured",
                            },
                            "review": {
                                "phase_id": "review",
                                "status": "rejected",
                                "reason_codes": ["review_rejected"],
                                "source": "structured",
                            },
                            "qa": {
                                "phase_id": "qa",
                                "status": "blocked",
                                "reason_codes": ["qa_blocked"],
                                "source": "structured",
                            },
                        },
                        "run_verdict": {
                            "state": "rejected",
                            "result": "fallido",
                            "reason_codes": [
                                "review:rejected_decision",
                                "qa:blocked_status",
                                "build:slice_drift:2->4",
                            ],
                            "policy_signals": ["semantic_gate_failed"],
                            "policy_review_required": True,
                            "semantic_gate_applied": True,
                            "semantic_gate_failures": [
                                "review:rejected_decision",
                                "qa:blocked_status",
                                "build:slice_drift:2->4",
                            ],
                            "evidence_gate_applied": False,
                            "evidence_gate_failures": [],
                            "updated_at": "2026-04-03T18:00:10+00:00",
                        }
                    }
                },
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)

                state_payload = client.get("/api/aiteam/state?environment=dev").json()
                diagnostics = dict(state_payload.get("startup_diagnostics", {}) or {})
                timings = dict(diagnostics.get("timings_ms", {}) or {})
                self.assertEqual(str(diagnostics.get("workspace", "")), str(workspace))
                self.assertIn("total_ms", timings)
                self.assertIn("orchestrator_init_ms", timings)
                state_verdict = ((state_payload.get("last_chat_run", {}) or {}).get("run_verdict", {}) or {})
                self.assertEqual(str(state_verdict.get("state", "")), "rejected")
                self.assertEqual(str(state_verdict.get("result", "")), "fallido")
                self.assertTrue(bool(state_verdict.get("semantic_gate_applied", False)))
                self.assertIn(
                    "qa:blocked_status",
                    list(state_verdict.get("reason_codes", []) or []),
                )
                state_contracts = ((state_payload.get("last_chat_run", {}) or {}).get("phase_contracts", {}) or {})
                self.assertEqual(
                    str(((state_contracts.get("build", {}) or {}).get("role", ""))),
                    "ENGINEER",
                )
                state_phase_verdicts = ((state_payload.get("last_chat_run", {}) or {}).get("phase_verdicts", {}) or {})
                self.assertEqual(
                    str(((state_phase_verdicts.get("review", {}) or {}).get("status", ""))),
                    "rejected",
                )
                self.assertEqual(
                    str(((state_phase_verdicts.get("build", {}) or {}).get("contract_status", ""))),
                    "drift",
                )
                state_close_policy = ((state_payload.get("last_chat_run", {}) or {}).get("lead_close_policy", {}) or {})
                self.assertEqual(
                    str(state_close_policy.get("authoritative_close_state", "")),
                    "rejected",
                )
                self.assertIn(
                    "review_rejected",
                    list(state_close_policy.get("blocking_signals", []) or []),
                )
                state_phase_delivery = list(
                    ((state_payload.get("last_chat_run", {}) or {}).get("phase_delivery_summary", []) or [])
                )
                state_phase_ids = [
                    str((item or {}).get("phase_id", ""))
                    for item in state_phase_delivery
                    if isinstance(item, dict)
                ]
                self.assertEqual(state_phase_ids, ["build", "review", "qa"])
                self.assertEqual(
                    str((state_phase_delivery[0] or {}).get("delivery_summary", "")),
                    "Resumen build: se ejecutó un slice distinto del aprobado.",
                )

                self.assertEqual(
                    str((state_phase_delivery[1] or {}).get("delivery_source", "")),
                    "phase_output",
                )
                self.assertIn(
                    "Review output extenso",
                    str((state_phase_delivery[1] or {}).get("delivery_summary", "")),
                )
                state_summary = ((state_payload.get("last_chat_run", {}) or {}).get("task_operational_summary", {}) or {})
                state_counts = dict(state_summary.get("counts", {}) or {})
                self.assertTrue(bool(state_summary.get("has_authoritative_blockers")))
                self.assertEqual(int(state_counts.get("blocked_by_policy", 0)), 1)
                self.assertEqual(int(state_counts.get("review_rejected", 0)), 1)
                self.assertEqual(int(state_counts.get("qa_blocked", 0)), 1)
                self.assertEqual(int(state_counts.get("slice_drift", 0)), 1)
                self.assertEqual(
                    str(state_summary.get("authoritative_close_state", "")),
                    "rejected",
                )

                conversations_payload = client.get("/api/aiteam/conversations?limit=10").json()
                conv_verdict = ((conversations_payload.get("last_chat_run", {}) or {}).get("run_verdict", {}) or {})
                self.assertEqual(str(conv_verdict.get("state", "")), "rejected")
                self.assertIn(
                    "semantic_gate_failed",
                    list(conv_verdict.get("policy_signals", []) or []),
                )
                conv_close_policy = ((conversations_payload.get("last_chat_run", {}) or {}).get("lead_close_policy", {}) or {})
                self.assertEqual(
                    str(conv_close_policy.get("authoritative_close_state", "")),
                    "rejected",
                )
                conv_contracts = ((conversations_payload.get("last_chat_run", {}) or {}).get("phase_contracts", {}) or {})
                self.assertIn("build", conv_contracts)
                conv_phase_verdicts = ((conversations_payload.get("last_chat_run", {}) or {}).get("phase_verdicts", {}) or {})
                self.assertEqual(
                    str(((conv_phase_verdicts.get("qa", {}) or {}).get("status", ""))),
                    "blocked",
                )
                conv_phase_delivery = list(
                    ((conversations_payload.get("last_chat_run", {}) or {}).get("phase_delivery_summary", []) or [])
                )
                self.assertEqual(len(conv_phase_delivery), 3)
                self.assertEqual(str((conv_phase_delivery[1] or {}).get("phase_id", "")), "review")
                conv_summary = ((conversations_payload.get("last_chat_run", {}) or {}).get("task_operational_summary", {}) or {})
                self.assertTrue(bool(conv_summary.get("has_authoritative_blockers")))
                conv_authoritative_reasons = {
                    str(item.get("reason_code", ""))
                    for item in list(conv_summary.get("authoritative_blockers", []) or [])
                    if isinstance(item, dict)
                }
                self.assertIn("review_rejected", conv_authoritative_reasons)
                self.assertIn("qa_blocked", conv_authoritative_reasons)
                self.assertIn("slice_drift", conv_authoritative_reasons)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_state_lite_bootstrap_endpoint_returns_fast_minimal_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "events.jsonl").write_text("", encoding="utf-8")
            (runtime_dir / "mailbox.jsonl").write_text("", encoding="utf-8")
            SqliteStore(runtime_dir / "aiteam.db").save_all_tasks([])

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                payload = client.get("/api/aiteam/state-lite?environment=dev").json()
                self.assertEqual(str(payload.get("workspace", "")), str(workspace))
                self.assertTrue(bool(payload.get("runtime_exists")))
                diagnostics = dict(payload.get("startup_diagnostics", {}) or {})
                self.assertTrue(bool(diagnostics.get("lite")))
                timings = dict(diagnostics.get("timings_ms", {}) or {})
                self.assertIn("total_ms", timings)
                self.assertIn("last_chat_run", payload)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_progress_forces_terminal_state_when_run_is_stalled_without_runnable_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            task_root = "CHAT-57A11ED1"
            (runtime_dir / "events.jsonl").write_text("", encoding="utf-8")

            self._write_runtime_tasks_sqlite(
                runtime_dir,
                [
                    {
                        "task_id": f"{task_root}::lead_intake",
                        "title": "Lead intake",
                        "description": "desc",
                        "role": "team_lead",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"result": "Plan preliminar listo."},
                    },
                    {
                        "task_id": f"{task_root}::plan_research",
                        "title": "Plan research",
                        "description": "desc",
                        "role": "researcher",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "research-1",
                        "metadata": {"result": "Hallazgos base confirmados."},
                    },
                    {
                        "task_id": f"{task_root}::build",
                        "title": "Build",
                        "description": "desc",
                        "role": "engineer",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "blocked",
                        "assignee": "eng-1",
                        "metadata": {"blocked_reason": "missing_execution_plan_required"},
                    },
                    {
                        "task_id": f"{task_root}::review",
                        "title": "Review",
                        "description": "desc",
                        "role": "reviewer",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "blocked",
                        "assignee": "review-1",
                        "metadata": {"blocked_reason": "upstream_blocked"},
                    },
                    {
                        "task_id": f"{task_root}::qa",
                        "title": "QA",
                        "description": "desc",
                        "role": "qa",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "blocked",
                        "assignee": "qa-1",
                        "metadata": {"blocked_reason": "upstream_blocked"},
                    },
                ],
            )
            self._write_runtime_workflow_sqlite(
                runtime_dir,
                {
                    task_root: {
                        "run_status": "in_progress",
                        "phase_states": {
                            "lead_intake": "completed",
                            "plan_research": "completed",
                            "build": "blocked",
                            "review": "blocked",
                            "qa": "blocked",
                            "lead_close": "blocked",
                        },
                        "run_verdict": {
                            "state": "in_progress",
                            "result": "parcial",
                            "evidence_gate_applied": True,
                            "evidence_gate_failures": ["build:not_completed"],
                            "pending_phases": ["build", "review", "qa", "lead_close"],
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        },
                    }
                },
            )

            progress = api_main._build_chat_progress(runtime_dir, task_root)
            self.assertEqual(progress.task_id, task_root)
            self.assertIn(progress.state, {"failed", "rejected"})

    def test_chat_progress_does_not_mark_completed_while_run_still_has_active_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            task_root = "CHAT-6A77F211"
            (runtime_dir / "events.jsonl").write_text("", encoding="utf-8")

            self._write_runtime_tasks_sqlite(
                runtime_dir,
                [
                    {
                        "task_id": f"{task_root}::lead_intake",
                        "title": "Lead intake",
                        "description": "desc",
                        "role": "team_lead",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"result": "Planning accepted."},
                    },
                    {
                        "task_id": f"{task_root}::plan_research",
                        "title": "Plan research",
                        "description": "desc",
                        "role": "researcher",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "claimed",
                        "assignee": "research-1",
                        "metadata": {},
                    },
                ],
            )
            self._write_runtime_workflow_sqlite(
                runtime_dir,
                {
                    task_root: {
                        "run_status": "running",
                        "phase_states": {
                            "lead_intake": "completed",
                            "plan_research": "claimed",
                        },
                        "run_verdict": {},
                    }
                },
            )

            progress = api_main._build_chat_progress(runtime_dir, task_root)
            self.assertEqual(progress.task_id, task_root)
            self.assertEqual(progress.state, "running")

    def test_chat_progress_does_not_mark_completed_when_only_initial_tasks_finished_but_run_status_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            task_root = "CHAT-4D0AE999"
            (runtime_dir / "events.jsonl").write_text("", encoding="utf-8")

            self._write_runtime_tasks_sqlite(
                runtime_dir,
                [
                    {
                        "task_id": f"{task_root}::scout_project_state",
                        "title": "Scout project state",
                        "description": "desc",
                        "role": "scout",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"result": "snapshot"},
                    },
                    {
                        "task_id": f"{task_root}::scout_session_history",
                        "title": "Scout session history",
                        "description": "desc",
                        "role": "scout",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"result": "history"},
                    },
                    {
                        "task_id": f"{task_root}::scout_context_curator",
                        "title": "Scout context curator",
                        "description": "desc",
                        "role": "scout",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"result": "curated"},
                    },
                    {
                        "task_id": f"{task_root}::lead_intake",
                        "title": "Lead intake",
                        "description": "desc",
                        "role": "team_lead",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-2",
                        "metadata": {"result": "lead plan"},
                    },
                ],
            )
            self._write_runtime_workflow_sqlite(
                runtime_dir,
                {
                    task_root: {
                        "run_status": "running",
                        "phase_states": {
                            "scout_project_state": "completed",
                            "scout_session_history": "completed",
                            "scout_context_curator": "completed",
                            "lead_intake": "completed",
                        },
                        "run_verdict": {},
                    }
                },
            )

            progress = api_main._build_chat_progress(runtime_dir, task_root)
            self.assertEqual(progress.task_id, task_root)
            self.assertEqual(progress.completed_tasks, 4)
            self.assertEqual(progress.pending_tasks, 0)
            self.assertEqual(progress.state, "running")

    def test_chat_progress_treats_blocked_after_lead_close_as_terminal_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            task_root = "CHAT-438A83A4"
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-04-11T03:34:44+00:00",
                        "event_type": "chat_run_resumed",
                        "payload": {
                            "task_id": task_root,
                            "phase": "lead_close",
                            "final_state": "in_progress",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self._write_runtime_tasks_sqlite(
                runtime_dir,
                [
                    {
                        "task_id": f"{task_root}::lead_intake",
                        "title": "Lead intake",
                        "description": "desc",
                        "role": "team_lead",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"result": "planned"},
                    },
                    {
                        "task_id": f"{task_root}::plan_engineering",
                        "title": "Plan engineering",
                        "description": "desc",
                        "role": "researcher",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "blocked",
                        "assignee": "researcher-1",
                        "metadata": {"blocked_reason": "phase_self_reported_blocked"},
                    },
                    {
                        "task_id": f"{task_root}::build",
                        "title": "Build",
                        "description": "desc",
                        "role": "engineer",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "blocked",
                        "assignee": "engineer-1",
                        "metadata": {"blocked_reason": "dependency_failed"},
                    },
                    {
                        "task_id": f"{task_root}::lead_close",
                        "title": "Lead close",
                        "description": "desc",
                        "role": "team_lead",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"result": "Terminal diagnostic."},
                    },
                ],
            )
            self._write_runtime_workflow_sqlite(
                runtime_dir,
                {
                    task_root: {
                        "run_status": "in_progress",
                        "phase_states": {
                            "lead_intake": "completed",
                            "plan_engineering": "blocked",
                            "build": "blocked",
                            "lead_close": "completed",
                        },
                        "run_verdict": {},
                    }
                },
            )

            progress = api_main._build_chat_progress(runtime_dir, task_root)
            self.assertEqual(progress.task_id, task_root)
            self.assertEqual(progress.state, "failed")

    def test_state_last_chat_run_masks_stale_running_after_lead_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            task_root = "CHAT-438A83A4"
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-04-11T03:28:13+00:00",
                        "event_type": "chat_plan_created",
                        "payload": {
                            "task_id": task_root,
                            "chat_mode": "sprint5",
                            "round_budget": 8,
                            "phase_count": 8,
                            "delegated_count": 1,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self._write_runtime_workflow_sqlite(
                runtime_dir,
                {
                    task_root: {
                        "run_status": "in_progress",
                        "phase_states": {},
                        "run_verdict": {},
                    }
                },
            )
            self._write_runtime_tasks_sqlite(
                runtime_dir,
                [
                    {
                        "task_id": f"{task_root}::lead_intake",
                        "title": "Lead intake",
                        "description": "desc",
                        "role": "team_lead",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"phase": "lead_intake"},
                    },
                    {
                        "task_id": f"{task_root}::plan_engineering",
                        "title": "Plan engineering",
                        "description": "desc",
                        "role": "researcher",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "blocked",
                        "assignee": "researcher-1",
                        "metadata": {"phase": "plan_engineering"},
                    },
                    {
                        "task_id": f"{task_root}::lead_close",
                        "title": "Lead close",
                        "description": "desc",
                        "role": "team_lead",
                        "complexity": "medium",
                        "criticality": "medium",
                        "dependencies": [],
                        "state": "completed",
                        "assignee": "lead-1",
                        "metadata": {"phase": "lead_close"},
                    },
                ],
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                payload = client.get("/api/aiteam/state?environment=dev").json()
                last_chat_run = dict(payload.get("last_chat_run", {}) or {})
                self.assertEqual(str(last_chat_run.get("status", "")), "failed")
                self.assertEqual(
                    str(last_chat_run.get("workflow_run_status", "")),
                    "in_progress",
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_state_last_chat_run_prefers_workflow_run_status_over_completed_or_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            task_root = "CHAT-5C8E1001"
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-04-08T10:00:00+00:00",
                        "event_type": "chat_plan_created",
                        "payload": {
                            "task_id": task_root,
                            "chat_mode": "sprint5",
                            "round_budget": 8,
                            "phase_count": 7,
                            "delegated_count": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self._write_runtime_workflow_sqlite(
                runtime_dir,
                {
                    task_root: {
                        "run_status": "running",
                        "phase_states": {
                            "lead_intake": "completed",
                            "plan_research": "claimed",
                        },
                        "run_verdict": {},
                    }
                },
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                payload = client.get("/api/aiteam/state?environment=dev").json()
                last_chat_run = dict(payload.get("last_chat_run", {}) or {})
                self.assertEqual(str(last_chat_run.get("status", "")), "running")
                self.assertEqual(str(last_chat_run.get("workflow_run_status", "")), "running")
                self.assertNotEqual(str(last_chat_run.get("status", "")), "completed_or_closed")
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_state_operational_summary_uses_phase_verdicts_before_run_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            root_id = "CHAT-BCDE2345"
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-04-03T19:00:00+00:00",
                        "event_type": "chat_plan_created",
                        "payload": {
                            "task_id": root_id,
                            "chat_mode": "sprint5",
                            "round_budget": 5,
                            "phase_count": 3,
                            "delegated_count": 1,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self._write_runtime_workflow_sqlite(
                runtime_dir,
                {
                    root_id: {
                        "phase_verdicts": {
                            "build": {
                                "phase_id": "build",
                                "status": "completed",
                                "contract_status": "drift",
                                "slice_id": "4",
                                "reason_codes": ["slice_drift"],
                            },
                            "review": {
                                "phase_id": "review",
                                "status": "rejected",
                                "reason_codes": ["review_rejected"],
                            },
                            "qa": {
                                "phase_id": "qa",
                                "status": "blocked",
                                "reason_codes": ["qa_blocked"],
                            },
                        }
                    }
                },
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                payload = client.get("/api/aiteam/state?environment=dev").json()
                summary = ((payload.get("last_chat_run", {}) or {}).get("task_operational_summary", {}) or {})
                counts = dict(summary.get("counts", {}) or {})
                self.assertTrue(bool(summary.get("has_authoritative_blockers")))
                self.assertEqual(int(counts.get("review_rejected", 0)), 1)
                self.assertEqual(int(counts.get("qa_blocked", 0)), 1)
                self.assertEqual(int(counts.get("slice_drift", 0)), 1)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_state_last_chat_run_exposes_reconstructed_verdict_and_continuation_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            root_id = "CHAT-CDEF3456"
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-04-03T19:30:00+00:00",
                        "event_type": "chat_plan_created",
                        "payload": {
                            "task_id": root_id,
                            "chat_mode": "sprint5",
                            "round_budget": 4,
                            "phase_count": 3,
                            "delegated_count": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self._write_runtime_workflow_sqlite(
                runtime_dir,
                {
                    root_id: {
                        "continuation_requested": True,
                        "continuation_effective": False,
                        "continuation_block_reason": "prior_run_rejected",
                        "phase_verdicts": {
                            "lead_intake": {
                                "phase_id": "lead_intake",
                                "status": "completed",
                                "slice_id": "2",
                            },
                            "build": {
                                "phase_id": "build",
                                "status": "completed",
                                "contract_status": "drift",
                                "slice_id": "4",
                                "reason_codes": ["slice_drift"],
                            },
                            "review": {
                                "phase_id": "review",
                                "status": "rejected",
                                "reason_codes": ["review_rejected"],
                            },
                            "qa": {
                                "phase_id": "qa",
                                "status": "blocked",
                                "reason_codes": ["qa_blocked"],
                            },
                        },
                    }
                },
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                payload = client.get("/api/aiteam/state?environment=dev").json()
                last_chat_run = dict(payload.get("last_chat_run", {}) or {})
                run_verdict = dict(last_chat_run.get("run_verdict", {}) or {})
                self.assertEqual(str(run_verdict.get("state", "")), "rejected")
                self.assertTrue(bool(run_verdict.get("reconstructed_from_phase_verdicts", False)))
                self.assertTrue(bool(last_chat_run.get("continuation_requested", False)))
                self.assertFalse(bool(last_chat_run.get("continuation_effective", True)))
                self.assertEqual(
                    str(last_chat_run.get("continuation_block_reason", "")),
                    "prior_run_rejected",
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_state_sanitizes_invalid_continuation_placeholder_from_old_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / ".aiteam"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            root_id = "CHAT-A1B2C3D4"
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-04-12T10:00:00+00:00",
                        "event_type": "chat_plan_created",
                        "payload": {
                            "task_id": root_id,
                            "chat_mode": "sprint5",
                            "round_budget": 5,
                            "phase_count": 2,
                            "delegated_count": 0,
                            "continuation_requested": True,
                            "continuation_of": "CHAT-XXXXXXXX",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self._write_runtime_workflow_sqlite(
                runtime_dir,
                {
                    root_id: {
                        "run_status": "rejected",
                        "continuation_requested": True,
                        "continuation_effective": True,
                        "continuation_of": "CHAT-XXXXXXXX",
                        "run_verdict": {
                            "state": "rejected",
                            "reason_codes": ["review:phase_failed"],
                        },
                    }
                },
            )

            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                state_payload = client.get("/api/aiteam/state?environment=dev").json()
                last_chat_run = dict(state_payload.get("last_chat_run", {}) or {})
                self.assertTrue(bool(last_chat_run.get("continuation_requested", False)))
                self.assertEqual(str(last_chat_run.get("continuation_of", "")), "")
                self.assertFalse(bool(last_chat_run.get("continuation_effective", True)))

                progress = api_main._build_chat_progress(runtime_dir, root_id)
                self.assertEqual(progress.continuation_of, "")
                self.assertFalse(progress.continuation_effective)
            finally:
                api_main.set_current_workspace(previous_workspace)


if __name__ == "__main__":
    unittest.main()
