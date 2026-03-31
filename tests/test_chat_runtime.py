import unittest

from aiteam.chat_runtime import ChatRunState
from aiteam.types import Complexity, Criticality, Role
from aiteam.workflow_planner import PhaseSpec


class ChatRunStateTests(unittest.TestCase):
    def test_builds_phase_and_delegated_ids(self) -> None:
        state = ChatRunState(
            chat_root="CHAT-ABC12345",
            lead_task_id="CHAT-ABC12345::lead_intake",
            preferred_role=Role.TEAM_LEAD,
            chat_mode="sprint5",
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            round_budget=5,
            phases=[
                PhaseSpec(
                    phase_id="discovery",
                    role="RESEARCHER",
                    objective="Investigar",
                    depends_on=[],
                ),
                PhaseSpec(
                    phase_id="build",
                    role="ENGINEER",
                    objective="Implementar",
                    depends_on=["discovery"],
                ),
            ],
        )

        self.assertEqual(
            state.phase_task_ids["lead_intake"], "CHAT-ABC12345::lead_intake"
        )
        self.assertEqual(state.phase_task_ids["discovery"], "CHAT-ABC12345::discovery")
        self.assertEqual(state.phase_task_ids["lead_close"], "CHAT-ABC12345::lead_close")
        self.assertEqual(
            state.workflow_phase_keys,
            ["lead_intake", "discovery", "build", "lead_close"],
        )
        self.assertEqual(
            state.delegated_task_ids,
            ["CHAT-ABC12345::discovery", "CHAT-ABC12345::build"],
        )

    def test_dependency_ids_for_resolves_phase_dependencies(self) -> None:
        state = ChatRunState(
            chat_root="CHAT-XYZ98765",
            lead_task_id="CHAT-XYZ98765::lead_intake",
            preferred_role=Role.TEAM_LEAD,
            chat_mode="classic",
            complexity=Complexity.HIGH,
            criticality=Criticality.HIGH,
            round_budget=8,
            phases=[
                PhaseSpec(
                    phase_id="plan",
                    role="RESEARCHER",
                    objective="Planificar",
                    depends_on=[],
                ),
                PhaseSpec(
                    phase_id="build",
                    role="ENGINEER",
                    objective="Construir",
                    depends_on=["plan"],
                ),
            ],
        )

        plan_spec = state.phases[0]
        build_spec = state.phases[1]
        self.assertEqual(
            state.dependency_ids_for(plan_spec),
            ["CHAT-XYZ98765::lead_intake"],
        )
        self.assertEqual(
            state.dependency_ids_for(build_spec),
            ["CHAT-XYZ98765::lead_intake", "CHAT-XYZ98765::plan"],
        )

    def test_roundtrip_serialization_preserves_canonical_state(self) -> None:
        state = ChatRunState(
            chat_root="CHAT-RTT12345",
            lead_task_id="CHAT-RTT12345::lead_intake",
            preferred_role=Role.ENGINEER,
            chat_mode="sprint5",
            complexity=Complexity.HIGH,
            criticality=Criticality.MEDIUM,
            round_budget=11,
            phases=[
                PhaseSpec(
                    phase_id="discovery",
                    role="RESEARCHER",
                    objective="Investigar dependencias",
                    depends_on=[],
                ),
                PhaseSpec(
                    phase_id="review",
                    role="REVIEWER",
                    objective="Revisar hallazgos",
                    depends_on=["discovery"],
                ),
            ],
            phase_evidence_plan={
                "review": {
                    "delegate_intents": ["delegate_repo_scan"],
                    "wait_policy": "all",
                    "delegate_budget": 3,
                }
            },
        )

        restored = ChatRunState.from_dict(state.to_dict())

        self.assertEqual(restored.chat_root, state.chat_root)
        self.assertEqual(restored.lead_task_id, state.lead_task_id)
        self.assertEqual(restored.preferred_role, Role.ENGINEER)
        self.assertEqual(restored.chat_mode, "sprint5")
        self.assertEqual(restored.complexity, Complexity.HIGH)
        self.assertEqual(restored.criticality, Criticality.MEDIUM)
        self.assertEqual(restored.round_budget, 11)
        self.assertEqual(restored.workflow_phase_keys, state.workflow_phase_keys)
        self.assertEqual(restored.phase_task_ids, state.phase_task_ids)
        self.assertEqual(restored.delegated_task_ids, state.delegated_task_ids)
        self.assertEqual(restored.phase_evidence_plan, state.phase_evidence_plan)

    def test_from_dict_accepts_role_name_format(self) -> None:
        restored = ChatRunState.from_dict(
            {
                "chat_root": "CHAT-ROLE0001",
                "lead_task_id": "CHAT-ROLE0001::lead_intake",
                "preferred_role": "TEAM_LEAD",
                "chat_mode": "classic",
                "complexity": "medium",
                "criticality": "high",
                "round_budget": 6,
                "phases": [],
            }
        )

        self.assertEqual(restored.preferred_role, Role.TEAM_LEAD)
        self.assertEqual(restored.workflow_phase_keys, ["lead_intake", "lead_close"])


if __name__ == "__main__":
    unittest.main()
