"""Tests for action routing — Lead-as-Evaluator scoring model.

Pure function tests — no DB, no adapters.

Routing matrix (criticality × complexity):
                          criticality
                  low      medium    high     critical
    complexity
       low    →  Tier 3   Tier 3   Tier 2   Lead-self
       medium →  Tier 3   Tier 2   Tier 2   Lead-self
       high   →  Tier 2   Tier 2   Lead-self Lead-self

Scores: criticality low=0, medium=2, high=4, critical=6
        complexity  low=0, medium=2, high=4
TIER_3 if score 0-3; TIER_2 if 4-7; LEAD_SELF if 8+

Special: action_type='test_exec' → always TIER_3.
         action_type in scout family → always TIER_3.
"""
from __future__ import annotations

import pytest

from aiteam.action_routing import Routing, pick_role_for_routing, route_action


# ── Matrix coverage (12 cells) ────────────────────────────────────────────────

class TestRoutingMatrix:
    """One test per cell of the 4×3 (criticality × complexity) matrix."""

    # --- low criticality (score 0) ---
    def test_low_low_tier3(self) -> None:
        assert route_action(criticality="low", complexity="low", action_type="code") == Routing.TIER_3

    def test_low_medium_tier3(self) -> None:
        assert route_action(criticality="low", complexity="medium", action_type="code") == Routing.TIER_3

    def test_low_high_tier2(self) -> None:
        # score = 0 + 4 = 4 → TIER_2
        assert route_action(criticality="low", complexity="high", action_type="code") == Routing.TIER_2

    # --- medium criticality (score 2) ---
    def test_medium_low_tier3(self) -> None:
        # score = 2 + 0 = 2 → TIER_3
        assert route_action(criticality="medium", complexity="low", action_type="code") == Routing.TIER_3

    def test_medium_medium_tier2(self) -> None:
        # score = 2 + 2 = 4 → TIER_2
        assert route_action(criticality="medium", complexity="medium", action_type="code") == Routing.TIER_2

    def test_medium_high_tier2(self) -> None:
        # score = 2 + 4 = 6 → TIER_2
        assert route_action(criticality="medium", complexity="high", action_type="code") == Routing.TIER_2

    # --- high criticality (score 4) ---
    def test_high_low_tier2(self) -> None:
        # score = 4 + 0 = 4 → TIER_2
        assert route_action(criticality="high", complexity="low", action_type="code") == Routing.TIER_2

    def test_high_medium_tier2(self) -> None:
        # score = 4 + 2 = 6 → TIER_2
        assert route_action(criticality="high", complexity="medium", action_type="code") == Routing.TIER_2

    def test_high_high_lead_self(self) -> None:
        # score = 4 + 4 = 8 → LEAD_SELF
        assert route_action(criticality="high", complexity="high", action_type="code") == Routing.LEAD_SELF

    # --- critical criticality (score 6) ---
    def test_critical_low_lead_self(self) -> None:
        # criticality="critical" always routes to LEAD_SELF regardless of complexity.
        # This is a special rule: critical mistakes are too costly for any Tier 2 agent.
        # score=6+0=6 would only give TIER_2 by pure score, but the critical override fires.
        assert route_action(criticality="critical", complexity="low", action_type="code") == Routing.LEAD_SELF

    def test_critical_medium_lead_self(self) -> None:
        assert route_action(criticality="critical", complexity="medium", action_type="code") == Routing.LEAD_SELF

    def test_critical_high_lead_self(self) -> None:
        assert route_action(criticality="critical", complexity="high", action_type="code") == Routing.LEAD_SELF


# ── Special action_type rules ─────────────────────────────────────────────────

class TestActionTypeOverrides:

    def test_test_exec_always_returns_tier3(self) -> None:
        """test_exec is always Tier 3 regardless of criticality or complexity."""
        for crit in ("low", "medium", "high", "critical"):
            for comp in ("low", "medium", "high"):
                result = route_action(criticality=crit, complexity=comp, action_type="test_exec")
                assert result == Routing.TIER_3, (
                    f"test_exec must always be Tier 3 but got {result} for crit={crit} comp={comp}"
                )

    def test_scout_files_always_tier3(self) -> None:
        assert route_action(criticality="critical", complexity="high", action_type="scout_files") == Routing.TIER_3

    def test_scout_web_always_tier3(self) -> None:
        assert route_action(criticality="critical", complexity="high", action_type="scout_web") == Routing.TIER_3


# ── Unknown values default to medium ──────────────────────────────────────────

class TestUnknownValueFallback:

    def test_unknown_criticality_defaults_to_medium(self) -> None:
        """Unknown criticality → treated as medium (score 2)."""
        # medium + low = 2 + 0 = 2 → TIER_3
        result = route_action(criticality="UNKNOWN_LEVEL", complexity="low", action_type="code")
        assert result == Routing.TIER_3

    def test_unknown_complexity_defaults_to_medium(self) -> None:
        """Unknown complexity → treated as medium (score 2)."""
        # medium + medium = 2 + 2 = 4 → TIER_2
        result = route_action(criticality="medium", complexity="UNKNOWN_COMPLEXITY", action_type="code")
        assert result == Routing.TIER_2

    def test_empty_criticality_defaults_to_medium(self) -> None:
        result = route_action(criticality="", complexity="low", action_type="code")
        # empty → fallback 2. 2 + 0 = 2 → TIER_3
        assert result == Routing.TIER_3


# ── pick_role_for_routing ─────────────────────────────────────────────────────

class TestPickRole:

    def test_lead_self_returns_lead_executor_role(self) -> None:
        role = pick_role_for_routing(Routing.LEAD_SELF, "code")
        assert role == "lead_executor"

    def test_lead_self_always_lead_executor_regardless_of_action(self) -> None:
        for action in ("code", "review", "synthesis", "research", "test_exec"):
            assert pick_role_for_routing(Routing.LEAD_SELF, action) == "lead_executor"

    def test_tier2_code_returns_engineer(self) -> None:
        assert pick_role_for_routing(Routing.TIER_2, "code") == "engineer"

    def test_tier2_review_returns_reviewer(self) -> None:
        assert pick_role_for_routing(Routing.TIER_2, "review") == "reviewer"

    def test_pick_role_synthesis_critical_routes_to_lead_executor(self) -> None:
        """Synthesis at LEAD_SELF → lead_executor (synthesis needs senior context)."""
        routing = route_action(criticality="critical", complexity="high", action_type="synthesis")
        role = pick_role_for_routing(routing, "synthesis")
        assert role == "lead_executor"

    def test_tier3_test_exec_returns_test_runner(self) -> None:
        routing = route_action(criticality="low", complexity="low", action_type="test_exec")
        role = pick_role_for_routing(routing, "test_exec")
        assert role == "test_runner"

    def test_tier3_scout_files_returns_file_scout(self) -> None:
        routing = route_action(criticality="low", complexity="low", action_type="scout_files")
        role = pick_role_for_routing(routing, "scout_files")
        assert role == "file_scout"

    def test_tier3_scout_web_returns_web_scout(self) -> None:
        routing = route_action(criticality="low", complexity="low", action_type="scout_web")
        role = pick_role_for_routing(routing, "scout_web")
        assert role == "web_scout"

    def test_tier3_synthesis_returns_context_curator(self) -> None:
        routing = route_action(criticality="low", complexity="low", action_type="synthesis")
        role = pick_role_for_routing(routing, "synthesis")
        assert role == "context_curator"

    def test_tier2_unknown_action_defaults_to_engineer(self) -> None:
        role = pick_role_for_routing(Routing.TIER_2, "unknown_action_xyz")
        assert role == "engineer"

    def test_tier3_unknown_action_defaults_to_file_scout(self) -> None:
        role = pick_role_for_routing(Routing.TIER_3, "unknown_action_xyz")
        assert role == "file_scout"


# ── Routing enum values ───────────────────────────────────────────────────────

class TestRoutingEnum:

    def test_routing_values_are_strings(self) -> None:
        assert Routing.TIER_3 == "tier_3"
        assert Routing.TIER_2 == "tier_2"
        assert Routing.LEAD_SELF == "lead_self"
