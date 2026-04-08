import unittest

from aiteam.chat_policy import (
    build_chat_task_policy_metadata,
    build_chat_validation_contract,
    ChatPolicyInput,
    evaluate_chat_policy,
    resolve_run_type_policy,
    uses_chat_policy,
)


class ChatPolicyTests(unittest.TestCase):
    def _base_input(self, **overrides) -> ChatPolicyInput:
        payload = {
            "task_id": "CHAT-123",
            "run_type": "build",
            "final_state": "completed",
            "productivity_status": "moderate",
            "next_action_hint": "initial",
            "strict_mode": False,
            "continuation_requested": False,
            "allow_low_productivity_override": False,
            "lead_advisory_mode": False,
            "live_mode_required": False,
            "execution_mode": "text_only",
            "execution_steps": 0,
            "artifact_created": 0,
            "artifact_modified": 0,
            "productivity_score": 10,
            "reasoning_score": 20,
            "evidence_gate_failures": [],
            "semantic_gate_failures": [],
        }
        payload.update(overrides)
        return ChatPolicyInput(**payload)

    def test_resolve_run_type_policy_for_context_recovery(self) -> None:
        policy = resolve_run_type_policy("context_recovery", reasoning_score=45)
        self.assertEqual(policy.productivity_threshold, 0)
        self.assertTrue(policy.passes_by_reasoning)
        self.assertTrue(policy.is_context_query)

    def test_build_chat_validation_contract_exports_explicit_owner(self) -> None:
        contract = build_chat_validation_contract(require_execution_plan=True)
        metadata = contract.as_metadata()
        self.assertEqual(metadata["validation_owner"], "chat_policy")
        self.assertEqual(metadata["final_validation_layer"], "chat_policy")
        self.assertEqual(metadata["phase_quality_gate_mode"], "delegated_to_chat_policy")
        self.assertEqual(metadata["phase_evidence_gate_mode"], "delegated_to_chat_policy")
        self.assertTrue(bool(metadata["require_execution_plan"]))
        self.assertTrue(uses_chat_policy(metadata))

    def test_build_chat_task_policy_metadata_marks_chat_policy_owner(self) -> None:
        metadata = build_chat_task_policy_metadata()
        self.assertTrue(bool(metadata["interactive_chat"]))
        self.assertTrue(bool(metadata["skip_quality_gates"]))
        self.assertTrue(bool(metadata["skip_evidence_gate"]))
        self.assertTrue(uses_chat_policy(metadata))

    def test_live_mode_rejects_without_advisory(self) -> None:
        policy_input = self._base_input(live_mode_required=True)
        outcome = evaluate_chat_policy(
            policy_input,
            resolve_run_type_policy(policy_input.run_type, policy_input.reasoning_score),
        )
        self.assertEqual(outcome.final_state, "completed")
        self.assertFalse(outcome.live_mode_rejected)
        self.assertTrue(outcome.policy_review_required)
        self.assertIn("live_mode_required_non_live", outcome.policy_signals)
        self.assertTrue(
            any(
                event.event_type == "chat_policy_signal"
                and event.payload.get("signal") == "live_mode_required_non_live"
                for event in outcome.events
            )
        )

    def test_live_mode_becomes_signal_in_advisory(self) -> None:
        policy_input = self._base_input(
            lead_advisory_mode=True,
            live_mode_required=True,
        )
        outcome = evaluate_chat_policy(
            policy_input,
            resolve_run_type_policy(policy_input.run_type, policy_input.reasoning_score),
        )
        self.assertEqual(outcome.final_state, "completed")
        self.assertIn("live_mode_required_non_live", outcome.policy_signals)
        self.assertTrue(
            any(
                event.event_type == "chat_policy_signal"
                and event.payload.get("signal") == "live_mode_required_non_live"
                for event in outcome.events
            )
        )

    def test_evidence_gate_rejects_without_advisory(self) -> None:
        policy_input = self._base_input(
            evidence_gate_failures=["build:placeholder_output"],
        )
        outcome = evaluate_chat_policy(
            policy_input,
            resolve_run_type_policy(policy_input.run_type, policy_input.reasoning_score),
        )
        self.assertTrue(outcome.evidence_gate_applied)
        self.assertEqual(outcome.final_state, "completed")
        self.assertTrue(outcome.policy_review_required)
        self.assertIn("evidence_gate_failed", outcome.policy_signals)
        self.assertTrue(
            any(
                event.event_type == "chat_policy_signal"
                and event.payload.get("signal") == "evidence_gate_failed"
                for event in outcome.events
            )
        )

    def test_semantic_gate_rejects_completed_run_without_advisory(self) -> None:
        policy_input = self._base_input(
            semantic_gate_failures=["review:rejected_decision", "qa:blocked_status"],
            productivity_score=90,
            execution_mode="live",
            execution_steps=3,
            artifact_modified=1,
        )
        outcome = evaluate_chat_policy(
            policy_input,
            resolve_run_type_policy(policy_input.run_type, policy_input.reasoning_score),
        )
        self.assertTrue(outcome.semantic_gate_applied)
        self.assertEqual(outcome.final_state, "rejected")
        self.assertEqual(outcome.productivity_status, "weak")
        self.assertTrue(outcome.policy_review_required)
        self.assertIn("semantic_gate_failed", outcome.policy_signals)
        self.assertTrue(
            any(
                event.event_type == "chat_policy_signal"
                and event.payload.get("signal") == "semantic_gate_failed"
                for event in outcome.events
            )
        )

    def test_semantic_gate_stays_advisory_when_lead_explicitly_closes_in_advisory(self) -> None:
        policy_input = self._base_input(
            lead_advisory_mode=True,
            semantic_gate_failures=["review:rejected_decision"],
            productivity_score=90,
            execution_mode="live",
            execution_steps=3,
            artifact_modified=1,
        )
        outcome = evaluate_chat_policy(
            policy_input,
            resolve_run_type_policy(policy_input.run_type, policy_input.reasoning_score),
        )
        self.assertTrue(outcome.semantic_gate_applied)
        self.assertEqual(outcome.final_state, "completed")
        self.assertEqual(outcome.productivity_status, "weak")
        self.assertIn("semantic_gate_failed", outcome.policy_signals)

    def test_strict_mode_blocks_close_without_advisory(self) -> None:
        policy_input = self._base_input(
            strict_mode=True,
            execution_mode="text_only",
            productivity_score=60,
        )
        outcome = evaluate_chat_policy(
            policy_input,
            resolve_run_type_policy(policy_input.run_type, policy_input.reasoning_score),
        )
        self.assertFalse(outcome.strict_mode_applied)
        self.assertEqual(outcome.final_state, "completed")
        self.assertTrue(outcome.policy_review_required)
        self.assertIn("strict_mode_requires_more_evidence", outcome.policy_signals)

    def test_planning_run_allows_reasoning_based_override(self) -> None:
        policy_input = self._base_input(
            run_type="planning",
            productivity_score=0,
            reasoning_score=60,
            final_state="completed",
            execution_mode="text_only",
        )
        outcome = evaluate_chat_policy(
            policy_input,
            resolve_run_type_policy(policy_input.run_type, policy_input.reasoning_score),
        )
        self.assertTrue(outcome.low_productivity_override)
        self.assertFalse(outcome.low_productivity_rejected)

    def test_low_productivity_rejects_build_without_override(self) -> None:
        policy_input = self._base_input(
            productivity_score=12,
            final_state="completed",
            execution_mode="live",
        )
        outcome = evaluate_chat_policy(
            policy_input,
            resolve_run_type_policy(policy_input.run_type, policy_input.reasoning_score),
        )
        self.assertFalse(outcome.low_productivity_rejected)
        self.assertEqual(outcome.final_state, "completed")
        self.assertTrue(outcome.policy_review_required)
        self.assertIn("low_productivity_below_threshold", outcome.policy_signals)

    # ── Fix C: continuation_requested no anula override en runs sin artefactos ──

    def test_continuation_override_allowed_when_some_work_done(self) -> None:
        """continuation_requested should override low productivity when artifacts exist."""
        policy_input = self._base_input(
            productivity_score=20,
            continuation_requested=True,
            artifact_created=2,   # some files written
            execution_steps=3,
        )
        outcome = evaluate_chat_policy(
            policy_input,
            resolve_run_type_policy(policy_input.run_type, policy_input.reasoning_score),
        )
        self.assertTrue(outcome.low_productivity_override)

    def test_continuation_override_blocked_when_zero_output(self) -> None:
        """continuation_requested must NOT override when run produced 0 artifacts and 0 steps."""
        policy_input = self._base_input(
            productivity_score=20,
            continuation_requested=True,
            artifact_created=0,
            artifact_modified=0,
            execution_steps=0,
        )
        outcome = evaluate_chat_policy(
            policy_input,
            resolve_run_type_policy(policy_input.run_type, policy_input.reasoning_score),
        )
        self.assertFalse(outcome.low_productivity_override)

    def test_allow_low_productivity_override_flag_still_works_for_zero_output(self) -> None:
        """Explicit allow_low_productivity_override flag always works (manual user decision)."""
        policy_input = self._base_input(
            productivity_score=20,
            allow_low_productivity_override=True,
            artifact_created=0,
            execution_steps=0,
        )
        outcome = evaluate_chat_policy(
            policy_input,
            resolve_run_type_policy(policy_input.run_type, policy_input.reasoning_score),
        )
        self.assertTrue(outcome.low_productivity_override)


if __name__ == "__main__":
    unittest.main()
