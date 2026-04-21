import unittest

from aiteam.profiles import build_prompt, build_system_prompt, role_charter_for
from aiteam.tool_specialists import build_tool_specialist_metadata
from aiteam.types import Role


class ProfileGovernanceTests(unittest.TestCase):
    def test_build_prompt_includes_rank_personality_and_justification_rule(self) -> None:
        prompt = build_prompt(Role.ENGINEER, "Implement feature", "Add safe migration")
        self.assertIn("Rango de decision: R4/5", prompt)
        self.assertIn("Personalidad operativa", prompt)
        self.assertIn("justifica la decision final", prompt.lower())
        self.assertIn("Aportes considerados", prompt)

    def test_role_charters_use_varied_decision_ranks(self) -> None:
        ranks = {
            role_charter_for(Role.TEAM_LEAD).decision_rank,
            role_charter_for(Role.RESEARCHER).decision_rank,
            role_charter_for(Role.ENGINEER).decision_rank,
            role_charter_for(Role.REVIEWER).decision_rank,
            role_charter_for(Role.QA).decision_rank,
        }
        self.assertGreaterEqual(len(ranks), 3)

    def test_build_system_prompt_includes_specialist_block_when_metadata_declares_it(self) -> None:
        prompt = build_system_prompt(
            Role.SCOUT,
            task_metadata=build_tool_specialist_metadata(
                specialist="repo_scout",
                required_capabilities=["analysis"],
                reason="leer repo",
            ),
        )
        self.assertIn("Especializacion activa: Repo Scout", prompt)
        self.assertIn("No arbitres producto", prompt)

    def test_team_lead_system_prompt_documents_evidence_plan_directive(self) -> None:
        prompt = build_system_prompt(Role.TEAM_LEAD)
        self.assertIn("[EVIDENCE_PLAN]", prompt)
        self.assertIn("WAIT_POLICY", prompt)
        self.assertIn("JERARQUIA DE EVIDENCIA", prompt)
        self.assertIn("DIRECT_ANSWER", prompt)
        self.assertIn("WORKFLOW_PLAN", prompt)
        self.assertIn("RUN HEALTH REPORT", prompt)
        self.assertIn("CAPACIDADES Y FACTIBILIDAD OPERATIVA", prompt)
        self.assertIn("QUORUM Y CONSULTORIA", prompt)
        self.assertIn("CONTROL OPERATIVO MID-RUN", prompt)
        self.assertIn("PAUSE_FOR_USER", prompt)

    def test_team_lead_direct_coding_prompt_allows_path_blocks(self) -> None:
        prompt = build_prompt(
            Role.TEAM_LEAD,
            "Build",
            "Implement directly",
            task_metadata={"direct_coding_executor": True},
        )

        self.assertIn("path=", prompt)
        self.assertIn("Archivos modificados", prompt)

    def test_team_lead_direct_coding_system_prompt_disables_roles(self) -> None:
        prompt = build_system_prompt(
            Role.TEAM_LEAD,
            task_metadata={"direct_coding_executor": True},
        )

        self.assertIn("Codex/OpenCode", prompt)
        self.assertIn("PROHIBICIONES", prompt)
        self.assertIn("dead code", prompt)

    def test_team_lead_lead_close_system_prompt_requires_current_run_root_cause_only(self) -> None:
        prompt = build_system_prompt(Role.TEAM_LEAD, task_metadata={"phase": "lead_close"})
        self.assertIn("MODO ESTRICTO LEAD_CLOSE", prompt)
        self.assertIn("causa raiz actual", prompt.lower())
        self.assertIn("failure_origin actual", prompt)

    def test_team_lead_charter_covers_replan_quorum_and_capabilities(self) -> None:
        charter = role_charter_for(Role.TEAM_LEAD)
        scope = "\n".join(charter.decision_scope)
        self.assertIn("replan", scope.lower())
        self.assertIn("quorum", scope.lower())
        self.assertIn("capabilities", scope.lower())

    def test_reviewer_system_prompt_documents_verdict_and_artifact_rules(self) -> None:
        prompt = build_system_prompt(Role.REVIEWER)
        self.assertIn("JERARQUIA DE EVIDENCIA", prompt)
        self.assertIn("APPROVED", prompt)
        self.assertIn("CHANGES_REQUESTED", prompt)
        self.assertIn("BLOCKED", prompt)
        self.assertIn("REJECTED", prompt)
        self.assertIn("artefactos", prompt.lower())
        self.assertIn("upstream_context", prompt)

    def test_reviewer_plan_risks_system_prompt_adds_planning_guardrails(self) -> None:
        prompt = build_system_prompt(Role.REVIEWER, task_metadata={"phase": "plan_risks"})
        self.assertIn("MODO ESTRICTO PLAN_RISKS", prompt)
        self.assertIn("quality gates", prompt)
        self.assertIn("Prohibido emitir codigo", prompt)
        self.assertIn("state=completed", prompt)
        self.assertIn("riesgo residual", prompt)
        self.assertIn("bullets cortos y operativos", prompt)
        self.assertIn("modulo CLI existente", prompt)
        self.assertIn("mini plan de implementacion", prompt)
        self.assertIn("decision/gate", prompt)
        self.assertIn("Maximo 2 bullets por seccion", prompt)

    def test_reviewer_execution_prompt_prefers_compact_review_without_regex_literals(self) -> None:
        prompt = build_system_prompt(Role.REVIEWER, task_metadata={"phase": "review_implementation"})
        self.assertIn("MODO ESTRICTO REVIEW", prompt)
        self.assertIn("Hallazgos, Evidencia, Riesgos Residuales, Veredicto", prompt)
        self.assertIn("regex literales", prompt)
        self.assertIn("solicitud original", prompt)

    def test_engineer_plan_engineering_prompt_prefers_single_artifact_and_low_narrative(self) -> None:
        prompt = build_system_prompt(Role.ENGINEER, task_metadata={"phase": "plan_engineering"})
        self.assertIn("MODO ESTRICTO PLAN_ENGINEERING", prompt)
        self.assertIn("unico bloque [PLANNING_ARTIFACT]", prompt)
        self.assertIn("evita narrativa larga", prompt)

    def test_reviewer_charter_mentions_blocking_and_contractual_coherence(self) -> None:
        charter = role_charter_for(Role.REVIEWER)
        scope = "\n".join(charter.decision_scope).lower()
        self.assertIn("blocked", scope)
        self.assertIn("contractual", scope)

    def test_qa_system_prompt_documents_validation_signals_and_decision_states(self) -> None:
        prompt = build_system_prompt(Role.QA)
        self.assertIn("JERARQUIA DE EVIDENCIA", prompt)
        self.assertIn("PASSED", prompt)
        self.assertIn("CONDITIONAL_PASS", prompt)
        self.assertIn("BLOCKED", prompt)
        self.assertIn("FAILED", prompt)
        self.assertIn("coverage", prompt.lower())
        self.assertIn("tests", prompt.lower())
        self.assertIn("criterios de salida", prompt.lower())
        self.assertIn("recovery=...", prompt)
        self.assertIn("bloqueos historicos ya resueltos", prompt)

    def test_qa_charter_mentions_exit_criteria_and_validation_signals(self) -> None:
        charter = role_charter_for(Role.QA)
        scope = "\n".join(charter.decision_scope).lower()
        self.assertIn("exit criteria", scope)
        self.assertIn("validation signals", scope)

    def test_engineer_system_prompt_documents_contractual_and_artifact_rules(self) -> None:
        prompt = build_system_prompt(Role.ENGINEER)
        self.assertIn("JERARQUIA DE EVIDENCIA", prompt)
        self.assertIn("DISCIPLINA CONTRACTUAL", prompt)
        self.assertIn("ARTEFACTOS Y EVIDENCIA MATERIAL", prompt)
        self.assertIn("allowed_module_path_hints", prompt)
        self.assertIn("plan_*", prompt)
        self.assertIn("path=", prompt)

    def test_engineer_plan_engineering_system_prompt_adds_strict_planning_suffix(self) -> None:
        prompt = build_system_prompt(Role.ENGINEER, task_metadata={"phase": "plan_engineering"})
        self.assertIn("MODO ESTRICTO PLAN_ENGINEERING", prompt)
        self.assertIn("[PLANNING_ARTIFACT]", prompt)
        self.assertIn("Prohibido emitir codigo", prompt)

    def test_engineer_charter_mentions_contract_drift_and_material_artifacts(self) -> None:
        charter = role_charter_for(Role.ENGINEER)
        scope = "\n".join(charter.decision_scope).lower()
        self.assertIn("contract drift", scope)
        self.assertIn("material artifacts", scope)

    def test_researcher_system_prompt_documents_facts_uncertainty_and_repo_priority(self) -> None:
        prompt = build_system_prompt(Role.RESEARCHER)
        self.assertIn("JERARQUIA DE EVIDENCIA", prompt)
        self.assertIn("hechos confirmados", prompt.lower())
        self.assertIn("incertidumbres", prompt.lower())
        self.assertIn("recomendacion", prompt.lower())
        self.assertIn("repo", prompt.lower())
        self.assertIn("NO bloquear", prompt)
        self.assertIn("team_lead/lead-2", prompt)
        self.assertIn("IDs de thread", prompt)

    def test_researcher_charter_mentions_uncertainty_and_contradictions(self) -> None:
        charter = role_charter_for(Role.RESEARCHER)
        scope = "\n".join(charter.decision_scope).lower()
        self.assertIn("uncertainty", scope)
        self.assertIn("contradictions", scope)

    def test_scout_system_prompt_documents_factual_briefing_limits(self) -> None:
        prompt = build_system_prompt(Role.SCOUT)
        self.assertIn("JERARQUIA DE EVIDENCIA", prompt)
        self.assertIn("Maximo 8 lineas", prompt)
        self.assertIn("Sin datos disponibles.", prompt)
        self.assertIn("No declares BLOCKED", prompt)
        self.assertIn("sin opinion", prompt.lower())

    def test_scout_charter_mentions_observed_facts_and_gaps(self) -> None:
        charter = role_charter_for(Role.SCOUT)
        scope = "\n".join(charter.decision_scope).lower()
        self.assertIn("observed facts", scope)
        self.assertIn("data gaps", scope)

    # ── Fix D: Engineer prompt delivery rules ────────────────────

    def test_engineer_build_prompt_requires_implementation_not_plan(self) -> None:
        """Engineer's item 5 must say IMPLEMENTACION and forbid bash commands."""
        prompt = build_prompt(Role.ENGINEER, "Build CLI", "Create pyproject.toml")
        self.assertIn("IMPLEMENTACION", prompt)
        # Must NOT have the generic plan wording for engineer
        self.assertNotIn("Plan ejecutable inmediato", prompt)

    def test_team_lead_build_prompt_has_control_specific_item5(self) -> None:
        prompt = build_prompt(Role.TEAM_LEAD, "Lead intake", "Plan current run")
        self.assertIn("WORKFLOW_PLAN o decision de control", prompt)
        self.assertNotIn("Plan ejecutable inmediato", prompt)
        self.assertIn("done/pending/risks/next step", prompt)

    def test_reviewer_build_prompt_has_review_verdict_specific_item5(self) -> None:
        prompt = build_prompt(Role.REVIEWER, "Review build", "Evalua artefactos")
        self.assertIn("Veredicto de review", prompt)
        self.assertIn("APPROVED", prompt)
        self.assertNotIn("Plan ejecutable inmediato", prompt)

    def test_qa_build_prompt_has_validation_decision_specific_item5(self) -> None:
        prompt = build_prompt(Role.QA, "QA build", "Valida entrega")
        self.assertIn("Decision de QA", prompt)
        self.assertIn("PASSED", prompt)
        self.assertNotIn("Plan ejecutable inmediato", prompt)

    def test_researcher_build_prompt_has_research_synthesis_specific_item5(self) -> None:
        prompt = build_prompt(Role.RESEARCHER, "Research current state", "Investiga hechos")
        self.assertIn("Sintesis de investigacion", prompt)
        self.assertIn("Hechos confirmados", prompt)
        self.assertIn("Huecos y contradicciones", prompt)
        self.assertIn("Riesgos y recomendacion", prompt)
        self.assertIn("hechos confirmados", prompt.lower())
        self.assertNotIn("Plan ejecutable inmediato", prompt)
        self.assertNotIn("Decision final y riesgos", prompt)

    def test_scout_build_prompt_has_briefing_specific_item5(self) -> None:
        prompt = build_prompt(Role.SCOUT, "Scout project state", "Resume hechos")
        self.assertIn("Briefing scout", prompt)
        self.assertIn("Objetivo observado", prompt)
        self.assertIn("Estado observable", prompt)
        self.assertIn("Artefactos y datos visibles", prompt)
        self.assertIn("maximo 8 lineas", prompt.lower())
        self.assertNotIn("Plan ejecutable inmediato", prompt)
        self.assertNotIn("Decision final y riesgos", prompt)

    def test_team_lead_build_prompt_uses_control_oriented_sections(self) -> None:
        prompt = build_prompt(Role.TEAM_LEAD, "Lead intake", "Plan current run")
        self.assertIn("Contexto operativo", prompt)
        self.assertIn("Evidencia autoritativa", prompt)
        self.assertIn("Decision de control y riesgos", prompt)

    def test_qa_build_prompt_uses_validation_oriented_sections(self) -> None:
        prompt = build_prompt(Role.QA, "QA build", "Valida entrega")
        self.assertIn("Validaciones ejecutadas", prompt)
        self.assertIn("Cobertura de criterios y gaps", prompt)
        self.assertIn("Decision de QA y riesgos", prompt)

    def test_reviewer_build_prompt_uses_review_oriented_sections(self) -> None:
        prompt = build_prompt(Role.REVIEWER, "Review build", "Evalua artefactos")
        self.assertIn("Hallazgos principales", prompt)
        self.assertIn("Veredicto y riesgos", prompt)

    def test_engineer_build_prompt_does_not_use_broken_write_file_format(self) -> None:
        """Engineer prompt must NOT use filesystem_mcp:write_file (wrong format + {} content bug).
        Primary file-writing mechanism is path= annotation, not USE_TOOL write_file."""
        prompt = build_prompt(Role.ENGINEER, "Build CLI", "Create src/sample_cli/cli.py")
        # Should NOT have the broken colon-syntax shorthand (breaks args regex for {} content)
        self.assertNotIn("filesystem_mcp:write_file", prompt)
        # Should NOT have write_file at all in build_prompt (that comes from tool_dispatch)
        self.assertNotIn("write_file", prompt)

    def test_engineer_build_prompt_mentions_path_annotation(self) -> None:
        """Engineer prompt must reference path= annotation as fallback."""
        prompt = build_prompt(Role.ENGINEER, "Build CLI", "Create files")
        self.assertIn("path=", prompt)

    def test_engineer_system_prompt_forbids_bash_plans(self) -> None:
        """Engineer system prompt must forbid bash commands and plans."""
        prompt = build_system_prompt(Role.ENGINEER)
        self.assertIn("NUNCA", prompt)
        self.assertIn("bash", prompt)
        self.assertIn("NO escribir codigo", prompt)

    def test_researcher_system_prompt_constrains_peer_blocking(self) -> None:
        """Researcher system prompt must not tell engineer to investigate instead of building."""
        from aiteam.profiles import DEFAULT_PROFILES
        system = DEFAULT_PROFILES[Role.RESEARCHER].system_prompt
        self.assertIn("NO bloquear", system)
        self.assertIn("PEER INPUT", system)


if __name__ == "__main__":
    unittest.main()
