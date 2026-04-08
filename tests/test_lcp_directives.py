"""E8-A9: Tests del Lead Control Protocol (LCP) — directivas de flujo adaptativo.

Cubre:
- Parser _extract_lcp_directives: un test por directiva
- _strip_lcp_directives: limpieza de output para usuario
- Prioridad entre directivas
- Directivas malformadas se ignoran
- Combinación de directivas
"""
import sys
import types
import importlib
import unittest

# Importar las funciones directamente desde api.main
from api.main import _extract_lcp_directives, _strip_lcp_directives


class TestExtractLcpDirectives(unittest.TestCase):

    # ── DIRECT_ANSWER ─────────────────────────────────────────────────

    def test_direct_answer_detected(self):
        text = "El estado del proyecto es estable.\n[DIRECT_ANSWER]"
        result = _extract_lcp_directives(text)
        self.assertTrue(result.get("direct_answer"))

    def test_direct_answer_case_insensitive(self):
        result = _extract_lcp_directives("[direct_answer]")
        self.assertTrue(result.get("direct_answer"))

    def test_direct_answer_with_payload_detected(self):
        result = _extract_lcp_directives('[DIRECT_ANSWER: "Respuesta resumida"]')
        self.assertTrue(result.get("direct_answer"))
        self.assertEqual(result.get("direct_answer_text"), "Respuesta resumida")

    def test_direct_answer_not_present(self):
        result = _extract_lcp_directives("Respuesta normal sin directiva.")
        self.assertNotIn("direct_answer", result)

    # ── REJECT ───────────────────────────────────────────────────────

    def test_reject_detected(self):
        text = 'No puedo hacer eso. [REJECT: "Petición fuera de scope del proyecto"]'
        result = _extract_lcp_directives(text)
        self.assertEqual(result.get("reject"), "Petición fuera de scope del proyecto")

    def test_reject_reason_extracted(self):
        text = '[REJECT: "Viola los guardrails de seguridad"]'
        result = _extract_lcp_directives(text)
        self.assertIn("guardrails", result["reject"])

    def test_reject_without_quotes_ignored(self):
        # Sin comillas → malformada → ignorar
        result = _extract_lcp_directives("[REJECT: razon sin comillas]")
        self.assertNotIn("reject", result)

    # ── ABORT_PHASES ─────────────────────────────────────────────────

    def test_abort_phases_detected(self):
        text = '[ABORT_PHASES: "Los scouts ya tienen toda la información necesaria"]'
        result = _extract_lcp_directives(text)
        self.assertEqual(result.get("abort_phases"), "Los scouts ya tienen toda la información necesaria")

    def test_advisory_mode_detected(self):
        text = '[ADVISORY_MODE: "Cerrar como advisory por falta de evidencia live"]'
        result = _extract_lcp_directives(text)
        self.assertEqual(
            result.get("advisory_mode"),
            "Cerrar como advisory por falta de evidencia live",
        )

    def test_pause_for_user_detected(self):
        text = '[PAUSE_FOR_USER: "¿Quieres cambiar la ruta de build o ajustar el objetivo?"]'
        result = _extract_lcp_directives(text)
        self.assertEqual(
            result.get("pause_for_user"),
            "¿Quieres cambiar la ruta de build o ajustar el objetivo?",
        )

    def test_skip_phase_detected(self):
        text = '[SKIP_PHASE: "build" reason="gate rechazado varias veces"]'
        result = _extract_lcp_directives(text)
        self.assertEqual(result.get("skip_phase", {}).get("phase_id"), "build")
        self.assertEqual(
            result.get("skip_phase", {}).get("reason"),
            "gate rechazado varias veces",
        )

    def test_degrade_detected(self):
        text = '[DEGRADE: scope="partial" reason="solo researcher produjo hallazgos utiles"]'
        result = _extract_lcp_directives(text)
        self.assertEqual(result.get("degrade", {}).get("scope"), "partial")
        self.assertEqual(
            result.get("degrade", {}).get("reason"),
            "solo researcher produjo hallazgos utiles",
        )

    # ── ESCALATE ─────────────────────────────────────────────────────

    def test_escalate_complexity_detected(self):
        text = "[ESCALATE: complexity=high]"
        result = _extract_lcp_directives(text)
        self.assertEqual(result.get("escalate", {}).get("complexity"), "high")

    def test_escalate_criticality_detected(self):
        text = "[ESCALATE: criticality=critical]"
        result = _extract_lcp_directives(text)
        self.assertEqual(result.get("escalate", {}).get("criticality"), "critical")

    def test_escalate_both_params(self):
        text = "[ESCALATE: complexity=very_high criticality=critical]"
        result = _extract_lcp_directives(text)
        esc = result.get("escalate", {})
        self.assertEqual(esc.get("complexity"), "very_high")
        self.assertEqual(esc.get("criticality"), "critical")

    def test_escalate_empty_payload_ignored(self):
        result = _extract_lcp_directives("[ESCALATE:]")
        self.assertNotIn("escalate", result)

    # ── SKIP ─────────────────────────────────────────────────────────

    def test_skip_single_phase(self):
        text = '[SKIP: "review"]'
        result = _extract_lcp_directives(text)
        self.assertEqual(result.get("skip"), ["review"])

    def test_skip_multiple_phases(self):
        text = '[SKIP: "review qa"]'
        result = _extract_lcp_directives(text)
        self.assertIn("review", result.get("skip", []))
        self.assertIn("qa", result.get("skip", []))

    def test_skip_without_quotes_ignored(self):
        result = _extract_lcp_directives("[SKIP: review]")
        self.assertNotIn("skip", result)

    # ── ADD_PHASE ────────────────────────────────────────────────────

    def test_add_phase_detected(self):
        text = '[ADD_PHASE: ENGINEER "Exportar datos antes de migrar"]'
        result = _extract_lcp_directives(text)
        ap = result.get("add_phase", {})
        self.assertEqual(ap.get("role"), "ENGINEER")
        self.assertEqual(ap.get("objective"), "Exportar datos antes de migrar")

    def test_add_phase_researcher(self):
        text = '[ADD_PHASE: RESEARCHER "Auditar endpoints de seguridad"]'
        result = _extract_lcp_directives(text)
        self.assertEqual(result["add_phase"]["role"], "RESEARCHER")

    # ── EXTEND_BUDGET ────────────────────────────────────────────────

    def test_extend_budget_detected(self):
        text = "[EXTEND_BUDGET: +15]"
        result = _extract_lcp_directives(text)
        self.assertEqual(result.get("extend_budget"), 15)

    def test_extend_budget_small(self):
        result = _extract_lcp_directives("[EXTEND_BUDGET: +3]")
        self.assertEqual(result.get("extend_budget"), 3)

    def test_extend_budget_negative_ignored(self):
        # El formato requiere +N; sin + no matchea
        result = _extract_lcp_directives("[EXTEND_BUDGET: 10]")
        self.assertNotIn("extend_budget", result)

    def test_set_budget_detected(self):
        result = _extract_lcp_directives("[SET_BUDGET: 3]")
        self.assertEqual(result.get("set_budget"), 3)

    # ── RUN_MODE ─────────────────────────────────────────────────────

    def test_run_mode_planning_only_detected(self):
        result = _extract_lcp_directives("[RUN_MODE: planning_only]")
        self.assertEqual(result.get("run_mode"), "planning_only")

    def test_run_mode_team_decision_detected(self):
        result = _extract_lcp_directives("[RUN_MODE: team_decision]")
        self.assertEqual(result.get("run_mode"), "team_decision")

    def test_run_mode_architecture_review_detected(self):
        result = _extract_lcp_directives("[RUN_MODE: architecture_review]")
        self.assertEqual(result.get("run_mode"), "architecture_review")

    def test_run_mode_roadmap_detected(self):
        result = _extract_lcp_directives("[RUN_MODE: roadmap]")
        self.assertEqual(result.get("run_mode"), "roadmap")

    def test_replan_detected(self):
        result = _extract_lcp_directives("[REPLAN]")
        self.assertTrue(result.get("replan"))

    def test_force_gate_detected(self):
        result = _extract_lcp_directives('[FORCE_GATE: "build"]')
        self.assertEqual(result.get("force_gate"), "build")

    def test_retry_route_detected(self):
        result = _extract_lcp_directives('[RETRY_ROUTE: "build"]')
        self.assertEqual(result.get("retry_route"), "build")

    # ── Combinaciones ────────────────────────────────────────────────

    def test_multiple_directives_parsed(self):
        text = (
            "Plan revisado.\n"
            "[ESCALATE: complexity=high criticality=critical]\n"
            '[SKIP: "review"]\n'
            "[EXTEND_BUDGET: +8]"
        )
        result = _extract_lcp_directives(text)
        self.assertIn("escalate", result)
        self.assertIn("skip", result)
        self.assertIn("extend_budget", result)
        self.assertNotIn("direct_answer", result)

    def test_empty_text_returns_empty_dict(self):
        self.assertEqual(_extract_lcp_directives(""), {})
        self.assertEqual(_extract_lcp_directives(None), {})  # type: ignore

    def test_no_directives_returns_empty_dict(self):
        result = _extract_lcp_directives("Texto normal sin directivas. Plan de 3 fases.")
        self.assertEqual(result, {})

    def test_malformed_directive_ignored(self):
        # Directiva sin corchete de cierre → ignorada
        result = _extract_lcp_directives("[DIRECT_ANSWER sin cerrar")
        self.assertNotIn("direct_answer", result)


class TestStripLcpDirectives(unittest.TestCase):

    def test_strips_direct_answer(self):
        text = "Respuesta al usuario.\n[DIRECT_ANSWER]"
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[DIRECT_ANSWER]", clean)
        self.assertIn("Respuesta al usuario", clean)

    def test_strips_reject(self):
        text = 'No puedo. [REJECT: "fuera de scope"]'
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[REJECT", clean)
        self.assertIn("No puedo", clean)

    def test_strips_advisory_mode(self):
        text = 'Cierro como advisory. [ADVISORY_MODE: "Falta evidencia"]'
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[ADVISORY_MODE", clean)
        self.assertIn("Cierro como advisory.", clean)

    def test_strips_pause_for_user(self):
        text = 'Necesito tu decision. [PAUSE_FOR_USER: "¿Priorizo A o B?"]'
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[PAUSE_FOR_USER", clean)
        self.assertIn("Necesito tu decision.", clean)

    def test_strips_skip_phase(self):
        text = 'Fase aceptada como saltada. [SKIP_PHASE: "build" reason="fallo irrecuperable"]'
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[SKIP_PHASE", clean)
        self.assertIn("Fase aceptada como saltada.", clean)

    def test_strips_degrade(self):
        text = 'Entrega parcial aceptada. [DEGRADE: scope="minimal" reason="solo diagnostico"]'
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[DEGRADE", clean)
        self.assertIn("Entrega parcial aceptada.", clean)

    def test_strips_workflow_plan_block(self):
        text = "Plan:\n[WORKFLOW_PLAN]\nphase: build\n[/WORKFLOW_PLAN]\nResumen."
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[WORKFLOW_PLAN]", clean)
        self.assertNotIn("[/WORKFLOW_PLAN]", clean)
        self.assertIn("Resumen", clean)

    def test_strips_multiple_directives(self):
        text = (
            "Análisis completo.\n"
            "[ESCALATE: complexity=high]\n"
            '[SKIP: "review"]\n'
            "[EXTEND_BUDGET: +5]"
        )
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[ESCALATE", clean)
        self.assertNotIn("[SKIP", clean)
        self.assertNotIn("[EXTEND_BUDGET", clean)
        self.assertIn("Análisis completo", clean)

    def test_strips_clarify(self):
        text = 'Necesito saber más. [CLARIFY: "¿REST o GraphQL?"]'
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[CLARIFY", clean)

    def test_strips_run_mode(self):
        text = "Solo quiero plan.\n[RUN_MODE: planning_only]"
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[RUN_MODE", clean)
        self.assertIn("Solo quiero plan.", clean)

    def test_strips_set_budget_and_retry_route(self):
        text = (
            "Ajuste mid-run.\n"
            "[SET_BUDGET: 4]\n"
            '[RETRY_ROUTE: "build"]'
        )
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[SET_BUDGET", clean)
        self.assertNotIn("[RETRY_ROUTE", clean)
        self.assertIn("Ajuste mid-run.", clean)

    def test_no_directives_unchanged(self):
        text = "Texto completamente limpio sin directivas."
        clean = _strip_lcp_directives(text)
        self.assertEqual(clean, text)

    def test_empty_input(self):
        self.assertEqual(_strip_lcp_directives(""), "")


class TestLcpPriority(unittest.TestCase):
    """Verifica que el parser detecta correctamente directivas de alta prioridad."""

    def test_reject_coexists_with_direct_answer(self):
        # Ambas presentes → el handler debe priorizar REJECT, pero el parser devuelve ambas
        text = '[REJECT: "Imposible"] [DIRECT_ANSWER]'
        result = _extract_lcp_directives(text)
        self.assertIn("reject", result)
        self.assertIn("direct_answer", result)
        # La lógica de prioridad está en el handler de api/main.py, no en el parser

    def test_escalate_with_skip_combined(self):
        text = '[ESCALATE: complexity=high] [SKIP: "review qa"]'
        result = _extract_lcp_directives(text)
        self.assertEqual(result["escalate"]["complexity"], "high")
        self.assertEqual(sorted(result["skip"]), ["qa", "review"])

    def test_all_mvp_directives_in_one_output(self):
        text = (
            "Output del Lead con múltiples directivas.\n"
            "[ESCALATE: complexity=very_high criticality=critical]\n"
            '[SKIP: "review"]\n'
            "[EXTEND_BUDGET: +10]\n"
            '[ADD_PHASE: ENGINEER "Backup de datos"]'
        )
        result = _extract_lcp_directives(text)
        self.assertIn("escalate", result)
        self.assertIn("skip", result)
        self.assertIn("extend_budget", result)
        self.assertIn("add_phase", result)
        self.assertEqual(result["extend_budget"], 10)
        self.assertEqual(result["add_phase"]["role"], "ENGINEER")


if __name__ == "__main__":
    unittest.main()
