"""Tests for F3.2 — action routing integration in lead_intake.py.

Covered:
  test_suggested_issues_include_action_type_for_full_team
  test_suggested_issues_include_criticality_for_full_team
  test_build_issue_action_type_is_code
  test_review_issue_action_type_is_review
  test_apply_proposal_routing_overrides_role_when_criticality_critical
  test_apply_proposal_routing_preserves_role_for_lead_tier
  test_apply_proposal_no_override_when_no_action_type
  test_apply_proposal_stores_criticality_from_item
  test_wakeup_payload_includes_action_type
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aiteam.db.migration import SCHEMA_PATH
from aiteam.lead_intake import (
    _suggested_issues_for_profile,
    apply_accepted_team_proposal,
)
from aiteam.project_adapters import write_project_adapter_policy
from aiteam.user_config import record_model_health


# ── Helpers ───────────────────────────────────────────────────────────────────

def _init_db(db_path: Path, *, issue_id: str = "issue:root") -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("g1", "Goal"))
        for agent_id, role, name, seniority, adapter in [
            ("role:lead",          "lead",     "Lead",     "lead",   "lead_builtin"),
            ("role:engineer",      "engineer", "Engineer", "standard", "manual"),
            ("role:reviewer",      "reviewer", "Reviewer", "standard", "manual"),
            ("role:lead_executor", "lead_executor", "Lead Executor", "senior", "lead_builtin"),
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO agents (id, role, name, seniority, adapter_type)"
                " VALUES (?, ?, ?, ?, ?)",
                (agent_id, role, name, seniority, adapter),
            )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (issue_id, "g1", "Test project", "in_progress", "lead", "role:lead"),
        )
        conn.commit()


def _issues_for_full_team() -> list[dict]:
    return _suggested_issues_for_profile("full_team", "issue:root", [
        {"id": "role:engineer", "role": "engineer"},
        {"id": "role:reviewer", "role": "reviewer"},
    ])


# ── _suggested_issues_for_profile ─────────────────────────────────────────────

class TestSuggestedIssuesFields:

    def test_suggested_issues_include_action_type_for_full_team(self) -> None:
        issues = _issues_for_full_team()
        for issue in issues:
            if issue["role"] != "lead":
                assert "action_type" in issue, (
                    f"Issue {issue['id']} (role={issue['role']}) must have action_type"
                )

    def test_suggested_issues_include_criticality_for_full_team(self) -> None:
        issues = _issues_for_full_team()
        for issue in issues:
            assert "criticality" in issue, (
                f"Issue {issue['id']} must have criticality field"
            )

    def test_build_issue_action_type_is_code(self) -> None:
        issues = _issues_for_full_team()
        build_issues = [i for i in issues if i["role"] == "engineer"]
        assert build_issues, "No engineer issue found"
        assert build_issues[0]["action_type"] == "code"

    def test_review_issue_action_type_is_review(self) -> None:
        issues = _issues_for_full_team()
        review_issues = [i for i in issues if i["role"] == "reviewer"]
        assert review_issues, "No reviewer issue found"
        assert review_issues[0]["action_type"] == "review"

    def test_plan_issue_action_type_is_synthesis(self) -> None:
        issues = _issues_for_full_team()
        plan_issues = [i for i in issues if i["role"] == "lead"]
        assert plan_issues, "No lead/plan issue found"
        assert plan_issues[0]["action_type"] == "synthesis"


# ── apply_accepted_team_proposal routing override ─────────────────────────────

class TestApplyProposalRoutingOverride:

    def _make_proposal(self, *, criticality: str, action_type: str, role: str = "engineer") -> dict:
        return {
            "profile": "full_team",
            "direct_work": False,
            "proposed_team": [],
            "suggested_issues": [
                {
                    "id": "issue:root:task",
                    "title": "Critical task",
                    "description": "Do critical work",
                    "role": role,
                    "complexity": "high",
                    "criticality": criticality,
                    "action_type": action_type,
                    "priority": 80,
                    "assignee_agent_id": f"role:{role}",
                }
            ],
        }

    def test_routing_overrides_engineer_to_lead_executor_for_critical_high(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        proposal = self._make_proposal(criticality="critical", action_type="code")
        apply_accepted_team_proposal(
            db_path, parent_issue_id="issue:root", proposal=proposal, source_run_id="r1"
        )
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT role, assignee_agent_id, criticality FROM issues WHERE id = 'issue:root:task'"
            ).fetchone()
        assert row is not None
        assert row["role"] == "lead_executor", (
            f"Expected lead_executor for critical+high+code, got {row['role']}"
        )
        assert row["assignee_agent_id"] == "role:lead_executor"

    def test_accepted_team_materializes_canonical_owner_selection_intent(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        write_project_adapter_policy(tmp_path, profile_ids=["openai_api"])
        record_model_health(
            "openai_api", "gpt-5.6-terra", available=True, reason="test_fixture"
        )
        proposal = {
            "profile": "full_team",
            "direct_work": False,
            "proposed_team": [{
                "id": "role:reviewer",
                "role": "reviewer",
                "name": "Reviewer",
                "seniority": "standard",
                "adapter_type": "openai_api",
                "adapter_config": {
                    "profile_id": "openai_api",
                    "model": "gpt-5.6-terra",
                },
                "capabilities": ["repo_read"],
                "supervisor_agent_id": "role:lead",
            }],
            "suggested_issues": [],
        }

        apply_accepted_team_proposal(
            db_path,
            parent_issue_id="issue:root",
            proposal=proposal,
            source_run_id="r1",
        )

        with sqlite3.connect(str(db_path)) as conn:
            raw = conn.execute(
                "SELECT adapter_config_json FROM agents WHERE id='role:reviewer'"
            ).fetchone()[0]
        config = json.loads(raw)
        assert config["profile_id"] == "openai_api"
        assert config["model"] == "gpt-5.6-terra"
        assert config["selection_intent"]["schema_version"] == "model_selection_intent_v1"
        assert config["selection_intent"]["mode"] == "owner_explicit"
        assert config["selection_intent"]["source"] == "accepted_team_proposal"
        assert config["selection_intent"]["candidate_id"]

    def test_accepted_team_rejects_forged_owner_candidate_before_insert(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        write_project_adapter_policy(tmp_path, profile_ids=["openai_api"])
        record_model_health(
            "openai_api", "gpt-5.6-terra", available=True, reason="test_fixture"
        )
        proposal = {
            "profile": "full_team",
            "direct_work": False,
            "proposed_team": [{
                "id": "role:reviewer",
                "role": "reviewer",
                "name": "Reviewer",
                "seniority": "standard",
                "adapter_type": "openai_api",
                "adapter_config": {
                    "profile_id": "openai_api",
                    "model": "gpt-5.6-terra",
                    "selection_intent": {
                        "schema_version": "model_selection_intent_v1",
                        "mode": "owner_explicit",
                        "source": "tampered_proposal",
                        "candidate_id": "candidate:forged",
                    },
                },
                "capabilities": ["repo_read"],
                "supervisor_agent_id": "role:lead",
            }],
            "suggested_issues": [],
        }

        with pytest.raises(ValueError, match="candidate_id does not match"):
            apply_accepted_team_proposal(
                db_path,
                parent_issue_id="issue:root",
                proposal=proposal,
                source_run_id="r1",
            )

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT adapter_type, adapter_config_json FROM agents "
                "WHERE id='role:reviewer'"
            ).fetchone()
        assert row == ("manual", "{}")

    def test_routing_preserves_role_for_lead_tier(self, tmp_path: Path) -> None:
        """Lead-tier roles (lead, team_lead, lead_executor) are never overridden."""
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        proposal = self._make_proposal(criticality="critical", action_type="synthesis", role="lead")
        proposal["suggested_issues"][0]["assignee_agent_id"] = "role:lead"
        apply_accepted_team_proposal(
            db_path, parent_issue_id="issue:root", proposal=proposal, source_run_id="r1"
        )
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT role FROM issues WHERE id = 'issue:root:task'"
            ).fetchone()
        assert row is not None
        assert row["role"] == "lead", "Lead-tier role must not be overridden by routing"

    def test_no_routing_override_when_no_action_type(self, tmp_path: Path) -> None:
        """Without action_type, no routing is applied and the proposed role is kept."""
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        proposal: dict = {
            "profile": "full_team",
            "direct_work": False,
            "proposed_team": [],
            "suggested_issues": [
                {
                    "id": "issue:root:task",
                    "title": "Simple task",
                    "role": "engineer",
                    "complexity": "medium",
                    "criticality": "critical",
                    # No action_type — routing must NOT fire
                    "priority": 50,
                    "assignee_agent_id": "role:engineer",
                }
            ],
        }
        apply_accepted_team_proposal(
            db_path, parent_issue_id="issue:root", proposal=proposal, source_run_id="r1"
        )
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT role FROM issues WHERE id = 'issue:root:task'"
            ).fetchone()
        assert row is not None
        assert row["role"] == "engineer", "Without action_type, original role must be kept"

    def test_criticality_stored_from_item_not_hardcoded_medium(
        self, tmp_path: Path
    ) -> None:
        """criticality from the spec must be stored in DB (not hardcoded 'medium')."""
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        proposal = self._make_proposal(criticality="high", action_type="review", role="reviewer")
        proposal["suggested_issues"][0]["assignee_agent_id"] = "role:reviewer"
        apply_accepted_team_proposal(
            db_path, parent_issue_id="issue:root", proposal=proposal, source_run_id="r1"
        )
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT criticality FROM issues WHERE id = 'issue:root:task'"
            ).fetchone()
        assert row is not None
        assert row["criticality"] == "high", (
            f"criticality must be 'high' from item spec, got {row['criticality']!r}"
        )

    def test_wakeup_payload_includes_action_type(self, tmp_path: Path) -> None:
        """The wakeup payload must include action_type so the agent knows its task type."""
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        proposal = self._make_proposal(criticality="medium", action_type="code")
        apply_accepted_team_proposal(
            db_path, parent_issue_id="issue:root", proposal=proposal, source_run_id="r1"
        )
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            wakeup = conn.execute(
                "SELECT payload_json FROM wakeup_requests WHERE payload_json LIKE '%issue:root:task%' LIMIT 1"
            ).fetchone()
        assert wakeup is not None
        payload = json.loads(wakeup["payload_json"] or "{}")
        assert payload.get("action_type") == "code", (
            "Wakeup payload must carry action_type so the agent can act accordingly"
        )

    def test_medium_complexity_code_stays_engineer(self, tmp_path: Path) -> None:
        """medium+medium+code scores 4 → TIER_2 → engineer (no override needed)."""
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        proposal = self._make_proposal(criticality="medium", action_type="code")
        proposal["suggested_issues"][0]["complexity"] = "medium"
        apply_accepted_team_proposal(
            db_path, parent_issue_id="issue:root", proposal=proposal, source_run_id="r1"
        )
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT role FROM issues WHERE id = 'issue:root:task'"
            ).fetchone()
        assert row is not None
        assert row["role"] == "engineer"
