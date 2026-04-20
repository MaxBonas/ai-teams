"""Tests for aiteam.lead_close_policy — derive_lead_close_policy and helpers."""
from __future__ import annotations

import pytest

from aiteam.lead_close_policy import (
    build_lead_close_policy_prompt_block,
    derive_lead_close_policy,
)


# ── Basic states ──────────────────────────────────────────────────────────────

class TestBasicStates:
    def test_empty_inputs_eligible_for_done(self):
        p = derive_lead_close_policy(phase_verdicts={}, phase_states={}, run_verdict={})
        assert p["authoritative_close_state"] == "eligible_for_done"
        assert p["can_declare_done"] is True
        assert p["requires_close_rewrite"] is False

    def test_completed_run_verdict_eligible(self):
        p = derive_lead_close_policy(
            phase_verdicts={}, run_verdict={"state": "completed"}
        )
        assert p["authoritative_close_state"] == "eligible_for_done"


# ── Review / QA / build — hardcoded checks ───────────────────────────────────

class TestHardcodedPhaseChecks:
    def test_qa_blocked_gives_rejected(self):
        p = derive_lead_close_policy(phase_verdicts={"qa": {"status": "blocked"}})
        assert p["authoritative_close_state"] == "rejected"
        assert "qa_blocked" in p["blocking_signals"]

    def test_review_rejected_gives_rejected(self):
        p = derive_lead_close_policy(phase_verdicts={"review": {"status": "rejected"}})
        assert p["authoritative_close_state"] == "rejected"
        assert "review_rejected" in p["blocking_signals"]
        assert p["recommended_close_action"] == "rework_from_review_findings"

    def test_review_blocked_gives_rejected(self):
        p = derive_lead_close_policy(
            phase_verdicts={"review": {"status": "blocked", "reason_codes": ["review_blocked"]}}
        )
        assert p["authoritative_close_state"] == "rejected"
        assert "review_blocked" in p["blocking_signals"]

    def test_build_slice_drift_gives_rejected(self):
        p = derive_lead_close_policy(
            phase_verdicts={"build": {"contract_status": "drift"}}
        )
        assert p["authoritative_close_state"] == "rejected"
        assert "slice_drift" in p["blocking_signals"]

    def test_solo_lead_direct_completed_slice_is_not_rejected_as_drift(self):
        p = derive_lead_close_policy(
            phase_verdicts={
                "build": {
                    "status": "completed",
                    "contract_status": "drift",
                    "reason_codes": ["direct_implementation", "minimal_viable_slice"],
                }
            },
            run_verdict={"state": "completed", "run_profile": "solo_lead"},
        )
        assert p["authoritative_close_state"] == "eligible_for_done"
        assert "slice_drift" not in p["blocking_signals"]

    def test_qa_via_reason_code(self):
        p = derive_lead_close_policy(
            phase_verdicts={"qa": {"status": "approved", "reason_codes": ["qa_blocked"]}}
        )
        assert p["authoritative_close_state"] == "rejected"

    def test_custom_gate_phase_verdicts_are_promoted_to_primary_semantic_signals(self):
        p = derive_lead_close_policy(
            phase_verdicts={
                "engineer_css_integration": {
                    "status": "completed",
                    "role_hint": "engineer",
                    "contract_status": "drift",
                    "reason_codes": ["slice_drift"],
                },
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
        assert p["authoritative_close_state"] == "rejected"
        assert p["primary_blocking_signals"][:3] == [
            "qa_blocked",
            "review_rejected",
            "slice_drift",
        ]

    def test_invalid_explicit_qa_alias_is_ignored_when_it_points_to_engineer_phase(self):
        p = derive_lead_close_policy(
            phase_verdicts={
                "qa": {
                    "phase_id": "engineer_tests",
                    "status": "completed",
                    "role_hint": "engineer",
                },
                "review_code": {
                    "phase_id": "review_code",
                    "status": "blocked",
                    "role_hint": "reviewer",
                },
            }
        )
        assert p["authoritative_close_state"] == "rejected"
        assert any("review_code" in item for item in p["blocking_signals"])


# ── Custom engineer phase verdicts (RC-3 / sweep step) ───────────────────────

class TestCustomEngineerPhaseVerdicts:
    def test_advisory_context_research_phase_is_not_treated_as_blocking_signal(self):
        p = derive_lead_close_policy(
            phase_verdicts={
                "research_current_toc_state": {
                    "status": "failed",
                    "role_hint": "researcher",
                }
            }
        )
        assert p["authoritative_close_state"] == "eligible_for_done"

    def test_advisory_plan_research_phase_is_not_treated_as_blocking_signal(self):
        p = derive_lead_close_policy(
            phase_verdicts={
                "plan_research": {
                    "status": "failed",
                    "role_hint": "researcher",
                }
            }
        )
        assert p["authoritative_close_state"] == "eligible_for_done"

    def test_engineer_custom_blocked_verdict_not_completed(self):
        p = derive_lead_close_policy(
            phase_verdicts={"engineer_toc_implementation": {"status": "blocked"}}
        )
        assert p["authoritative_close_state"] == "not_completed"
        assert any("engineer_toc_implementation" in s for s in p["blocking_signals"])

    def test_engineer_failed_verdict_not_completed(self):
        p = derive_lead_close_policy(
            phase_verdicts={"engineer_auth": {"status": "failed"}}
        )
        assert p["authoritative_close_state"] == "not_completed"

    def test_lead_intake_verdict_skipped(self):
        # lead_intake is in _SKIP_PHASES — should not trigger blocking
        p = derive_lead_close_policy(
            phase_verdicts={"lead_intake": {"status": "blocked"}}
        )
        assert p["authoritative_close_state"] == "eligible_for_done"

    def test_lead_close_verdict_skipped(self):
        p = derive_lead_close_policy(
            phase_verdicts={"lead_close": {"status": "failed"}}
        )
        assert p["authoritative_close_state"] == "eligible_for_done"


# ── Phase outputs fallback (belt-and-suspenders) ─────────────────────────────

class TestPhaseOutputsFallback:
    def test_engineer_output_bloqueada_not_completed(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            phase_outputs={"engineer_toc_implementation": "BLOQUEADA: no hay phase contract"},
        )
        assert p["authoritative_close_state"] == "not_completed"
        assert any("engineer_toc_implementation" in s for s in p["blocking_signals"])

    def test_build_output_bloqueado_not_completed(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            phase_outputs={"build": "BLOQUEADO: falta contexto del slice"},
        )
        assert p["authoritative_close_state"] == "not_completed"

    def test_scout_output_not_false_positive(self):
        # Scout output describing a past block should NOT trigger blocking
        p = derive_lead_close_policy(
            phase_verdicts={},
            phase_outputs={"scout_project_state": "La fase estaba bloqueada en runs previas."},
        )
        assert p["authoritative_close_state"] == "eligible_for_done"

    def test_contextual_bloqueada_no_colon_not_blocked(self):
        # "bloqueada" without colon inside engineer output is contextual prose
        p = derive_lead_close_policy(
            phase_verdicts={},
            phase_outputs={
                "engineer_toc_implementation": "La fase anterior estaba bloqueada por recursos."
            },
        )
        assert p["authoritative_close_state"] == "eligible_for_done"

    def test_lead_intake_output_skipped(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            phase_outputs={"lead_intake": "BLOQUEADA: falta todo"},
        )
        assert p["authoritative_close_state"] == "eligible_for_done"

    def test_evidencegate_in_build_output(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            phase_outputs={"build": "No se puede ejecutar: evidencegate fallo"},
        )
        assert p["authoritative_close_state"] == "not_completed"


# ── Policy signals path (observability / post-run) ───────────────────────────

class TestPolicySignals:
    def test_preplanning_support_failure_origin_short_circuits_close_policy(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            run_verdict={
                "state": "failed",
                "failure_origin": "preplanning_support",
                "reason_codes": [
                    "phase_failed:scout_session_history",
                    "phase_failed:scout_context_curator",
                    "lead_intake:blocked_by_support_context",
                ],
            },
        )
        assert p["authoritative_close_state"] == "not_completed"
        assert p["primary_reason"] == "preplanning_support_failure"
        assert p["blocking_signals"] == [
            "lead_intake:blocked_by_support_context",
            "phase_failed:scout_context_curator",
            "phase_failed:scout_session_history",
        ]

    def test_evidence_gate_failed_signal(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            run_verdict={"state": "completed", "policy_signals": ["evidence_gate_failed"]},
        )
        assert p["authoritative_close_state"] == "not_completed"
        assert "evidence_gate_failed" in p["blocking_signals"]

    def test_semantic_gate_failed_signal(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            run_verdict={"policy_signals": ["semantic_gate_failed"]},
        )
        assert p["authoritative_close_state"] == "not_completed"

    def test_unknown_policy_signal_ignored(self):
        # Signals not in the known set should not affect the result
        p = derive_lead_close_policy(
            phase_verdicts={},
            run_verdict={"policy_signals": ["some_custom_signal"]},
        )
        assert p["authoritative_close_state"] == "eligible_for_done"

    def test_run_rejected_verdict(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            run_verdict={"state": "rejected"},
        )
        assert p["authoritative_close_state"] == "rejected"

    def test_semantic_signals_are_prioritized_before_infra_signals(self):
        p = derive_lead_close_policy(
            phase_verdicts={
                "build": {"contract_status": "drift"},
                "review": {"status": "rejected"},
                "qa": {"status": "blocked"},
            },
            run_verdict={
                "state": "rejected",
                "policy_signals": [
                    "evidence_gate_failed",
                    "low_productivity_below_threshold",
                    "semantic_gate_failed",
                ],
            },
            phase_states={"build": "blocked"},
        )
        assert p["authoritative_close_state"] == "rejected"
        assert p["blocking_signals"][:4] == [
            "qa_blocked",
            "review_rejected",
            "semantic_gate_failed",
            "slice_drift",
        ]
        assert p["primary_blocking_signals"] == [
            "qa_blocked",
            "review_rejected",
            "semantic_gate_failed",
            "slice_drift",
        ]
        assert "run_rejected" in p["secondary_blocking_signals"]
        assert "evidence_gate_failed" in p["secondary_blocking_signals"]
        assert p["prefer_semantic_summary_first"] is True

    def test_user_accepted_degraded_close_can_cover_qa_validation_block(self):
        p = derive_lead_close_policy(
            phase_verdicts={"qa_validate_toc": {"status": "blocked", "role_hint": "qa"}},
            phase_states={
                "lead_close": "completed",
                "qa_validate_toc": "failed",
            },
            run_verdict={
                "state": "completed",
                "degraded_delivery": True,
                "user_risk_acceptance": True,
                "policy_signals": ["evidence_gate_failed"],
            },
        )
        assert p["authoritative_close_state"] == "eligible_for_done"
        assert p["can_declare_done"] is True
        assert "qa_blocked" in p["accepted_risk_signals"]
        assert "qa_validate_toc_failed" in p["accepted_risk_signals"]

    def test_user_risk_acceptance_does_not_cover_review_rejected(self):
        p = derive_lead_close_policy(
            phase_verdicts={"review": {"status": "rejected"}},
            run_verdict={
                "state": "completed",
                "degraded_delivery": True,
                "user_risk_acceptance": True,
            },
        )
        assert p["authoritative_close_state"] == "rejected"
        assert "review_rejected" in p["blocking_signals"]

    def test_planning_failure_is_primary_before_evidence_gate_signal(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            phase_states={"plan_engineering": "failed", "build": "blocked"},
            run_verdict={
                "state": "failed",
                "policy_signals": ["evidence_gate_failed", "live_mode_required_non_live"],
            },
        )
        assert p["authoritative_close_state"] == "not_completed"
        assert p["blocking_signals"][0] == "plan_engineering_failed"
        assert "evidence_gate_failed" in p["secondary_blocking_signals"]

    def test_planning_failure_suppresses_downstream_blocked_noise(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            phase_states={
                "plan_engineering": "failed",
                "build": "blocked",
                "review_slice2_code": "blocked",
                "qa_slice2_validation": "blocked",
                "lead_close": "blocked",
            },
            run_verdict={"state": "failed"},
        )
        assert p["authoritative_close_state"] == "not_completed"
        assert "plan_engineering_failed" in p["blocking_signals"]
        assert "build_blocked" not in p["blocking_signals"]
        assert "review_slice2_code_blocked" not in p["blocking_signals"]
        assert "qa_slice2_validation_blocked" not in p["blocking_signals"]
        assert "lead_close_blocked" not in p["blocking_signals"]


# ── Phase states (taskboard) ──────────────────────────────────────────────────

class TestPhaseStates:
    def test_failed_state_not_completed(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            phase_states={"engineer_toc_implementation": "failed"},
        )
        assert p["authoritative_close_state"] == "not_completed"

    def test_blocked_state_not_completed(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            phase_states={"build": "blocked"},
        )
        assert p["authoritative_close_state"] == "not_completed"

    def test_skip_phases_in_states_ignored(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            phase_states={"lead_intake": "failed"},
        )
        assert p["authoritative_close_state"] == "eligible_for_done"

    def test_support_scout_failures_are_ignored_as_close_blockers(self):
        p = derive_lead_close_policy(
            phase_verdicts={},
            phase_states={"scout_context_curator": "failed"},
            phase_outputs={"scout_context_curator": "BLOQUEADA: contexto auxiliar roto"},
        )
        assert p["authoritative_close_state"] == "eligible_for_done"


# ── build_lead_close_policy_prompt_block ─────────────────────────────────────

class TestPromptBlock:
    def test_eligible_for_done_allows_done(self):
        policy = derive_lead_close_policy(phase_verdicts={})
        block = build_lead_close_policy_prompt_block(policy)
        assert "eligible_for_done" in block
        assert "solo puedes declarar DONE" in block

    def test_rejected_forbids_done(self):
        policy = derive_lead_close_policy(
            phase_verdicts={"qa": {"status": "blocked"}}
        )
        block = build_lead_close_policy_prompt_block(policy)
        assert "NO declares DONE" in block
        assert "rejected" in block or "rechazada" in block.lower()

    def test_not_completed_forbids_done(self):
        policy = derive_lead_close_policy(
            phase_verdicts={"engineer_toc_implementation": {"status": "blocked"}}
        )
        block = build_lead_close_policy_prompt_block(policy)
        assert "NO declares DONE" in block

    def test_prompt_block_explicitly_prioritizes_semantic_cause(self):
        policy = derive_lead_close_policy(
            phase_verdicts={
                "review": {"status": "rejected"},
                "build": {"contract_status": "drift"},
            },
            run_verdict={"policy_signals": ["evidence_gate_failed"]},
        )
        block = build_lead_close_policy_prompt_block(policy)
        assert "primary_blocking_signals: review_rejected, slice_drift" in block
        assert "secondary_blocking_signals: evidence_gate_failed" in block
        assert "debes explicarlas primero como causa autoritativa" in block

    def test_prompt_block_includes_failure_origin_and_current_run_reason_codes(self):
        policy = derive_lead_close_policy(
            phase_verdicts={},
            run_verdict={
                "state": "failed",
                "failure_origin": "planning",
                "reason_codes": ["phase_failed:plan_engineering", "phase_failed:plan_risks"],
            },
            phase_states={"plan_engineering": "failed", "plan_risks": "failed"},
        )
        block = build_lead_close_policy_prompt_block(policy)
        assert "failure_origin: planning" in block
        assert "run_reason_codes: phase_failed:plan_engineering, phase_failed:plan_risks" in block
        assert "no cites bloqueos historicos" in block

    def test_prompt_block_includes_current_failed_and_pending_phases(self):
        policy = derive_lead_close_policy(
            phase_verdicts={},
            run_verdict={
                "state": "failed",
                "failure_origin": "planning",
                "reason_codes": ["phase_failed:plan_risks"],
                "failed_phases": ["plan_risks"],
                "blocked_phases": ["validate_core_logic"],
                "pending_phases": ["build", "review", "qa"],
            },
            phase_states={
                "plan_risks": "failed",
                "validate_core_logic": "blocked",
                "build": "pending",
            },
        )
        block = build_lead_close_policy_prompt_block(policy)
        assert "failed_phases: plan_risks" in block
        assert "blocked_phases: validate_core_logic" in block
        assert "pending_phases: build, review, qa" in block
        assert "failed_phases, blocked_phases, pending_phases" in block

    def test_prompt_block_includes_completed_phases_and_prohibits_relabeling_them(self):
        policy = derive_lead_close_policy(
            phase_verdicts={},
            run_verdict={
                "state": "failed",
                "failure_origin": "planning",
                "reason_codes": ["phase_failed:plan_risks"],
                "failed_phases": ["plan_risks"],
                "pending_phases": ["build", "review", "qa"],
            },
            phase_states={
                "plan_engineering": "completed",
                "plan_risks": "failed",
                "build": "pending",
            },
        )
        block = build_lead_close_policy_prompt_block(policy)
        assert "completed_phases: plan_engineering" in block
        assert "No describas una fase de completed_phases" in block

    def test_prompt_block_keeps_review_rejection_above_transient_routing_noise(self):
        policy = derive_lead_close_policy(
            phase_verdicts={
                "review_toc_module": {
                    "phase_id": "review_toc_module",
                    "role_hint": "reviewer",
                    "status": "rejected",
                    "reason_codes": ["review_rejected"],
                }
            },
            phase_states={"review_toc_module": "failed", "qa_validate": "blocked"},
            run_verdict={"state": "rejected", "reason_codes": ["review:rejected_decision"]},
        )
        block = build_lead_close_policy_prompt_block(policy)
        assert "recommended_close_action: rework_from_review_findings" in block
        assert "no lo reemplaces por routing" in block
        assert "No uses [PAUSE_FOR_USER] para routing/capacidad" in block

    def test_non_dict_input_returns_empty(self):
        assert build_lead_close_policy_prompt_block(None) == ""
        assert build_lead_close_policy_prompt_block("string") == ""
