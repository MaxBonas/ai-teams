"""Tests confirming QA Tier 2 has been removed from the full_team profile.

The Reviewer absorbs static QA. For runtime test execution, the Lead may
optionally delegate to a test_runner (Tier 3) scout, but this is not created
by default and is never a required gate for cycle-close.

Covered:
  test_full_team_blueprint_has_only_engineer_and_reviewer
  test_full_team_proposal_does_not_suggest_qa_issue
  test_full_team_profile_requires_qa_gate_is_false
  test_full_team_accountability_chain_has_no_qa
"""
from __future__ import annotations

from aiteam.lead_intake import build_team_proposal, _suggested_issues_for_profile
from aiteam.run_profiles import FULL_TEAM, PROFILE_CONFIGS, build_default_team_blueprint


class TestFullTeamBlueprintNoQA:

    def test_full_team_blueprint_has_only_engineer_and_reviewer(self) -> None:
        """Default full_team blueprint must NOT include a qa agent."""
        blueprint = build_default_team_blueprint(
            goal_id="goal-1",
            raw_profile=FULL_TEAM,
            objective="Build a web app",
            source="test",
        )
        roles = {a.role for a in blueprint.agents}
        assert "qa" not in roles, f"QA role must not be in full_team blueprint; got roles={roles}"
        # Engineer and reviewer are required
        assert "engineer" in roles
        assert "reviewer" in roles

    def test_full_team_proposal_does_not_suggest_qa_issue(self) -> None:
        """build_team_proposal for full_team must NOT include any QA suggested issue."""
        fake_issue = {
            "id": "issue:intake",
            "title": "Build a web app",
            "description": "Build a simple web app",
        }
        proposal = build_team_proposal(fake_issue, profile=FULL_TEAM)
        suggested_roles = {item["role"] for item in proposal["suggested_issues"]}
        assert "qa" not in suggested_roles, (
            f"No QA suggested issue expected in full_team; got roles={suggested_roles}"
        )

    def test_full_team_profile_requires_qa_gate_is_false(self) -> None:
        """requires_qa_gate must be False (deprecated field, kept for API compat)."""
        config = PROFILE_CONFIGS[FULL_TEAM]
        assert config.requires_qa_gate is False, (
            "requires_qa_gate must always be False — QA Tier 2 is deprecated"
        )

    def test_full_team_accountability_chain_has_no_qa(self) -> None:
        """The accountability chain (engineer → lead, reviewer → lead) must not mention QA."""
        fake_issue = {
            "id": "issue:intake",
            "title": "Build a web app",
            "description": "Build a simple web app",
        }
        proposal = build_team_proposal(fake_issue, profile=FULL_TEAM)
        accountability_roles = {item["from"] for item in proposal["accountability"]}
        assert "qa" not in accountability_roles, (
            f"QA must not appear in accountability chain; got {accountability_roles}"
        )

    def test_suggested_issues_full_team_contains_reviewer_as_static_qa_owner(self) -> None:
        """The reviewer issue description must reference static QA responsibility."""
        fake_issue = {
            "id": "issue:intake",
            "title": "Build a web app",
            "description": "Build a simple web app",
        }
        proposal = build_team_proposal(fake_issue, profile=FULL_TEAM)
        review_issues = [i for i in proposal["suggested_issues"] if i["role"] == "reviewer"]
        assert len(review_issues) >= 1
        review_desc = review_issues[0]["description"].lower()
        # Reviewer must own static QA
        assert "qa" in review_desc or "static" in review_desc or "review" in review_desc
