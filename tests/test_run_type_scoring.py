"""Tests para E7-B: _detect_run_type() y scoring reform.

Verifica que el tipo de run se clasifica correctamente y que
las políticas de aceptación/rechazo son coherentes con el objetivo
multi-tier (Lead caro + Scouts baratos).
"""

import unittest


def _detect_run_type(message, phase_task_ids, artifact_created, artifact_modified):
    """Importar desde api.main para test."""
    from api.main import _detect_run_type as _real
    return _real(
        message=message,
        phase_task_ids=phase_task_ids,
        artifact_created=artifact_created,
        artifact_modified=artifact_modified,
    )


class TestDetectRunType(unittest.TestCase):
    # ── context_recovery ─────────────────────────────────────────────

    def test_context_only_message_no_build_phases(self):
        result = _detect_run_type(
            message="¿de qué iba el proyecto?",
            phase_task_ids={"lead_intake": "x::lead_intake", "lead_close": "x::lead_close"},
            artifact_created=0,
            artifact_modified=0,
        )
        self.assertEqual(result, "context_recovery")

    def test_context_query_with_history_phase(self):
        result = _detect_run_type(
            message="resumen del estado actual",
            phase_task_ids={
                "lead_intake": "x::lead_intake",
                "discovery": "x::discovery",
                "lead_close": "x::lead_close",
            },
            artifact_created=0,
            artifact_modified=0,
        )
        # discovery sola no hace build — sigue siendo context_recovery o planning
        self.assertIn(result, ("context_recovery", "planning"))

    def test_context_query_with_artifacts_is_build(self):
        """Si hay artefactos producidos, aunque la pregunta sea de orientación → build."""
        result = _detect_run_type(
            message="¿de qué iba el proyecto?",
            phase_task_ids={"lead_intake": "x::lead_intake", "lead_close": "x::lead_close"},
            artifact_created=2,
            artifact_modified=0,
        )
        self.assertEqual(result, "build")

    # ── planning ─────────────────────────────────────────────────────

    def test_research_only_phases_is_planning(self):
        result = _detect_run_type(
            message="investiga qué librerías usar",
            phase_task_ids={
                "lead_intake": "x::lead_intake",
                "research": "x::research",
                "review": "x::review",
                "lead_close": "x::lead_close",
            },
            artifact_created=0,
            artifact_modified=0,
        )
        self.assertEqual(result, "planning")

    def test_plan_research_plan_risks_is_planning(self):
        result = _detect_run_type(
            message="planifica el sprint",
            phase_task_ids={
                "lead_intake": "x::lead_intake",
                "plan_research": "x::plan_research",
                "plan_risks": "x::plan_risks",
                "lead_close": "x::lead_close",
            },
            artifact_created=0,
            artifact_modified=0,
        )
        self.assertEqual(result, "planning")

    # ── build ────────────────────────────────────────────────────────

    def test_build_phase_is_build(self):
        result = _detect_run_type(
            message="implementa el endpoint POST /users",
            phase_task_ids={
                "lead_intake": "x::lead_intake",
                "discovery": "x::discovery",
                "build": "x::build",
                "review": "x::review",
                "qa": "x::qa",
                "lead_close": "x::lead_close",
            },
            artifact_created=3,
            artifact_modified=1,
        )
        self.assertEqual(result, "build")

    def test_implement_phase_is_build(self):
        result = _detect_run_type(
            message="añade tests unitarios",
            phase_task_ids={
                "lead_intake": "x::lead_intake",
                "implement": "x::implement",
                "lead_close": "x::lead_close",
            },
            artifact_created=0,
            artifact_modified=0,
        )
        self.assertEqual(result, "build")

    def test_artifacts_alone_make_build(self):
        """Aunque no haya fase build explícita, si hay artefactos → build."""
        result = _detect_run_type(
            message="ajusta el estilo del botón",
            phase_task_ids={
                "lead_intake": "x::lead_intake",
                "research": "x::research",
                "lead_close": "x::lead_close",
            },
            artifact_created=0,
            artifact_modified=2,
        )
        self.assertEqual(result, "build")

    # ── mixed ────────────────────────────────────────────────────────

    def test_unknown_phases_is_mixed(self):
        result = _detect_run_type(
            message="reorganiza el proyecto",
            phase_task_ids={
                "lead_intake": "x::lead_intake",
                "custom_phase": "x::custom_phase",
                "lead_close": "x::lead_close",
            },
            artifact_created=0,
            artifact_modified=0,
        )
        self.assertEqual(result, "mixed")


class TestScoringCoherence(unittest.TestCase):
    """Verifica la lógica de thresholds por tipo de run."""

    def _simulate_threshold(self, run_type, reasoning_score):
        """Simula la lógica de threshold del endpoint."""
        if run_type == "context_recovery":
            productivity_threshold = 0
            passes_by_reasoning = reasoning_score >= 40
        elif run_type == "planning":
            productivity_threshold = 0
            passes_by_reasoning = reasoning_score >= 50
        else:
            productivity_threshold = 35
            passes_by_reasoning = False
        return productivity_threshold, passes_by_reasoning

    def test_context_recovery_p0_r70_passes(self):
        """La sesión analizada (P30, R70) hubiera pasado como context_recovery."""
        threshold, passes_reasoning = self._simulate_threshold("context_recovery", reasoning_score=70)
        # Con P=30, el threshold es 0 → no hay rechazo por P
        self.assertEqual(threshold, 0)
        # Con R=70 >= 40 → passes_by_reasoning=True → low_productivity_override activo
        self.assertTrue(passes_reasoning)

    def test_context_recovery_p0_r30_fails_reasoning(self):
        """Context recovery con razonamiento muy débil no debe pasar."""
        _, passes_reasoning = self._simulate_threshold("context_recovery", reasoning_score=30)
        self.assertFalse(passes_reasoning)

    def test_planning_r60_passes(self):
        _, passes_reasoning = self._simulate_threshold("planning", reasoning_score=60)
        self.assertTrue(passes_reasoning)

    def test_planning_r49_fails(self):
        _, passes_reasoning = self._simulate_threshold("planning", reasoning_score=49)
        self.assertFalse(passes_reasoning)

    def test_build_uses_productivity_threshold(self):
        threshold, passes_reasoning = self._simulate_threshold("build", reasoning_score=90)
        self.assertEqual(threshold, 35)
        self.assertFalse(passes_reasoning)  # build siempre usa P-score

    def test_original_session_would_pass_now(self):
        """La sesión del análisis (context_recovery, P=30, R=100) debe pasar."""
        run_type = "context_recovery"
        productivity_score = 30
        reasoning_score = 100  # lead close produjo texto largo = R alto
        threshold, passes_reasoning = self._simulate_threshold(run_type, reasoning_score)
        is_context_query = run_type in ("context_recovery", "planning")
        low_productivity_override = is_context_query and passes_reasoning
        rejected = productivity_score < threshold and not low_productivity_override
        self.assertFalse(rejected, "La sesión de orientación analizada debe aceptarse ahora")


class TestIsContextOnlyQuery(unittest.TestCase):
    def _check(self, message):
        from api.main import _is_context_only_query
        return _is_context_only_query(message)

    def test_de_que_iba(self):
        self.assertTrue(self._check("¿de qué iba el proyecto?"))

    def test_de_que_va(self):
        self.assertTrue(self._check("de qué va esto"))

    def test_resumen(self):
        self.assertTrue(self._check("dame un resumen del estado"))

    def test_que_hemos_hecho(self):
        self.assertTrue(self._check("¿qué hemos hecho hasta ahora?"))

    def test_recuerdas(self):
        self.assertTrue(self._check("¿recuerdas el proyecto?"))

    def test_sabes_de_que_va(self):
        self.assertTrue(self._check("¿sabes de qué va esto?"))

    def test_build_request_is_not_context(self):
        self.assertFalse(self._check("implementa el endpoint de login con JWT"))

    def test_fix_request_is_not_context(self):
        self.assertFalse(self._check("arregla el bug en el router"))

    def test_long_technical_message_is_not_context(self):
        long_msg = "necesito que implementes un sistema de autenticación OAuth2 con refresh tokens, " \
                   "soporte para múltiples proveedores y tests de integración completos"
        self.assertFalse(self._check(long_msg))
