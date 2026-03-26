import unittest

from aiteam.profiles import build_prompt, role_charter_for
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


if __name__ == "__main__":
    unittest.main()
