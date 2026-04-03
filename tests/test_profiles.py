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

    # ── Fix D: Engineer prompt delivery rules ────────────────────

    def test_engineer_build_prompt_requires_implementation_not_plan(self) -> None:
        """Engineer's item 5 must say IMPLEMENTACION and forbid bash commands."""
        prompt = build_prompt(Role.ENGINEER, "Build CLI", "Create pyproject.toml")
        self.assertIn("IMPLEMENTACION", prompt)
        # Must NOT have the generic plan wording for engineer
        self.assertNotIn("Plan ejecutable inmediato", prompt)

    def test_non_engineer_build_prompt_keeps_plan_format(self) -> None:
        """Other roles still get 'Plan ejecutable inmediato' in item 5."""
        for role in (Role.TEAM_LEAD, Role.REVIEWER, Role.QA):
            prompt = build_prompt(role, "Task", "Description")
            self.assertIn("Plan ejecutable inmediato", prompt, f"Role {role} should still have plan format")

    def test_engineer_build_prompt_mentions_use_tool(self) -> None:
        """Engineer prompt must reference USE_TOOL write_file so Engineer knows the mechanism."""
        prompt = build_prompt(Role.ENGINEER, "Build CLI", "Create src/md_report/cli.py")
        self.assertIn("USE_TOOL", prompt)
        self.assertIn("write_file", prompt)

    def test_engineer_build_prompt_mentions_path_annotation(self) -> None:
        """Engineer prompt must reference path= annotation as fallback."""
        prompt = build_prompt(Role.ENGINEER, "Build CLI", "Create files")
        self.assertIn("path=", prompt)

    def test_engineer_system_prompt_forbids_bash_plans(self) -> None:
        """Engineer system prompt must forbid bash commands and plans."""
        prompt = build_system_prompt(Role.ENGINEER)
        self.assertIn("NUNCA", prompt)
        self.assertIn("bash", prompt)

    def test_researcher_system_prompt_constrains_peer_blocking(self) -> None:
        """Researcher system prompt must not tell engineer to investigate instead of building."""
        from aiteam.profiles import DEFAULT_PROFILES
        system = DEFAULT_PROFILES[Role.RESEARCHER].system_prompt
        self.assertIn("NO bloquear", system)
        self.assertIn("PEER INPUT", system)


if __name__ == "__main__":
    unittest.main()
