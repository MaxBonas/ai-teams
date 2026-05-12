"""Tests for workspace file injection into executor wake payloads.

Covers:
- _read_workspace_files: content injection for reviewer/QA roles
- _list_workspace_files: listing injection for engineer continuation runs
- RunExecutor: correct injection per role/wake_reason
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aiteam.adapters.registry import (
    AdapterDescriptor,
    AdapterRegistry,
    ExecutionResult,
)
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import (
    RunExecutor,
    _list_workspace_files,
    _read_workspace_files,
)
from aiteam.heartbeat.scheduler import HeartbeatScheduler


# ── Helpers ──────────────────────────────────────────────────────────────────

def _init_db(db_path: Path, *, role: str = "engineer") -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type, budget_monthly_cents)"
            " VALUES (?, ?, ?, ?, ?)",
            (f"agent-{role}", role, role.title(), "static_ok", 0),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("issue-1", "goal-1", "Task", "todo", role, f"agent-{role}"),
        )
        conn.commit()


def _write_project_files(project_root: Path) -> None:
    """Create a small set of workspace files for injection tests."""
    src = project_root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (src / "utils.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (project_root / "README.md").write_text("# Project\n", encoding="utf-8")


class _CapturingRuntime:
    """Adapter that stores the wake_context it was called with."""

    descriptor = AdapterDescriptor(adapter_type="static_ok", channel="subscription")

    def __init__(self) -> None:
        self.captured_context: dict[str, object] | None = None

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        self.captured_context = wake_context
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(status="completed", output="captured")


def _make_registry(runtime: _CapturingRuntime) -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(runtime)
    return registry


def _dispatch(db_path: Path, *, agent_id: str, wake_reason: str = "new_issue") -> Any:
    enqueue_wakeup(
        db_path,
        agent_id=agent_id,
        source="assignment",
        reason=wake_reason,
        payload={"issue_id": "issue-1", "wake_reason": wake_reason},
    )
    return HeartbeatScheduler(db_path).dispatch_next(agent_id=agent_id)


# ── _read_workspace_files ─────────────────────────────────────────────────────

class TestReadWorkspaceFiles:
    def test_returns_files_with_content(self, tmp_path: Path) -> None:
        _write_project_files(tmp_path)
        result = _read_workspace_files(tmp_path)
        paths = [f["path"] for f in result]
        assert "README.md" in paths
        assert "src/main.py" in paths
        assert "src/utils.py" in paths
        for f in result:
            assert "content" in f
            assert "size_bytes" in f
            assert len(f["content"]) > 0

    def test_skips_hidden_files(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
        (tmp_path / "visible.py").write_text("x = 1\n", encoding="utf-8")
        result = _read_workspace_files(tmp_path)
        paths = [f["path"] for f in result]
        assert ".env" not in paths
        assert "visible.py" in paths

    def test_skips_noisy_directories(self, tmp_path: Path) -> None:
        for skip_dir in ("node_modules", "__pycache__", ".git", ".venv", "dist"):
            d = tmp_path / skip_dir
            d.mkdir()
            (d / "file.txt").write_text("noise\n", encoding="utf-8")
        (tmp_path / "real.py").write_text("x = 1\n", encoding="utf-8")
        result = _read_workspace_files(tmp_path)
        paths = [f["path"] for f in result]
        assert paths == ["real.py"], f"Expected only real.py, got {paths}"

    def test_skips_binary_files(self, tmp_path: Path) -> None:
        # Bytes 0-8 are "non-text" per the heuristic (b < 9).
        # Fill 512 bytes entirely with null bytes → 100% non-text → well above 15% threshold.
        binary = bytes([0]) * 512
        (tmp_path / "image.bin").write_bytes(binary)
        (tmp_path / "text.py").write_text("print(1)\n", encoding="utf-8")
        result = _read_workspace_files(tmp_path)
        paths = [f["path"] for f in result]
        assert "image.bin" not in paths
        assert "text.py" in paths

    def test_truncates_large_files(self, tmp_path: Path) -> None:
        big = "x" * 20_000
        (tmp_path / "big.txt").write_text(big, encoding="utf-8")
        result = _read_workspace_files(tmp_path, max_per_file_bytes=8192)
        assert len(result) == 1
        assert "[truncated" in result[0]["content"]

    def test_respects_total_byte_limit(self, tmp_path: Path) -> None:
        for i in range(20):
            (tmp_path / f"file{i:02d}.txt").write_text("a" * 2000, encoding="utf-8")
        result = _read_workspace_files(tmp_path, max_per_file_bytes=2000, max_total_bytes=8000)
        # Should stop after ~4 files (4 * 2000 = 8000 bytes)
        assert len(result) <= 5

    def test_empty_directory(self, tmp_path: Path) -> None:
        assert _read_workspace_files(tmp_path) == []

    def test_results_sorted_by_path(self, tmp_path: Path) -> None:
        (tmp_path / "z.py").write_text("z\n", encoding="utf-8")
        (tmp_path / "a.py").write_text("a\n", encoding="utf-8")
        (tmp_path / "m.py").write_text("m\n", encoding="utf-8")
        result = _read_workspace_files(tmp_path)
        paths = [f["path"] for f in result]
        assert paths == sorted(paths)


# ── _list_workspace_files ─────────────────────────────────────────────────────

class TestListWorkspaceFiles:
    def test_returns_path_and_size_only(self, tmp_path: Path) -> None:
        _write_project_files(tmp_path)
        result = _list_workspace_files(tmp_path)
        for entry in result:
            assert "path" in entry
            assert "size_bytes" in entry
            assert "content" not in entry  # listing only, no content

    def test_includes_expected_files(self, tmp_path: Path) -> None:
        _write_project_files(tmp_path)
        result = _list_workspace_files(tmp_path)
        paths = [e["path"] for e in result]
        assert "README.md" in paths
        assert "src/main.py" in paths

    def test_skips_hidden_and_noisy(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "pkg.js").write_text("module.exports={}\n", encoding="utf-8")
        (tmp_path / "app.py").write_text("pass\n", encoding="utf-8")
        result = _list_workspace_files(tmp_path)
        paths = [e["path"] for e in result]
        assert ".gitignore" not in paths
        assert "node_modules/pkg.js" not in paths
        assert "app.py" in paths

    def test_empty_directory(self, tmp_path: Path) -> None:
        assert _list_workspace_files(tmp_path) == []

    def test_results_sorted_by_path(self, tmp_path: Path) -> None:
        for name in ("z.txt", "a.txt", "m.txt"):
            (tmp_path / name).write_text("x\n", encoding="utf-8")
        result = _list_workspace_files(tmp_path)
        paths = [e["path"] for e in result]
        assert paths == sorted(paths)


# ── Executor injection ────────────────────────────────────────────────────────

class TestExecutorWorkspaceInjection:
    """Verify the executor injects workspace_files / workspace_listing
    into the wake payload under the right conditions."""

    def _run_and_capture(
        self,
        tmp_path: Path,
        *,
        role: str,
        wake_reason: str = "new_issue",
    ) -> dict[str, object] | None:
        # Project root is the tmp_path parent; db lives in tmp_path/.aiteam/
        project_root = tmp_path
        aiteam_dir = project_root / ".aiteam"
        aiteam_dir.mkdir(parents=True, exist_ok=True)
        db_path = aiteam_dir / "aiteam.db"

        _init_db(db_path, role=role)
        _write_project_files(project_root)

        runtime = _CapturingRuntime()
        executor = RunExecutor(db_path, _make_registry(runtime))
        dispatch = _dispatch(db_path, agent_id=f"agent-{role}", wake_reason=wake_reason)
        executor.execute(dispatch)
        return runtime.captured_context

    def test_reviewer_gets_workspace_files(self, tmp_path: Path) -> None:
        ctx = self._run_and_capture(tmp_path, role="reviewer")
        assert ctx is not None
        payload = json.loads(str(ctx.get("wake_payload_json") or "{}"))
        ws_files = payload.get("workspace_files")
        assert ws_files is not None, "reviewer should receive workspace_files"
        assert isinstance(ws_files, list)
        assert len(ws_files) > 0
        # Each entry must have path + content
        for f in ws_files:
            assert "path" in f
            assert "content" in f

    def test_test_runner_gets_workspace_files(self, tmp_path: Path) -> None:
        """test_runner (Tier 3) must receive workspace_files so it can see test targets."""
        ctx = self._run_and_capture(tmp_path, role="test_runner")
        assert ctx is not None
        payload = json.loads(str(ctx.get("wake_payload_json") or "{}"))
        assert payload.get("workspace_files") is not None, "test_runner should receive workspace_files"

    def test_engineer_gets_workspace_files_on_new_issue(self, tmp_path: Path) -> None:
        # Engineers now receive workspace_files on ALL wakes (including new_issue) so
        # they can inspect existing files without blocking to ask the Lead for content.
        # This prevents the "I need file contents" blocking pattern that was bypassing
        # the communication chain and surfacing raw questions to the user.
        ctx = self._run_and_capture(tmp_path, role="engineer", wake_reason="new_issue")
        assert ctx is not None
        payload = json.loads(str(ctx.get("wake_payload_json") or "{}"))
        ws_files = payload.get("workspace_files")
        # The test workspace has files (written by _write_project_files), so the
        # engineer should receive them.
        assert ws_files is not None, (
            "engineer should receive workspace_files when files exist (prevents blocking)"
        )
        assert isinstance(ws_files, list)
        assert len(ws_files) > 0

    def test_engineer_gets_workspace_files_on_continuation(self, tmp_path: Path) -> None:
        # On liveness_continuation engineers receive workspace_files (full content),
        # which supersedes the older workspace_listing (path+size only).
        # workspace_listing is omitted when workspace_files is already present to
        # avoid inflating the payload with redundant data.
        ctx = self._run_and_capture(
            tmp_path, role="engineer", wake_reason="liveness_continuation"
        )
        assert ctx is not None
        payload = json.loads(str(ctx.get("wake_payload_json") or "{}"))
        ws_files = payload.get("workspace_files")
        assert ws_files is not None, "engineer continuation should receive workspace_files"
        assert isinstance(ws_files, list)
        assert len(ws_files) > 0
        for entry in ws_files:
            assert "path" in entry
            assert "content" in entry   # full content, not just a listing
            assert "size_bytes" in entry
        # workspace_listing is skipped when workspace_files is already present
        assert "workspace_listing" not in payload, (
            "workspace_listing should not be injected when workspace_files is already present"
        )

    def test_engineer_does_not_get_workspace_listing_on_new_issue(self, tmp_path: Path) -> None:
        ctx = self._run_and_capture(tmp_path, role="engineer", wake_reason="new_issue")
        assert ctx is not None
        payload = json.loads(str(ctx.get("wake_payload_json") or "{}"))
        assert "workspace_listing" not in payload, (
            "workspace_listing should only be injected as fallback when workspace_files is absent"
        )

    def test_reviewer_workspace_files_contain_correct_paths(self, tmp_path: Path) -> None:
        ctx = self._run_and_capture(tmp_path, role="reviewer")
        assert ctx is not None
        payload = json.loads(str(ctx.get("wake_payload_json") or "{}"))
        ws_files = payload.get("workspace_files", [])
        paths = {f["path"] for f in ws_files}
        assert "README.md" in paths
        assert "src/main.py" in paths

    def test_file_scout_gets_workspace_files(self, tmp_path: Path) -> None:
        """file_scout must receive workspace_files — that is its sole input."""
        ctx = self._run_and_capture(tmp_path, role="file_scout")
        assert ctx is not None
        payload = json.loads(str(ctx.get("wake_payload_json") or "{}"))
        ws_files = payload.get("workspace_files")
        assert ws_files is not None, "file_scout should receive workspace_files"
        assert isinstance(ws_files, list)
        assert len(ws_files) > 0

    def test_no_injection_when_workspace_is_empty(self, tmp_path: Path) -> None:
        """If the workspace has no files, neither field should appear in the payload."""
        project_root = tmp_path
        aiteam_dir = project_root / ".aiteam"
        aiteam_dir.mkdir(parents=True, exist_ok=True)
        db_path = aiteam_dir / "aiteam.db"
        _init_db(db_path, role="reviewer")
        # Do NOT write any workspace files

        runtime = _CapturingRuntime()
        executor = RunExecutor(db_path, _make_registry(runtime))
        dispatch = _dispatch(db_path, agent_id="agent-reviewer")
        executor.execute(dispatch)

        assert runtime.captured_context is not None
        payload = json.loads(str(runtime.captured_context.get("wake_payload_json") or "{}"))
        # With empty workspace, the injection should be skipped entirely
        assert "workspace_files" not in payload or payload["workspace_files"] == []
