"""Action routing — Lead-as-Evaluator scoring model.

Pure functions: deterministic, no DB access, fully testable in isolation.

Usage::

    from aiteam.action_routing import Routing, route_action, pick_role_for_routing

    routing = route_action(criticality="critical", complexity="high", action_type="code")
    # → Routing.LEAD_SELF

    role = pick_role_for_routing(routing, action_type="code")
    # → "lead_executor"

Scoring model:

    score = _CRIT_SCORE[criticality] + _COMP_SCORE[complexity]

    TIER_3    : 0 – 3
    TIER_2    : 4 – 7
    LEAD_SELF : 8+

Routing table (criticality × complexity):

                          criticality
                  low      medium    high     critical
    complexity
       low    →  Tier 3   Tier 3   Tier 2   Lead-self
       medium →  Tier 3   Tier 2   Tier 2   Lead-self
       high   →  Tier 2   Tier 2   Lead-self Lead-self

Special rule: action_type == "test_exec" → always Tier 3, regardless of scores.
"""
from __future__ import annotations

from enum import Enum


class Routing(str, Enum):
    TIER_3 = "tier_3"
    TIER_2 = "tier_2"
    LEAD_SELF = "lead_self"


# Criticality score contribution.
# Unknown values default to medium (2) — fail-safe: treat as medium-cost mistake.
_CRIT_SCORE: dict[str, int] = {
    "low": 0,
    "medium": 2,
    "high": 4,
    "critical": 6,
}

# Complexity score contribution.
# Unknown values default to medium (2).
_COMP_SCORE: dict[str, int] = {
    "low": 0,
    "medium": 2,
    "high": 4,
}

# Tier 3 action types — these are always mechanical scouts regardless of criticality/complexity.
_TIER3_ACTION_TYPES: frozenset[str] = frozenset({"test_exec", "scout_files", "scout_web"})

# Tier 2 action types — these can be handled by senior sub-agents.
_TIER2_ROLES: dict[str, str] = {
    "code": "engineer",
    "review": "reviewer",
    # synthesis at Tier 2 routes to lead_executor (senior context needed)
    "synthesis": "lead_executor",
    "research": "engineer",  # research at Tier 2: engineer with long-read capability
}

# Tier 3 role mapping by action type.
_TIER3_ROLES: dict[str, str] = {
    "scout_files": "file_scout",
    "scout_web": "web_scout",
    "synthesis": "context_curator",
    "test_exec": "test_runner",
    "research": "web_scout",  # low-stakes research: web scout
}


def route_action(
    *,
    criticality: str,
    complexity: str,
    action_type: str,
) -> Routing:
    """Return the tier routing for an action based on (criticality, complexity, action_type).

    Deterministic pure function — no side effects.

    Parameters
    ----------
    criticality:
        One of ``low``, ``medium``, ``high``, ``critical``.
        Unknown values are treated as ``medium`` (fail-safe).
    complexity:
        One of ``low``, ``medium``, ``high``.
        Unknown values are treated as ``medium`` (fail-safe).
    action_type:
        One of ``code``, ``review``, ``scout_files``, ``scout_web``, ``research``,
        ``synthesis``, ``test_exec``.
        ``test_exec`` is always routed to Tier 3 regardless of scores.
    """
    # Special case: mechanical execution is always Tier 3
    if action_type in _TIER3_ACTION_TYPES:
        return Routing.TIER_3

    crit_norm = criticality.strip().lower()
    comp_norm = complexity.strip().lower()

    # "critical" criticality routes to LEAD_SELF regardless of complexity.
    # Rationale: the cost of a mistake is too high for any Tier 2 sub-agent.
    # This matches the routing matrix where all "critical" cells → Lead-self.
    if crit_norm == "critical":
        return Routing.LEAD_SELF

    score = _CRIT_SCORE.get(crit_norm, 2) + _COMP_SCORE.get(comp_norm, 2)

    if score >= 8:
        return Routing.LEAD_SELF
    if score >= 4:
        return Routing.TIER_2
    return Routing.TIER_3


def pick_role_for_routing(routing: Routing, action_type: str) -> str:
    """Map (routing, action_type) to a concrete role name.

    Parameters
    ----------
    routing:
        The tier decision from ``route_action``.
    action_type:
        The type of action being delegated.

    Returns
    -------
    str
        A role name suitable for passing to ``create_issue``.
        Defaults to ``"engineer"`` (Tier 2) or ``"file_scout"`` (Tier 3) when
        the action_type has no explicit mapping.
    """
    if routing == Routing.LEAD_SELF:
        return "lead_executor"
    if routing == Routing.TIER_2:
        return _TIER2_ROLES.get(action_type, "engineer")
    # Tier 3
    return _TIER3_ROLES.get(action_type, "file_scout")
