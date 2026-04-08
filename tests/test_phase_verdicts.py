"""Tests for aiteam.phase_verdicts — heuristic and structured verdict extraction."""
from __future__ import annotations

import pytest

from aiteam.phase_verdicts import (
    build_phase_verdict_prompt_block,
    coerce_phase_verdicts,
    detect_contract_path_drift,
    detect_continuation_drift,
    derive_run_verdict_from_phase_verdicts,
    extract_path_candidates,
    extract_phase_verdict,
    infer_objective_path_hints,
)


# ── extract_phase_verdict — structured block ──────────────────────────────────

class TestStructuredBlock:
    def test_basic_approved(self):
        text = "[PHASE_VERDICT]\nstatus: approved\n[/PHASE_VERDICT]"
        v = extract_phase_verdict(text, phase_id="build")
        assert v["status"] == "approved"
        assert v["source"] == "structured"

    def test_spanish_status_mapping(self):
        text = "[PHASE_VERDICT]\nstatus: aprobado\nphase_id: build\n[/PHASE_VERDICT]"
        v = extract_phase_verdict(text, phase_id="build")
        assert v["status"] == "approved"

    def test_structured_overrides_heuristic(self):
        # Even though body contains "BLOQUEADA:", structured block wins
        text = (
            "BLOQUEADA: hay un problema.\n"
            "[PHASE_VERDICT]\nstatus: completed\n[/PHASE_VERDICT]"
        )
        v = extract_phase_verdict(text, phase_id="engineer_toc_implementation")
        assert v["status"] == "completed"
        assert v["source"] == "structured"

    def test_inline_verdict(self):
        text = "[PHASE_VERDICT: rejected]"
        v = extract_phase_verdict(text, phase_id="review")
        assert v["status"] == "rejected"

    def test_reason_codes_parsed(self):
        text = "[PHASE_VERDICT]\nstatus: blocked\nreason_codes: engineer_blocked, missing_contract\n[/PHASE_VERDICT]"
        v = extract_phase_verdict(text, phase_id="build")
        assert "engineer_blocked" in v["reason_codes"]
        assert "missing_contract" in v["reason_codes"]

    def test_summary_truncated(self):
        long_summary = "x" * 300
        text = f"[PHASE_VERDICT]\nstatus: approved\nsummary: {long_summary}\n[/PHASE_VERDICT]"
        v = extract_phase_verdict(text, phase_id="review")
        assert len(v["summary"]) <= 240


# ── extract_phase_verdict — heuristic: engineer phases ───────────────────────

class TestEngineerHeuristic:
    """BLOQUEADA: at line start → blocked for any engineer-hint phase."""

    def test_explicit_label_build(self):
        v = extract_phase_verdict("BLOQUEADA: no hay phase contract.", phase_id="build")
        assert v.get("status") == "blocked"
        assert "engineer_blocked" in v.get("reason_codes", [])

    def test_explicit_label_custom_phase(self):
        v = extract_phase_verdict(
            "BLOQUEADA: El PHASE_CONTRACT esta incompleto.",
            phase_id="engineer_toc_implementation",
        )
        assert v.get("status") == "blocked"
        assert "engineer_blocked" in v.get("reason_codes", [])

    def test_implement_phase_hint(self):
        v = extract_phase_verdict("BLOQUEADA: falta contexto.", phase_id="implement_auth")
        assert v.get("status") == "blocked"

    def test_develop_phase_hint(self):
        v = extract_phase_verdict("BLOQUEADO: missing spec.", phase_id="develop_api")
        assert v.get("status") == "blocked"

    def test_evidencegate_phrase(self):
        v = extract_phase_verdict("No se puede ejecutar: evidencegate fallo.", phase_id="build")
        assert v.get("status") == "blocked"

    def test_contextual_bloqueada_no_colon_not_blocked(self):
        # "bloqueada" without colon is contextual prose — must NOT block
        v = extract_phase_verdict(
            "La fase anterior estaba bloqueada por falta de recursos.",
            phase_id="engineer_toc_implementation",
        )
        assert v.get("status") != "blocked"

    def test_non_engineer_phase_not_affected(self):
        # scout is not an engineer-hint phase
        v = extract_phase_verdict("BLOQUEADA: problema.", phase_id="scout_project_state")
        assert v.get("status") != "blocked"


# ── extract_phase_verdict — heuristic: review / qa ───────────────────────────

class TestReviewQaHeuristic:
    def test_review_rejected(self):
        v = extract_phase_verdict(
            "Decisión: rechazado - el código no pasa los criterios.",
            phase_id="review",
        )
        assert v.get("status") == "rejected"
        assert "review_rejected" in v.get("reason_codes", [])

    def test_review_rejected_english(self):
        v = extract_phase_verdict("**Decision**: **rejected** — failed criteria.", phase_id="review")
        assert v.get("status") == "rejected"

    def test_review_changes_requested_is_treated_as_rejected_signal(self):
        v = extract_phase_verdict(
            "Veredicto: CHANGES_REQUESTED — faltan pruebas y coherencia contractual.",
            phase_id="review",
        )
        assert v.get("status") == "rejected"
        assert "review_rejected" in v.get("reason_codes", [])

    def test_qa_blocked_via_estado(self):
        v = extract_phase_verdict(
            "Estado: bloqueado - no hay codigo que validar.",
            phase_id="qa",
        )
        assert v.get("status") == "blocked"
        assert "qa_blocked" in v.get("reason_codes", [])

    def test_qa_blocked_via_verdict_label(self):
        v = extract_phase_verdict(
            "Veredicto: BLOCKED — falta evidencia ejecutable.",
            phase_id="qa",
        )
        assert v.get("status") == "blocked"
        assert "qa_blocked" in v.get("reason_codes", [])

    def test_qa_failed_via_decision_label_is_treated_as_blocking(self):
        v = extract_phase_verdict(
            "Decision: FAILED — regresion abierta en checks criticos.",
            phase_id="qa",
        )
        assert v.get("status") == "blocked"
        assert "qa_blocked" in v.get("reason_codes", [])


# ── role_hint for custom phase names ─────────────────────────────────────────

class TestRoleHint:
    def test_build_role_hint(self):
        v = extract_phase_verdict(
            "[PHASE_VERDICT]\nstatus: approved\n[/PHASE_VERDICT]",
            phase_id="build",
        )
        assert v.get("role_hint") == "engineer"

    def test_engineer_custom_role_hint(self):
        v = extract_phase_verdict(
            "[PHASE_VERDICT]\nstatus: completed\n[/PHASE_VERDICT]",
            phase_id="engineer_toc_implementation",
        )
        assert v.get("role_hint") == "engineer"

    def test_implement_role_hint(self):
        v = extract_phase_verdict(
            "[PHASE_VERDICT]\nstatus: completed\n[/PHASE_VERDICT]",
            phase_id="implement_auth",
        )
        assert v.get("role_hint") == "engineer"

    def test_review_role_hint(self):
        v = extract_phase_verdict(
            "[PHASE_VERDICT]\nstatus: approved\n[/PHASE_VERDICT]",
            phase_id="review",
        )
        assert v.get("role_hint") == "reviewer"

    def test_qa_role_hint(self):
        v = extract_phase_verdict(
            "[PHASE_VERDICT]\nstatus: approved\n[/PHASE_VERDICT]",
            phase_id="qa",
        )
        assert v.get("role_hint") == "qa"


# ── continuation drift heuristics ────────────────────────────────────────────

class TestContinuationDrift:
    def test_extract_path_candidates_prefers_code_block_paths(self):
        text = (
            "Voy a cambiar `README.md`.\n"
            "```python path=src/sample_cli/generator.py\nprint('x')\n```"
        )
        candidates = extract_path_candidates(text)
        assert "readme.md" in candidates
        assert "src/sample_cli/generator.py" in candidates

    def test_infer_objective_path_hints_for_readme_and_tests(self):
        hints = infer_objective_path_hints("Actualizar README.md y tests del CLI.")
        assert "readme.md" in hints
        assert "tests" in hints

    def test_detect_continuation_drift_when_paths_leave_objective_scope(self):
        drift = detect_continuation_drift(
            objective="Actualizar README.md y tests del CLI.",
            proposed_paths=["src/sample_cli/generator.py"],
        )
        assert drift["contract_status"] == "drift"
        assert "slice_drift" in drift["reason_codes"]
        assert "continuation_drift" in drift["reason_codes"]
        assert "src/sample_cli/generator.py" in drift["proposed_paths"]

    def test_detect_continuation_drift_accepts_matching_scope(self):
        drift = detect_continuation_drift(
            objective="Actualizar README.md y tests del CLI.",
            proposed_paths=["README.md", "tests/test_cli.py"],
        )
        assert drift == {}

    def test_detect_continuation_drift_ignores_generic_objective(self):
        drift = detect_continuation_drift(
            objective="Implementa el cambio aprobado.",
            proposed_paths=["src/sample_cli/generator.py"],
        )
        assert drift == {}

    def test_detect_contract_path_drift_is_generic_for_any_src_layout_package(self):
        drift = detect_contract_path_drift(
            proposed_paths=["src/acme_cli/report_builder.py"],
            allowed_module_path_hints=["cli.py", "styles.py"],
        )
        assert drift["contract_status"] == "drift"
        assert "forbidden_module_scope" in drift["reason_codes"]
        assert "src/acme_cli/report_builder.py" in drift["proposed_paths"]


# ── empty / degenerate inputs ────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_text_returns_empty(self):
        assert extract_phase_verdict("", phase_id="build") == {}

    def test_empty_phase_id_returns_empty(self):
        assert extract_phase_verdict("some text", phase_id="") == {}

    def test_no_signal_returns_empty(self):
        # Text with no structural block markers
        assert extract_phase_verdict("Todo bien, continuar.", phase_id="build") == {}


# ── coerce_phase_verdicts ────────────────────────────────────────────────────

class TestCoercePhaseVerdicts:
    def test_normalises_spanish_status(self):
        result = coerce_phase_verdicts({"build": {"status": "aprobado"}})
        assert result["build"]["status"] == "approved"

    def test_skips_non_dict_entries(self):
        result = coerce_phase_verdicts({"build": "not a dict", "qa": {"status": "blocked"}})
        assert "build" not in result
        assert result["qa"]["status"] == "blocked"

    def test_non_dict_payload_returns_empty(self):
        assert coerce_phase_verdicts(None) == {}
        assert coerce_phase_verdicts("string") == {}


# ── derive_run_verdict_from_phase_verdicts ───────────────────────────────────

class TestDeriveRunVerdict:
    def test_no_failures_returns_empty(self):
        result = derive_run_verdict_from_phase_verdicts(
            {"build": {"status": "completed"}, "qa": {"status": "approved"}}
        )
        assert result == {}

    def test_review_rejected_triggers_verdict(self):
        result = derive_run_verdict_from_phase_verdicts(
            {"review": {"status": "rejected"}}
        )
        assert result["state"] == "rejected"
        assert any("review" in rc for rc in result["reason_codes"])

    def test_qa_blocked_triggers_verdict(self):
        result = derive_run_verdict_from_phase_verdicts(
            {"qa": {"status": "blocked"}}
        )
        assert result["state"] == "rejected"

    def test_custom_review_and_qa_gate_verdicts_trigger_semantic_rejection(self):
        result = derive_run_verdict_from_phase_verdicts(
            {
                "review_slice2_code": {
                    "status": "rejected",
                    "role_hint": "reviewer",
                },
                "qa_slice2_validation": {
                    "status": "blocked",
                    "role_hint": "qa",
                },
            }
        )
        assert result["state"] == "rejected"
        assert "review:rejected_decision" in result["semantic_gate_failures"]
        assert "qa:blocked_status" in result["semantic_gate_failures"]

    def test_custom_engineer_gate_drift_triggers_build_slice_drift(self):
        result = derive_run_verdict_from_phase_verdicts(
            {
                "lead_intake": {"slice_id": "2"},
                "engineer_css_integration": {
                    "status": "completed",
                    "role_hint": "engineer",
                    "contract_status": "drift",
                    "reason_codes": ["slice_drift"],
                    "slice_id": "unknown",
                },
            }
        )
        assert result["state"] == "rejected"
        assert any(
            item.startswith("build:slice_drift:2->")
            for item in result["semantic_gate_failures"]
        )

    def test_custom_plan_phase_slice_id_feeds_build_slice_drift(self):
        result = derive_run_verdict_from_phase_verdicts(
            {
                "plan_engineering_retry": {"slice_id": "2"},
                "engineer_css_integration": {
                    "status": "completed",
                    "role_hint": "engineer",
                    "contract_status": "drift",
                    "reason_codes": ["slice_drift"],
                    "slice_id": "unknown",
                },
            }
        )
        assert result["state"] == "rejected"
        assert any(
            item.startswith("build:slice_drift:2->")
            for item in result["semantic_gate_failures"]
        )

    def test_empty_verdicts_returns_empty(self):
        assert derive_run_verdict_from_phase_verdicts({}) == {}


# ── build_phase_verdict_prompt_block ─────────────────────────────────────────

class TestPromptBlock:
    def test_reviewer_status_options(self):
        block = build_phase_verdict_prompt_block(phase_id="review", role="REVIEWER")
        assert "approved|rejected|blocked|unknown" in block
        assert "[PHASE_VERDICT]" in block

    def test_engineer_status_options(self):
        block = build_phase_verdict_prompt_block(phase_id="build", role="ENGINEER")
        assert "completed|approved|blocked|rejected|partial|unknown" in block

    def test_empty_phase_id_returns_empty(self):
        assert build_phase_verdict_prompt_block(phase_id="", role="QA") == ""
