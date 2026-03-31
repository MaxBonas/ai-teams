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


if __name__ == "__main__":
    unittest.main()
