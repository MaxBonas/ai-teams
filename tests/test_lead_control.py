import unittest

import api.main as api_main
from aiteam.lead_control import (
    extract_clarify_directive,
    extract_delegate_directive,
    extract_delegate_request,
    extract_evidence_plan,
    extract_lcp_directives,
    iter_lead_checkpoint_directives,
    resolve_lead_intake,
    strip_selected_lcp_directives,
    strip_lcp_directives,
)
from aiteam.types import Complexity, Criticality


class LeadControlTests(unittest.TestCase):
    def test_resolve_lead_intake_returns_early_reject(self) -> None:
        result = resolve_lead_intake(
            lead_output='No puedo hacerlo. [REJECT: "Fuera de scope"]',
            chat_mode="sprint5",
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            round_budget=5,
        )

        self.assertIsNotNone(result.early_exit)
        assert result.early_exit is not None
        self.assertEqual(result.early_exit.state, "rejected")
        self.assertEqual(result.round_budget, 5)
        self.assertEqual(result.cleaned_output, "No puedo hacerlo.")

    def test_resolve_lead_intake_applies_skip_add_phase_and_extend_budget(self) -> None:
        lead_output = (
            "[WORKFLOW_PLAN]\n"
            "RESEARCHER: discovery — investigar\n"
            "ENGINEER: build — implementar\n"
            "REVIEWER: review — revisar\n"
            "[/WORKFLOW_PLAN]\n"
            '[SKIP: "review"]\n'
            '[ADD_PHASE: QA "validar humo"]\n'
            "[EXTEND_BUDGET: +4]"
        )

        result = resolve_lead_intake(
            lead_output=lead_output,
            chat_mode="classic",
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            round_budget=5,
        )

        self.assertIsNone(result.early_exit)
        phase_ids = [item.phase_id for item in result.phases]
        self.assertIn("discovery", phase_ids)
        self.assertIn("build", phase_ids)
        self.assertNotIn("review", phase_ids)
        self.assertTrue(any(item.startswith("extra_qa") for item in phase_ids))
        self.assertEqual(result.round_budget, 9)

    def test_directive_extractors_and_strip_remain_available(self) -> None:
        text = (
            'Analisis.\n[CLARIFY: "REST o GraphQL?"]\n'
            '[DELEGATE: "revisa requirements.txt"]\n'
            "[DIRECT_ANSWER]"
        )

        self.assertEqual(extract_clarify_directive(text), "REST o GraphQL?")
        self.assertEqual(
            extract_delegate_directive(text), "revisa requirements.txt"
        )
        self.assertTrue(extract_lcp_directives(text).get("direct_answer"))
        self.assertEqual(strip_lcp_directives(text), "Analisis.")

    def test_extract_specialized_delegate_request_with_wait_policy_and_budget(self) -> None:
        text = (
            '[DELEGATE_BROWSER_REPRO: "reproduce el fallo del login"]\n'
            "[WAIT_POLICY: quorum]\n"
            "[DELEGATE_BUDGET: +4]"
        )

        request = extract_delegate_request(text)

        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.intent, "delegate_browser_repro")
        self.assertEqual(request.query, "reproduce el fallo del login")
        self.assertEqual(request.wait_policy, "quorum")
        self.assertEqual(request.delegate_budget, 4)

    def test_strip_lcp_directives_removes_specialized_delegate_controls(self) -> None:
        text = (
            "Analiza esto.\n"
            '[DELEGATE_REPO_SCAN: "mapea el repo"]\n'
            "[WAIT_POLICY: best_effort]\n"
            "[DELEGATE_BUDGET: 2]"
        )

        self.assertEqual(strip_lcp_directives(text), "Analiza esto.")

    def test_extract_evidence_plan_parses_phase_requirements(self) -> None:
        plan = extract_evidence_plan(
            "[EVIDENCE_PLAN]\n"
            "phase_id: build\n"
            "delegate: delegate_test_run\n"
            "delegate: delegate_browser_repro\n"
            "wait_policy: quorum\n"
            "delegate_budget: 4\n"
            "phase_id: review\n"
            "delegate: delegate_repo_scan\n"
            "[/EVIDENCE_PLAN]"
        )

        self.assertEqual(
            plan["build"]["delegate_intents"],
            ["delegate_test_run", "delegate_browser_repro"],
        )
        self.assertEqual(plan["build"]["wait_policy"], "quorum")
        self.assertEqual(plan["build"]["delegate_budget"], 4)
        self.assertEqual(
            plan["review"]["delegate_intents"],
            ["delegate_repo_scan"],
        )

    def test_strip_lcp_directives_removes_evidence_plan_block(self) -> None:
        text = (
            "Plan listo.\n"
            "[EVIDENCE_PLAN]\n"
            "phase_id: build\n"
            "delegate: delegate_test_run\n"
            "[/EVIDENCE_PLAN]"
        )

        self.assertEqual(strip_lcp_directives(text), "Plan listo.")

    def test_iter_lead_checkpoint_directives_skips_lead_intake_by_default(self) -> None:
        items = iter_lead_checkpoint_directives(
            {
                "lead_intake": '[DELEGATE_REPO_SCAN: "ignorar intake"]',
                "lead_failure_build": '[DELEGATE_REPO_SCAN: "investiga fallo"]',
                "build": '[SKIP: "qa"]',
                "lead_close": '[ADVISORY_MODE: "cerrar advisory"]',
            }
        )

        self.assertEqual(
            [phase_name for phase_name, _output, _directives in items],
            ["lead_close", "lead_failure_build"],
        )
        self.assertTrue(items[0][2].get("advisory_mode"))
        self.assertEqual(items[1][2].get("direct_answer"), None)

    def test_main_and_lead_control_share_selective_strip_behavior(self) -> None:
        text = (
            '[DELEGATE_BROWSER_REPRO: "reproduce"]\n'
            "[WAIT_POLICY: quorum]\n"
            "[DELEGATE_BUDGET: 4]\n"
            '[RETRY_ROUTE: "build"]\n'
            "Texto visible"
        )
        directives = ["DELEGATE_BROWSER_REPRO", "WAIT_POLICY", "DELEGATE_BUDGET"]

        self.assertEqual(
            api_main._strip_selected_directives(text, directives),
            strip_selected_lcp_directives(text, directives),
        )

    def test_run_mode_planning_only_uses_non_build_preset(self) -> None:
        result = resolve_lead_intake(
            lead_output="[RUN_MODE: planning_only]\nPlanifica sin construir.",
            chat_mode="sprint5",
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            round_budget=5,
        )

        self.assertEqual(
            [item.phase_id for item in result.phases],
            ["discovery", "plan"],
        )
        self.assertEqual(
            [item.role for item in result.phases],
            ["RESEARCHER", "REVIEWER"],
        )
        self.assertEqual(result.directives.get("run_mode"), "planning_only")

    def test_run_mode_team_decision_uses_deliberative_preset(self) -> None:
        result = resolve_lead_intake(
            lead_output="[RUN_MODE: team_decision]\nDecidid en equipo.",
            chat_mode="classic",
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            round_budget=5,
        )

        self.assertEqual(
            [item.phase_id for item in result.phases],
            ["discovery", "review_options", "qa_risks"],
        )
        self.assertEqual(
            [item.role for item in result.phases],
            ["RESEARCHER", "REVIEWER", "QA"],
        )

    def test_run_mode_architecture_review_uses_architecture_preset(self) -> None:
        result = resolve_lead_intake(
            lead_output="[RUN_MODE: architecture_review]\nRevisa arquitectura.",
            chat_mode="classic",
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            round_budget=5,
        )

        self.assertEqual(
            [item.phase_id for item in result.phases],
            ["discovery", "architecture_options", "adr_document"],
        )
        self.assertEqual(
            [item.role for item in result.phases],
            ["RESEARCHER", "REVIEWER", "REVIEWER"],
        )
        self.assertEqual(result.directives.get("run_mode"), "architecture_review")

    def test_run_mode_roadmap_uses_roadmap_preset(self) -> None:
        result = resolve_lead_intake(
            lead_output="[RUN_MODE: roadmap]\nDefine roadmap.",
            chat_mode="classic",
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            round_budget=5,
        )

        self.assertEqual(
            [item.phase_id for item in result.phases],
            ["discovery", "roadmap_prioritization", "roadmap_document"],
        )
        self.assertEqual(
            [item.role for item in result.phases],
            ["RESEARCHER", "REVIEWER", "REVIEWER"],
        )
        self.assertEqual(result.directives.get("run_mode"), "roadmap")
        self.assertEqual(result.plan_source, "run_mode:roadmap")

    def test_explicit_workflow_plan_takes_precedence_over_run_mode(self) -> None:
        result = resolve_lead_intake(
            lead_output=(
                "[WORKFLOW_PLAN]\n"
                "phase_id: build\n"
                "role: ENGINEER\n"
                "objective: implementar\n"
                "[/WORKFLOW_PLAN]\n"
                "[RUN_MODE: planning_only]"
            ),
            chat_mode="classic",
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            round_budget=5,
        )

        self.assertEqual([item.phase_id for item in result.phases], ["build"])
        self.assertEqual([item.role for item in result.phases], ["ENGINEER"])
        self.assertEqual(result.plan_source, "explicit_workflow_plan")

    def test_default_plan_source_is_explicit(self) -> None:
        result = resolve_lead_intake(
            lead_output="Planifica la siguiente accion sin bloque estructurado.",
            chat_mode="classic",
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            round_budget=5,
        )

        self.assertEqual(result.plan_source, "default")

    def test_direct_answer_is_blocked_when_continuation_has_pending_work(self) -> None:
        result = resolve_lead_intake(
            lead_output="[DIRECT_ANSWER]\nResumen directo no permitido.",
            chat_mode="sprint5",
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            round_budget=5,
            forbid_direct_answer=True,
        )

        self.assertIsNone(result.early_exit)
        self.assertFalse(bool(result.directives.get("direct_answer")))
        self.assertIn("build", [item.phase_id for item in result.phases])
        event_names = [item.directive for item in result.events]
        self.assertIn("direct_answer_blocked", event_names)


if __name__ == "__main__":
    unittest.main()
