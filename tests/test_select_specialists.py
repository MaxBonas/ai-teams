"""
Tests para select_specialists_for_task() y SpecialistRoster.

Verifica que el sistema de composición automática de rosters multispecialista
seleccione el conjunto correcto de operadores baratos según el contexto de la tarea.
"""
from __future__ import annotations

import unittest

from aiteam.tool_specialists import (
    SpecialistRoster,
    select_specialists_for_task,
)
from aiteam.types import Complexity, Criticality, Role


class TestSelectSpecialistsBasic(unittest.TestCase):
    """Casos base: selección de especialista único."""

    def test_lsp_targets_select_lsp_navigator(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["coding"],
            lsp_targets=["symbols", "references"],
        )
        self.assertIn("lsp_navigator", roster.specialists)

    def test_skill_targets_select_skill_worker(self):
        roster = select_specialists_for_task(
            role=Role.SCOUT,
            required_capabilities=["skill_run"],
            skill_targets=["deploy", "migrate"],
        )
        self.assertIn("skill_worker", roster.specialists)

    def test_browser_capabilities_select_browser_operator(self):
        roster = select_specialists_for_task(
            role=Role.QA,
            required_capabilities=["browser_test"],
        )
        self.assertIn("browser_operator", roster.specialists)

    def test_test_execute_capability_selects_test_runner(self):
        roster = select_specialists_for_task(
            role=Role.QA,
            required_capabilities=["test_execute"],
        )
        self.assertIn("test_runner", roster.specialists)

    def test_repo_read_scout_selects_repo_scout(self):
        roster = select_specialists_for_task(
            role=Role.SCOUT,
            required_capabilities=["repo_read"],
        )
        self.assertIn("repo_scout", roster.specialists)

    def test_mcp_capability_with_available_servers_selects_mcp_operator(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["external_mcp"],
            available_mcp_servers=["filesystem", "fetch"],
        )
        self.assertIn("mcp_operator", roster.specialists)

    def test_mcp_capability_without_servers_does_not_select_mcp_operator(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["external_mcp"],
            available_mcp_servers=[],
        )
        self.assertNotIn("mcp_operator", roster.specialists)

    def test_rewiring_metadata_suppresses_mcp_operator_in_favor_of_skill_worker(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["external_mcp"],
            available_mcp_servers=["filesystem"],
            metadata={
                "tool_rewiring_suppress_mcp_operator": True,
                "tool_rewiring_candidates": ["semgrep_security_skill"],
                "tool_rewiring_preferred_specialist": "skill_worker",
            },
        )
        self.assertIn("skill_worker", roster.specialists)
        self.assertNotIn("mcp_operator", roster.specialists)

    def test_continuation_requested_for_team_lead_adds_context_curator(self):
        roster = select_specialists_for_task(
            role=Role.TEAM_LEAD,
            required_capabilities=["reasoning"],
            metadata={"continuation_requested": True},
        )
        self.assertIn("context_curator", roster.specialists)

    def test_context_pressure_recommended_adds_context_curator(self):
        roster = select_specialists_for_task(
            role=Role.REVIEWER,
            required_capabilities=["review"],
            metadata={
                "context_curator_recommended": True,
                "context_pressure_level": "medium",
            },
        )
        self.assertIn("context_curator", roster.specialists)

    def test_context_compaction_priority_promotes_context_curator_to_front(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["browser_test", "test_execute"],
            complexity=Complexity.HIGH,
            metadata={
                "context_curator_recommended": True,
                "context_compaction_priority_boost": True,
                "context_compaction_value_level": "high",
                "estimated_context_tokens_saved": 640,
            },
        )
        self.assertTrue(roster.specialists)
        self.assertEqual(roster.specialists[0], "context_curator")


class TestSelectSpecialistsComposition(unittest.TestCase):
    """Composición de rosters multispecialista."""

    def test_high_complexity_engineer_adds_repo_scout(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["coding"],
            complexity=Complexity.HIGH,
        )
        self.assertIn("repo_scout", roster.specialists)

    def test_medium_complexity_engineer_no_repo_scout(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["coding"],
            complexity=Complexity.MEDIUM,
        )
        self.assertNotIn("repo_scout", roster.specialists)

    def test_lsp_targets_prevent_redundant_repo_scout(self):
        """Si lsp_navigator ya cubre contexto, no añadir repo_scout."""
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["coding"],
            complexity=Complexity.HIGH,
            lsp_targets=["symbols", "impact"],
        )
        self.assertIn("lsp_navigator", roster.specialists)
        self.assertNotIn("repo_scout", roster.specialists)

    def test_roster_deduplicates_specialists(self):
        roster = select_specialists_for_task(
            role=Role.SCOUT,
            required_capabilities=["repo_read"],
            skill_targets=["deploy"],
        )
        self.assertEqual(len(roster.specialists), len(set(roster.specialists)))

    def test_roster_max_three_specialists(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["browser_test", "test_execute", "external_mcp"],
            complexity=Complexity.HIGH,
            lsp_targets=["symbols"],
            skill_targets=["migrate"],
            available_mcp_servers=["filesystem"],
        )
        self.assertLessEqual(len(roster.specialists), 3)

    def test_explicit_roster_in_metadata_respected(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["coding"],
            metadata={"specialist_roster": ["repo_scout", "test_runner"]},
        )
        self.assertEqual(roster.specialists, ["repo_scout", "test_runner"])
        self.assertEqual(roster.reasoning, "explicit_roster_from_metadata")

    def test_invalid_specialist_in_metadata_filtered(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["coding"],
            metadata={"specialist_roster": ["nonexistent_specialist", "repo_scout"]},
        )
        self.assertNotIn("nonexistent_specialist", roster.specialists)
        self.assertIn("repo_scout", roster.specialists)


class TestSpecialistRosterQuorum(unittest.TestCase):
    """Lógica de quorum según criticidad y número de especialistas."""

    def test_high_criticality_requires_all(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["browser_test", "test_execute"],
            criticality=Criticality.HIGH,
        )
        if len(roster.specialists) > 0:
            self.assertEqual(roster.quorum_mode, "all")
            self.assertEqual(roster.quorum_required, len(roster.specialists))

    def test_medium_criticality_single_specialist_any(self):
        roster = select_specialists_for_task(
            role=Role.SCOUT,
            required_capabilities=["repo_read"],
            criticality=Criticality.MEDIUM,
        )
        if len(roster.specialists) == 1:
            self.assertEqual(roster.quorum_mode, "any")
            self.assertEqual(roster.quorum_required, 1)

    def test_three_specialists_medium_criticality_majority_quorum(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["browser_test", "test_execute"],
            complexity=Complexity.HIGH,
            lsp_targets=["symbols"],
            criticality=Criticality.MEDIUM,
        )
        if len(roster.specialists) >= 3:
            self.assertEqual(roster.quorum_mode, "majority")
            self.assertEqual(roster.quorum_required, 2)

    def test_empty_roster_quorum_zero(self):
        roster = select_specialists_for_task(
            role=Role.TEAM_LEAD,
            required_capabilities=["reasoning"],
        )
        if roster.is_empty():
            self.assertEqual(roster.quorum_required, 0)
            self.assertEqual(roster.quorum_mode, "any")


class TestSpecialistRosterEconomics(unittest.TestCase):
    """Economics: todos los especialistas deben tener tier presupuestario."""

    def test_economics_all_specialists_have_tier(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["browser_test", "test_execute"],
            complexity=Complexity.HIGH,
        )
        for specialist in roster.specialists:
            self.assertIn(specialist, roster.economics)
            self.assertEqual(roster.economics[specialist], "budget_api")

    def test_empty_roster_empty_economics(self):
        roster = select_specialists_for_task(
            role=Role.TEAM_LEAD,
            required_capabilities=["reasoning"],
        )
        if roster.is_empty():
            self.assertEqual(roster.economics, {})


class TestSpecialistRosterMetadata(unittest.TestCase):
    """Serialización del roster a metadata de tarea."""

    def test_to_metadata_contains_required_keys(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["browser_test"],
        )
        meta = roster.to_metadata()
        self.assertIn("specialist_roster", meta)
        self.assertIn("specialist_roster_quorum_required", meta)
        self.assertIn("specialist_roster_quorum_mode", meta)
        self.assertIn("specialist_roster_reasoning", meta)
        self.assertIn("specialist_roster_economics", meta)

    def test_to_metadata_specialists_match(self):
        roster = select_specialists_for_task(
            role=Role.QA,
            required_capabilities=["test_execute"],
        )
        meta = roster.to_metadata()
        self.assertEqual(meta["specialist_roster"], roster.specialists)

    def test_is_empty_true_for_no_specialists(self):
        roster = select_specialists_for_task(
            role=Role.TEAM_LEAD,
            required_capabilities=["reasoning"],
        )
        self.assertIsInstance(roster.is_empty(), bool)

    def test_reasoning_not_empty(self):
        roster = select_specialists_for_task(
            role=Role.ENGINEER,
            required_capabilities=["coding"],
            complexity=Complexity.HIGH,
        )
        self.assertIsInstance(roster.reasoning, str)
        self.assertTrue(len(roster.reasoning) > 0)
