"""Tests para aiteam/workflow_planner.py"""
import pytest
from aiteam.workflow_planner import (
    PhaseSpec,
    parse_workflow_plan,
    default_phases,
    infer_role_from_phase_id,
    normalize_role,
    _no_cycles,
    _parse_phase_blocks,
)


# ---------------------------------------------------------------------------
# Fixtures de output del Lead
# ---------------------------------------------------------------------------

VALID_PLAN_SIMPLE = """
Analice la solicitud. Se necesitan 3 fases.

[WORKFLOW_PLAN]
- phase_id: research
  role: RESEARCHER
  objective: Investigar restricciones de la API
  depends_on: []
- phase_id: implement
  role: ENGINEER
  objective: Implementar el endpoint con validacion
  depends_on: [research]
- phase_id: qa
  role: QA
  objective: Verificar flujo end-to-end
  depends_on: [implement]
[/WORKFLOW_PLAN]

Siguiente accion: comenzar con research.
"""

VALID_PLAN_PARALLEL = """
[WORKFLOW_PLAN]
- phase_id: security_check
  role: REVIEWER
  objective: Revisar requisitos de seguridad
  depends_on: []
- phase_id: api_design
  role: ENGINEER
  objective: Disenar la API
  depends_on: []
- phase_id: build
  role: ENGINEER
  objective: Implementar la solucion
  depends_on: [security_check, api_design]
- phase_id: qa
  role: QA
  objective: Validar acceptance criteria
  depends_on: [build]
[/WORKFLOW_PLAN]
"""

PLAN_WITH_CYCLE = """
[WORKFLOW_PLAN]
- phase_id: phase_a
  role: ENGINEER
  objective: Fase A
  depends_on: [phase_b]
- phase_id: phase_b
  role: ENGINEER
  objective: Fase B
  depends_on: [phase_a]
[/WORKFLOW_PLAN]
"""

PLAN_INVALID_ROLE = """
[WORKFLOW_PLAN]
- phase_id: planning
  role: TEAM_LEAD
  objective: Planificar
  depends_on: []
[/WORKFLOW_PLAN]
"""

PLAN_BAD_DEP = """
[WORKFLOW_PLAN]
- phase_id: build
  role: ENGINEER
  objective: Implementar
  depends_on: [nonexistent_phase]
[/WORKFLOW_PLAN]
"""

PLAN_RESERVED_ID = """
[WORKFLOW_PLAN]
- phase_id: lead_intake
  role: ENGINEER
  objective: Intentar sobreescribir el intake
  depends_on: []
[/WORKFLOW_PLAN]
"""

NO_PLAN_TEXT = """
Analice la solicitud. Voy a proceder directamente.
Entrega: objetivos y riesgos identificados.
"""

PLAN_LOWERCASE_ROLE = """
[WORKFLOW_PLAN]
- phase_id: research
  role: researcher
  objective: Investigar
  depends_on: []
[/WORKFLOW_PLAN]
"""

PLAN_INFERRED_ROLE = """
[WORKFLOW_PLAN]
- phase_id: security_audit
  objective: Auditar seguridad
  depends_on: []
[/WORKFLOW_PLAN]
"""

PLAN_EMPTY_DEPS = """
[WORKFLOW_PLAN]
- phase_id: research
  role: RESEARCHER
  objective: Investigar
  depends_on: []
- phase_id: build
  role: ENGINEER
  objective: Construir
  depends_on: [research]
[/WORKFLOW_PLAN]
"""

PLAN_MULTILINE_OBJECTIVE = """
[WORKFLOW_PLAN]
- phase_id: verify_codebase_state
  role: RESEARCHER
  objective: |
    Validar estado actual del repositorio
    Confirmar imports en tests y dependencia markdown
  depends_on: []
- phase_id: fix_test_imports
  role: ENGINEER
  objective: |
    Corregir imports en test_cli.py
    Ajustar referencias al paquete principal
  depends_on: [verify_codebase_state]
[/WORKFLOW_PLAN]
"""


# ---------------------------------------------------------------------------
# parse_workflow_plan — casos validos
# ---------------------------------------------------------------------------

class TestParseWorkflowPlanValid:
    def test_simple_plan_returns_three_phases(self):
        result = parse_workflow_plan(VALID_PLAN_SIMPLE)
        assert result is not None
        assert len(result) == 3

    def test_phase_ids_correct(self):
        result = parse_workflow_plan(VALID_PLAN_SIMPLE)
        ids = [p.phase_id for p in result]
        assert ids == ["research", "implement", "qa"]

    def test_roles_correct(self):
        result = parse_workflow_plan(VALID_PLAN_SIMPLE)
        assert result[0].role == "RESEARCHER"
        assert result[1].role == "ENGINEER"
        assert result[2].role == "QA"

    def test_objectives_not_empty(self):
        result = parse_workflow_plan(VALID_PLAN_SIMPLE)
        for spec in result:
            assert spec.objective.strip()

    def test_dependencies_chain(self):
        result = parse_workflow_plan(VALID_PLAN_SIMPLE)
        assert result[0].depends_on == []
        assert result[1].depends_on == ["research"]
        assert result[2].depends_on == ["implement"]

    def test_parallel_phases(self):
        result = parse_workflow_plan(VALID_PLAN_PARALLEL)
        assert result is not None
        assert len(result) == 4
        build = next(p for p in result if p.phase_id == "build")
        assert set(build.depends_on) == {"security_check", "api_design"}

    def test_lowercase_role_accepted(self):
        result = parse_workflow_plan(PLAN_LOWERCASE_ROLE)
        assert result is not None
        assert result[0].role == "RESEARCHER"

    def test_empty_depends_on_brackets(self):
        result = parse_workflow_plan(PLAN_EMPTY_DEPS)
        assert result is not None
        assert result[0].depends_on == []
        assert result[1].depends_on == ["research"]

    def test_plan_surrounded_by_text(self):
        """El bloque puede estar en medio de texto del Lead."""
        result = parse_workflow_plan(VALID_PLAN_SIMPLE)
        assert result is not None

    def test_multiline_objective_block_is_preserved(self):
        result = parse_workflow_plan(PLAN_MULTILINE_OBJECTIVE)
        assert result is not None
        assert len(result) == 2
        assert "Validar estado actual del repositorio" in result[0].objective
        assert "Confirmar imports en tests" in result[0].objective
        assert "Corregir imports en test_cli.py" in result[1].objective


# ---------------------------------------------------------------------------
# parse_workflow_plan — casos invalidos / fallback a None
# ---------------------------------------------------------------------------

class TestParseWorkflowPlanInvalid:
    def test_no_plan_block_returns_none(self):
        assert parse_workflow_plan(NO_PLAN_TEXT) is None

    def test_empty_string_returns_none(self):
        assert parse_workflow_plan("") is None

    def test_cycle_returns_none(self):
        assert parse_workflow_plan(PLAN_WITH_CYCLE) is None

    def test_invalid_role_team_lead_returns_none(self):
        assert parse_workflow_plan(PLAN_INVALID_ROLE) is None

    def test_nonexistent_dependency_returns_none(self):
        assert parse_workflow_plan(PLAN_BAD_DEP) is None

    def test_reserved_phase_id_returns_none(self):
        assert parse_workflow_plan(PLAN_RESERVED_ID) is None

    def test_too_many_phases_returns_none(self):
        phases = "\n".join(
            f"- phase_id: phase_{i}\n  role: ENGINEER\n  objective: Fase {i}\n  depends_on: []"
            for i in range(11)
        )
        text = f"[WORKFLOW_PLAN]\n{phases}\n[/WORKFLOW_PLAN]"
        assert parse_workflow_plan(text) is None


# ---------------------------------------------------------------------------
# parse_workflow_plan — rol inferido desde phase_id
# ---------------------------------------------------------------------------

class TestInferredRole:
    def test_security_audit_infers_reviewer(self):
        result = parse_workflow_plan(PLAN_INFERRED_ROLE)
        assert result is not None
        assert result[0].role == "REVIEWER"

    def test_infer_role_from_phase_id_research(self):
        assert infer_role_from_phase_id("research") == "RESEARCHER"
        assert infer_role_from_phase_id("api_research") == "RESEARCHER"

    def test_infer_role_from_phase_id_build(self):
        assert infer_role_from_phase_id("build") == "ENGINEER"
        assert infer_role_from_phase_id("build_service") == "ENGINEER"

    def test_infer_role_from_phase_id_review(self):
        assert infer_role_from_phase_id("review") == "REVIEWER"
        assert infer_role_from_phase_id("security_review") == "REVIEWER"

    def test_infer_role_from_phase_id_qa(self):
        assert infer_role_from_phase_id("qa") == "QA"
        assert infer_role_from_phase_id("qa_acceptance") == "QA"

    def test_infer_role_unknown_defaults_to_engineer(self):
        assert infer_role_from_phase_id("random_phase") == "ENGINEER"
        assert infer_role_from_phase_id("xyz") == "ENGINEER"


# ---------------------------------------------------------------------------
# normalize_role
# ---------------------------------------------------------------------------

class TestNormalizeRole:
    def test_uppercase_passthrough(self):
        assert normalize_role("ENGINEER") == "ENGINEER"
        assert normalize_role("RESEARCHER") == "RESEARCHER"
        assert normalize_role("REVIEWER") == "REVIEWER"
        assert normalize_role("QA") == "QA"

    def test_lowercase_normalized(self):
        assert normalize_role("engineer") == "ENGINEER"
        assert normalize_role("researcher") == "RESEARCHER"

    def test_team_lead_returns_none(self):
        assert normalize_role("TEAM_LEAD") is None
        assert normalize_role("LEAD") is None

    def test_short_alias_eng(self):
        assert normalize_role("ENG") == "ENGINEER"


# ---------------------------------------------------------------------------
# default_phases — identico al comportamiento anterior
# ---------------------------------------------------------------------------

class TestDefaultPhases:
    def test_classic_phases_order(self):
        phases = default_phases("classic")
        ids = [p.phase_id for p in phases]
        assert ids == ["discovery", "build", "review", "qa"]

    def test_classic_roles(self):
        phases = default_phases("classic")
        role_map = {p.phase_id: p.role for p in phases}
        assert role_map["discovery"] == "RESEARCHER"
        assert role_map["build"] == "ENGINEER"
        assert role_map["review"] == "REVIEWER"
        assert role_map["qa"] == "QA"

    def test_classic_dependency_chain(self):
        phases = default_phases("classic")
        dep_map = {p.phase_id: p.depends_on for p in phases}
        assert dep_map["discovery"] == []
        assert dep_map["build"] == ["discovery"]
        assert dep_map["review"] == ["build"]
        assert dep_map["qa"] == ["review"]

    def test_sprint5_phases_order(self):
        phases = default_phases("sprint5")
        ids = [p.phase_id for p in phases]
        assert ids == [
            "plan_research",
            "plan_engineering",
            "plan_risks",
            "build",
            "review",
            "qa",
        ]

    def test_sprint5_roles(self):
        phases = default_phases("sprint5")
        role_map = {p.phase_id: p.role for p in phases}
        assert role_map["plan_research"] == "RESEARCHER"
        assert role_map["plan_engineering"] == "ENGINEER"
        assert role_map["plan_risks"] == "REVIEWER"
        assert role_map["build"] == "ENGINEER"
        assert role_map["review"] == "REVIEWER"
        assert role_map["qa"] == "QA"

    def test_sprint5_build_depends_on_all_plans(self):
        phases = default_phases("sprint5")
        build = next(p for p in phases if p.phase_id == "build")
        assert set(build.depends_on) == {"plan_engineering", "plan_risks"}

    def test_sprint5_build_objective_forbids_slice_drift(self):
        phases = default_phases("sprint5")
        build = next(p for p in phases if p.phase_id == "build")
        assert "exactamente el slice aprobado" in build.objective.lower()
        assert "mayor impacto" not in build.objective.lower()

    def test_unknown_mode_returns_sprint5(self):
        phases = default_phases("unknown_mode")
        ids = [p.phase_id for p in phases]
        assert "plan_research" in ids

    def test_all_phases_have_objectives(self):
        for mode in ("classic", "sprint5"):
            for p in default_phases(mode):
                assert p.objective.strip(), f"phase {p.phase_id} missing objective"

    def test_default_phases_pass_validation(self):
        """Las fases por defecto deben pasar _no_cycles."""
        for mode in ("classic", "sprint5"):
            phases = default_phases(mode)
            assert _no_cycles(phases), f"cycles detected in default_phases({mode!r})"


# ---------------------------------------------------------------------------
# DAG cycle detection
# ---------------------------------------------------------------------------

class TestNoCycles:
    def test_linear_chain_no_cycle(self):
        phases = [
            PhaseSpec("a", "ENGINEER", "A", []),
            PhaseSpec("b", "ENGINEER", "B", ["a"]),
            PhaseSpec("c", "ENGINEER", "C", ["b"]),
        ]
        assert _no_cycles(phases) is True

    def test_direct_cycle_detected(self):
        phases = [
            PhaseSpec("a", "ENGINEER", "A", ["b"]),
            PhaseSpec("b", "ENGINEER", "B", ["a"]),
        ]
        assert _no_cycles(phases) is False

    def test_indirect_cycle_detected(self):
        phases = [
            PhaseSpec("a", "ENGINEER", "A", []),
            PhaseSpec("b", "ENGINEER", "B", ["a"]),
            PhaseSpec("c", "ENGINEER", "C", ["b"]),
            PhaseSpec("a2", "ENGINEER", "A2", ["c"]),  # a2 depende de c
        ]
        # No es ciclo, a2 es un nombre distinto
        assert _no_cycles(phases) is True

    def test_parallel_phases_no_cycle(self):
        phases = [
            PhaseSpec("a", "ENGINEER", "A", []),
            PhaseSpec("b", "RESEARCHER", "B", []),
            PhaseSpec("c", "ENGINEER", "C", ["a", "b"]),
        ]
        assert _no_cycles(phases) is True

    def test_self_dependency_detected(self):
        phases = [
            PhaseSpec("a", "ENGINEER", "A", ["a"]),
        ]
        assert _no_cycles(phases) is False
