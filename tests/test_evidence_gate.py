"""
tests/test_evidence_gate.py — Unit tests for aiteam/evidence_gate.py

Each function is tested in isolation without importing the orchestrator.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aiteam.evidence_gate import (
    assess_output_quality,
    build_gate_evidence_context,
    detect_conversational_task,
    summarize_git_diff,
    verify_task_evidence,
)
from aiteam.types import Role, WorkTask


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_task(
    task_id: str = "task::build",
    title: str = "Test task",
    description: str = "Do some work",
    role: Role = Role.ENGINEER,
    metadata: dict | None = None,
) -> WorkTask:
    return WorkTask(
        task_id=task_id,
        title=title,
        description=description,
        role=role,
        dependencies=[],
        metadata=metadata or {},
    )


# ── verify_task_evidence ──────────────────────────────────────────────────────


class TestVerifyTaskEvidence:
    def test_simulated_placeholder_blocked(self, tmp_path):
        task = _make_task(metadata={"_last_agent_output": "[SIMULADO | modelo:test:api] respuesta"})
        ok, reason = verify_task_evidence(task, tmp_path, project_root=None, runtime_dir=tmp_path)
        assert not ok
        assert "placeholder" in reason

    def test_simulated_mode_accepted_with_clean_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AITEAM_ENABLE_LIVE_API", "0")
        task = _make_task(metadata={"_last_agent_output": "Here is the implementation."})
        ok, reason = verify_task_evidence(task, tmp_path, project_root=None, runtime_dir=tmp_path)
        assert ok
        assert reason == "simulated_mode_accepted"

    def test_git_diff_detected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AITEAM_ENABLE_LIVE_API", "1")
        task = _make_task(metadata={"_last_agent_output": ""})
        # Patch subprocess to simulate a repo with changes
        porcelain_result = MagicMock(returncode=0, stdout=" M somefile.py\n")
        diff_result = MagicMock(stdout="diff --git a/somefile.py b/somefile.py\n+added line\n")
        with patch("aiteam.evidence_gate.subprocess.run") as mock_run:
            mock_run.side_effect = [porcelain_result, diff_result]
            ok, reason = verify_task_evidence(task, tmp_path, project_root=tmp_path, runtime_dir=tmp_path)
        assert ok
        assert reason == "git_diff_detected"
        assert "git_diff_evidence" in task.metadata

    def test_git_diff_falls_back_to_cached(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AITEAM_ENABLE_LIVE_API", "1")
        task = _make_task(metadata={"_last_agent_output": ""})
        porcelain_result = MagicMock(returncode=0, stdout="M  staged.py\n")
        diff_empty = MagicMock(stdout="")
        diff_cached = MagicMock(stdout="diff --git a/staged.py b/staged.py\n+new line\n")
        with patch("aiteam.evidence_gate.subprocess.run") as mock_run:
            mock_run.side_effect = [porcelain_result, diff_empty, diff_cached]
            ok, reason = verify_task_evidence(task, tmp_path, project_root=tmp_path, runtime_dir=tmp_path)
        assert ok
        assert reason == "git_diff_detected"

    def test_git_diff_uses_safe_utf8_decoding(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AITEAM_ENABLE_LIVE_API", "1")
        task = _make_task(metadata={"_last_agent_output": ""})
        porcelain_result = MagicMock(returncode=0, stdout=" M somefile.py\n")
        diff_result = MagicMock(stdout="diff --git a/somefile.py b/somefile.py\n+added line\n")
        with patch("aiteam.evidence_gate.subprocess.run") as mock_run:
            mock_run.side_effect = [porcelain_result, diff_result]
            ok, reason = verify_task_evidence(
                task,
                tmp_path,
                project_root=tmp_path,
                runtime_dir=tmp_path,
            )
        assert ok
        assert reason == "git_diff_detected"
        for call in mock_run.call_args_list:
            assert call.kwargs.get("encoding") == "utf-8"
            assert call.kwargs.get("errors") == "replace"

    def test_conversational_accepts_any_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AITEAM_ENABLE_LIVE_API", "0")
        task = _make_task(
            metadata={
                "conversational": True,
                "_last_agent_output": "Short answer.",
            }
        )
        with patch("aiteam.evidence_gate.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            ok, reason = verify_task_evidence(task, tmp_path, project_root=None, runtime_dir=tmp_path)
        assert ok
        assert "conversational" in reason

    def test_conversational_persists_long_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AITEAM_ENABLE_LIVE_API", "0")
        long_output = "A" * 500
        task = _make_task(
            task_id="task_persist::conv",
            metadata={"conversational": True, "_last_agent_output": long_output},
        )
        with patch("aiteam.evidence_gate.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            ok, reason = verify_task_evidence(task, tmp_path, project_root=None, runtime_dir=tmp_path)
        assert ok
        assert "conversational" in reason
        # The doc should have been written under runtime_dir/docs/
        assert "doc_evidence" in task.metadata

    def test_strict_gate_fails_without_evidence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AITEAM_ENABLE_LIVE_API", "1")
        task = _make_task(metadata={"_last_agent_output": "done."})
        with patch("aiteam.evidence_gate.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            ok, reason = verify_task_evidence(task, tmp_path, project_root=None, runtime_dir=tmp_path)
        assert not ok
        assert "Strict Evidence Gate" in reason

    def test_live_mode_quality_output_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AITEAM_ENABLE_LIVE_API", "1")
        long_technical_output = "The implementation refactors the router module. " * 10
        task = _make_task(metadata={"_last_agent_output": long_technical_output})
        with patch("aiteam.evidence_gate.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            ok, reason = verify_task_evidence(task, tmp_path, project_root=None, runtime_dir=tmp_path)
        assert ok
        assert "live_output_quality" in reason

    def test_planning_run_mode_requires_structured_markdown(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AITEAM_ENABLE_LIVE_API", "0")
        task = _make_task(
            metadata={
                "conversational": True,
                "run_mode": "architecture_review",
                "_last_agent_output": "Analisis largo pero sin artefacto markdown.",
            }
        )
        with patch("aiteam.evidence_gate.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            ok, reason = verify_task_evidence(
                task,
                tmp_path,
                project_root=None,
                runtime_dir=tmp_path / "runtime",
            )
        assert not ok
        assert reason == "planning_requires_structured_markdown"

    def test_planning_run_mode_accepts_structured_markdown_in_workspace(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("AITEAM_ENABLE_LIVE_API", "0")
        runtime_dir = tmp_path / "runtime"
        runtime_dir.mkdir()
        task = _make_task(
            metadata={
                "conversational": True,
                "run_mode": "roadmap",
                "_last_agent_output": "Roadmap disponible en archivo markdown.",
            }
        )
        doc_path = tmp_path / "ROADMAP.md"
        doc_path.write_text(
            "# Roadmap\n\n"
            "## Prioridades\n"
            "Feature A primero por impacto.\n\n"
            "## Complejidad\n"
            "Alta para migracion, media para UX.\n\n"
            "### Secuencia\n"
            "1. Base de datos\n2. API\n3. UI\n"
            + ("Detalle adicional.\n" * 20),
            encoding="utf-8",
        )
        with patch("aiteam.evidence_gate.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            ok, reason = verify_task_evidence(
                task,
                tmp_path,
                project_root=None,
                runtime_dir=runtime_dir,
            )
        assert ok
        assert reason == "planning_structured_doc:ROADMAP.md"
        assert task.metadata.get("doc_evidence") == str(doc_path)

    def test_planning_run_mode_ignores_runtime_markdown(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AITEAM_ENABLE_LIVE_API", "0")
        runtime_dir = tmp_path / "runtime"
        docs_dir = runtime_dir / "docs"
        docs_dir.mkdir(parents=True)
        task = _make_task(
            metadata={
                "conversational": True,
                "run_mode": "planning_only",
                "_last_agent_output": "Plan persistido solo en runtime.",
            }
        )
        (docs_dir / "plan.md").write_text(
            "# Plan\n\n## Scope\nTexto suficiente.\n\n## Riesgos\n"
            + ("Detalle.\n" * 30),
            encoding="utf-8",
        )
        with patch("aiteam.evidence_gate.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            ok, reason = verify_task_evidence(
                task,
                tmp_path,
                project_root=None,
                runtime_dir=runtime_dir,
            )
        assert not ok
        assert reason == "planning_requires_structured_markdown"


# ── assess_output_quality ─────────────────────────────────────────────────────


class TestAssessOutputQuality:
    def test_empty_output_fails(self):
        ok, reason = assess_output_quality("", Role.ENGINEER, "build")
        assert not ok
        assert reason == "output_vacio"

    def test_placeholder_fails(self):
        ok, reason = assess_output_quality(
            "[modelo:test:api] respuesta simulada", Role.ENGINEER, "build"
        )
        assert not ok
        assert reason == "placeholder_output"

    def test_trivial_short_output_fails(self):
        ok, reason = assess_output_quality("Tarea completada.", Role.ENGINEER, "build")
        assert not ok
        assert reason == "output_trivial_sin_contenido_tecnico"

    def test_substantial_engineer_output_passes(self):
        text = "I refactored the authentication module to use JWT. " * 5
        ok, reason = assess_output_quality(text, Role.ENGINEER, "build")
        assert ok

    def test_very_short_output_fails(self):
        ok, reason = assess_output_quality("OK done", Role.ENGINEER, "build")
        assert not ok
        assert "output_muy_corto" in reason

    def test_reviewer_with_observation_passes(self):
        text = "There is an issue with error handling in line 42. Suggest adding try/except."
        ok, reason = assess_output_quality(text, Role.REVIEWER, "review")
        assert ok
        assert reason == "review_con_observaciones"

    def test_reviewer_without_observations_fails(self):
        # Short text (<80 chars) with no signals → output_muy_corto
        ok, reason = assess_output_quality("Looks good.", Role.REVIEWER, "review")
        assert not ok
        # Medium text (80-300 chars) with no signals → review_sin_observaciones_accionables
        medium = "The implementation looks acceptable and seems to meet the requirements that were specified."
        assert 80 <= len(medium) < 300
        ok2, reason2 = assess_output_quality(medium, Role.REVIEWER, "review")
        assert not ok2
        assert "review_sin_observaciones_accionables" == reason2

    def test_qa_with_test_signal_passes(self):
        text = "All tests passed. Coverage at 87%."
        ok, reason = assess_output_quality(text, Role.QA, "qa")
        assert ok
        assert reason == "qa_con_resultados"

    def test_qa_without_test_signal_fails(self):
        ok, reason = assess_output_quality("Everything seems fine.", Role.QA, "qa")
        assert not ok
        assert "qa" in reason

    def test_qa_substantial_output_passes(self):
        text = "The feature was examined thoroughly across all edge cases. " * 6
        ok, reason = assess_output_quality(text, Role.QA, "qa")
        assert ok
        assert reason == "qa_output_sustancial"

    def test_medium_length_engineer_output_fails(self):
        # Between 80 and 200 chars without being trivial
        text = "I added some new code to the module and updated the config file for this feature."
        assert 80 < len(text) < 200
        ok, reason = assess_output_quality(text, Role.ENGINEER, "build")
        assert not ok
        assert "output_insuficiente_en_live" in reason


# ── summarize_git_diff ────────────────────────────────────────────────────────


class TestSummarizeGitDiff:
    def test_empty_diff(self):
        result = summarize_git_diff("")
        assert "Sin diferencias" in result

    def test_single_file(self):
        raw = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "+added line\n"
            "+another added\n"
            "-removed line\n"
        )
        result = summarize_git_diff(raw)
        assert "1 archivos" in result
        assert "+2/-1" in result
        assert "foo.py" in result

    def test_multiple_files(self):
        raw = ""
        for i in range(10):
            raw += f"diff --git a/file{i}.py b/file{i}.py\n+line\n"
        result = summarize_git_diff(raw)
        assert "10 archivos" in result
        assert "archivos mas" in result

    def test_truncates_at_8_files(self):
        raw = ""
        for i in range(12):
            raw += f"diff --git a/file{i}.py b/file{i}.py\n+line\n"
        result = summarize_git_diff(raw)
        assert "4 archivos mas" in result


# ── detect_conversational_task ────────────────────────────────────────────────


class TestDetectConversationalTask:
    def test_build_phase_not_conversational(self):
        task = _make_task(
            description="Implement the feature",
            metadata={"phase": "build"},
        )
        assert not detect_conversational_task(task)

    def test_question_mark_is_conversational(self):
        task = _make_task(title="What is the best approach?")
        assert detect_conversational_task(task)

    def test_explain_keyword_is_conversational(self):
        task = _make_task(description="explain how the router works")
        assert detect_conversational_task(task)

    def test_regular_task_not_conversational(self):
        task = _make_task(title="Add retry logic to the API client")
        assert not detect_conversational_task(task)

    def test_spanish_keyword_is_conversational(self):
        task = _make_task(description="analiza las opciones de arquitectura")
        assert detect_conversational_task(task)

    def test_review_phase_not_conversational(self):
        task = _make_task(
            description="explain the code changes",  # keyword present but phase overrides
            metadata={"phase": "review"},
        )
        assert not detect_conversational_task(task)


# ── build_gate_evidence_context ───────────────────────────────────────────────


class TestBuildGateEvidenceContext:
    def _make_session_store(self, actions: list) -> MagicMock:
        store = MagicMock()
        store.sessions_for_task.return_value = [{"actions": actions}]
        return store

    def test_empty_task_returns_empty_string(self):
        task = _make_task()
        store = MagicMock()
        store.sessions_for_task.return_value = []
        result = build_gate_evidence_context(task, session_store=store, compact_fn=lambda t, n: t[:n])
        assert result == ""

    def test_includes_exec_actions(self):
        actions = [
            {"action_type": "command_exec", "success": True, "detail": "ran pytest"},
            {"action_type": "llm_call", "success": False, "detail": "timeout"},
        ]
        task = _make_task()
        store = self._make_session_store(actions)
        result = build_gate_evidence_context(task, session_store=store, compact_fn=lambda t, n: t[:n])
        assert "ran pytest" in result
        assert "timeout" in result
        assert "[OK]" in result
        assert "[FAIL]" in result

    def test_includes_git_diff_summary(self):
        raw_diff = "diff --git a/main.py b/main.py\n+added\n"
        task = _make_task(metadata={"git_diff_evidence": raw_diff})
        store = MagicMock()
        store.sessions_for_task.return_value = []
        result = build_gate_evidence_context(task, session_store=store, compact_fn=lambda t, n: t[:n])
        assert "Resumen de cambios" in result
        assert "main.py" in result

    def test_includes_justification(self):
        task = _make_task(metadata={"decision_justification": "I chose this approach because it's simpler."})
        store = MagicMock()
        store.sessions_for_task.return_value = []
        result = build_gate_evidence_context(task, session_store=store, compact_fn=lambda t, n: t[:n])
        assert "Razonamiento del engineer" in result
        assert "simpler" in result

    def test_includes_gate_iteration(self):
        task = _make_task(metadata={"gate_iteration": 1, "review_feedback": "Fix the null check."})
        store = MagicMock()
        store.sessions_for_task.return_value = []
        result = build_gate_evidence_context(task, session_store=store, compact_fn=lambda t, n: t[:n])
        assert "iteracion 2" in result
        assert "null check" in result

    def test_filters_non_exec_actions(self):
        actions = [
            {"action_type": "file_read", "success": True, "detail": "read file"},
            {"action_type": "command_exec", "success": True, "detail": "ran tests"},
        ]
        task = _make_task()
        store = self._make_session_store(actions)
        result = build_gate_evidence_context(task, session_store=store, compact_fn=lambda t, n: t[:n])
        assert "ran tests" in result
        assert "read file" not in result
