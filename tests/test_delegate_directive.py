"""E7-C / E8-B4: Tests del mecanismo de delegacion bajo demanda (DELEGATE).

Cubre:
- _extract_delegate_directive: deteccion y extraccion de la query
- Directivas malformadas se ignoran
- Coexistencia con otras directivas LCP
- _extract_lcp_directives NO extrae DELEGATE (es un helper independiente)
- _strip_lcp_directives SÍ elimina DELEGATE del output visible
"""
import unittest

from api.main import (
    _extract_delegate_directive,
    _extract_lcp_directives,
    _strip_lcp_directives,
)


class TestExtractDelegateDirective(unittest.TestCase):

    # ── Deteccion basica ──────────────────────────────────────────────

    def test_delegate_detected(self):
        text = '[DELEGATE: "¿Qué version de Python usa el proyecto?"]'
        result = _extract_delegate_directive(text)
        self.assertEqual(result, "¿Qué version de Python usa el proyecto?")

    def test_delegate_with_surrounding_text(self):
        text = (
            "Necesito mas informacion antes de planificar.\n"
            '[DELEGATE: "lista las dependencias de requirements.txt"]'
        )
        result = _extract_delegate_directive(text)
        self.assertEqual(result, "lista las dependencias de requirements.txt")

    def test_delegate_query_stripped(self):
        # Espacios internos en la query se preservan
        text = '[DELEGATE: "  ¿cuantos tests hay en el proyecto?  "]'
        result = _extract_delegate_directive(text)
        self.assertEqual(result, "¿cuantos tests hay en el proyecto?")

    def test_delegate_multiline_query(self):
        # El parser maneja queries con saltos de linea (re.DOTALL)
        text = '[DELEGATE: "primera linea\nsegunda linea"]'
        result = _extract_delegate_directive(text)
        self.assertIn("primera linea", result)
        self.assertIn("segunda linea", result)

    # ── Directivas malformadas ────────────────────────────────────────

    def test_delegate_without_quotes_ignored(self):
        result = _extract_delegate_directive("[DELEGATE: consulta sin comillas]")
        self.assertIsNone(result)

    def test_delegate_empty_returns_none(self):
        result = _extract_delegate_directive("")
        self.assertIsNone(result)

    def test_delegate_none_returns_none(self):
        result = _extract_delegate_directive(None)  # type: ignore
        self.assertIsNone(result)

    def test_delegate_not_present(self):
        result = _extract_delegate_directive("Texto normal sin directiva delegate.")
        self.assertIsNone(result)

    def test_delegate_unclosed_bracket_ignored(self):
        result = _extract_delegate_directive('[DELEGATE: "consulta sin cerrar')
        self.assertIsNone(result)

    # ── Case sensitivity ──────────────────────────────────────────────

    def test_delegate_case_insensitive(self):
        text = '[delegate: "¿cuál es el estado del repo?"]'
        result = _extract_delegate_directive(text)
        self.assertEqual(result, "¿cuál es el estado del repo?")

    def test_delegate_mixed_case(self):
        text = '[Delegate: "version de node"]'
        result = _extract_delegate_directive(text)
        self.assertEqual(result, "version de node")

    # ── Interaccion con otras directivas ─────────────────────────────

    def test_delegate_with_workflow_plan(self):
        text = (
            "Necesito saber mas antes de planificar.\n"
            '[DELEGATE: "dame el contenido de package.json"]\n'
            "[WORKFLOW_PLAN]\nphase_id: build\nrole: ENGINEER\n[/WORKFLOW_PLAN]"
        )
        result = _extract_delegate_directive(text)
        self.assertIsNotNone(result)
        self.assertIn("package.json", result)

    def test_only_first_delegate_extracted(self):
        # re.search devuelve el primero; no hay ambiguedad
        text = (
            '[DELEGATE: "primera consulta"]\n'
            '[DELEGATE: "segunda consulta"]'
        )
        result = _extract_delegate_directive(text)
        self.assertEqual(result, "primera consulta")


class TestDelegateNotInLcpDirectives(unittest.TestCase):
    """_extract_lcp_directives no maneja DELEGATE (tiene su propio extractor)."""

    def test_lcp_directives_does_not_include_delegate(self):
        text = '[DELEGATE: "¿qué hay en requirements.txt?"]'
        result = _extract_lcp_directives(text)
        self.assertNotIn("delegate", result)

    def test_lcp_directives_coexist_with_delegate(self):
        text = (
            '[DELEGATE: "version Python"]\n'
            "[ESCALATE: complexity=high]\n"
            "[EXTEND_BUDGET: +5]"
        )
        lcp = _extract_lcp_directives(text)
        # ESCALATE y EXTEND_BUDGET se extraen normalmente
        self.assertIn("escalate", lcp)
        self.assertIn("extend_budget", lcp)
        # DELEGATE no
        self.assertNotIn("delegate", lcp)


class TestStripDelegateDirective(unittest.TestCase):
    """_strip_lcp_directives elimina [DELEGATE...] del output visible."""

    def test_strips_delegate_directive(self):
        text = (
            "Necesito informacion adicional.\n"
            '[DELEGATE: "¿cuál es la version de Python?"]'
        )
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[DELEGATE", clean)
        self.assertIn("Necesito informacion adicional", clean)

    def test_strips_delegate_among_other_directives(self):
        text = (
            "Analisis inicial.\n"
            '[DELEGATE: "lista de tests"]\n'
            "[ESCALATE: complexity=high]\n"
            "[EXTEND_BUDGET: +3]"
        )
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[DELEGATE", clean)
        self.assertNotIn("[ESCALATE", clean)
        self.assertNotIn("[EXTEND_BUDGET", clean)
        self.assertIn("Analisis inicial", clean)

    def test_no_directives_unchanged(self):
        text = "Texto limpio sin ninguna directiva."
        clean = _strip_lcp_directives(text)
        self.assertEqual(clean, text)


class TestAddPhaseDirectiveParser(unittest.TestCase):
    """Tests del parser de [ADD_PHASE] que ya existe en _extract_lcp_directives."""

    def test_add_phase_engineer(self):
        text = '[ADD_PHASE: ENGINEER "Exportar backup antes de migrar"]'
        result = _extract_lcp_directives(text)
        ap = result.get("add_phase", {})
        self.assertEqual(ap.get("role"), "ENGINEER")
        self.assertEqual(ap.get("objective"), "Exportar backup antes de migrar")

    def test_add_phase_researcher(self):
        text = '[ADD_PHASE: RESEARCHER "Auditar endpoints de autenticacion"]'
        result = _extract_lcp_directives(text)
        self.assertEqual(result["add_phase"]["role"], "RESEARCHER")
        self.assertIn("autenticacion", result["add_phase"]["objective"])

    def test_add_phase_reviewer(self):
        text = '[ADD_PHASE: REVIEWER "Revisar impacto en APIs publicas"]'
        result = _extract_lcp_directives(text)
        self.assertEqual(result["add_phase"]["role"], "REVIEWER")

    def test_add_phase_qa(self):
        text = '[ADD_PHASE: QA "Validar regresiones en modulo de pagos"]'
        result = _extract_lcp_directives(text)
        self.assertEqual(result["add_phase"]["role"], "QA")

    def test_add_phase_combined_with_skip(self):
        text = (
            '[SKIP: "review"]\n'
            '[ADD_PHASE: ENGINEER "Crear script de rollback"]'
        )
        result = _extract_lcp_directives(text)
        self.assertIn("skip", result)
        self.assertIn("add_phase", result)
        self.assertEqual(result["add_phase"]["role"], "ENGINEER")

    def test_add_phase_with_all_directives(self):
        text = (
            "Output del Lead.\n"
            "[ESCALATE: complexity=very_high criticality=critical]\n"
            '[SKIP: "review"]\n'
            "[EXTEND_BUDGET: +10]\n"
            '[ADD_PHASE: ENGINEER "Backup de datos antes de migrar"]'
        )
        result = _extract_lcp_directives(text)
        self.assertIn("escalate", result)
        self.assertIn("skip", result)
        self.assertIn("extend_budget", result)
        self.assertIn("add_phase", result)
        self.assertEqual(result["add_phase"]["objective"], "Backup de datos antes de migrar")

    def test_add_phase_stripped_from_output(self):
        text = 'Plan generado. [ADD_PHASE: ENGINEER "Script de limpieza"]'
        clean = _strip_lcp_directives(text)
        self.assertNotIn("[ADD_PHASE", clean)
        self.assertIn("Plan generado", clean)


if __name__ == "__main__":
    unittest.main()
