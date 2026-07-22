from __future__ import annotations

from pathlib import Path

from aiteam.skills import list_skills, load_skill

SKILLS_DIR = Path(__file__).parent.parent / "skills"


def test_load_skill_lead():
    skill = load_skill("lead")
    assert skill is not None
    assert "Team Lead" in skill
    assert "Heartbeat contract" in skill
    assert "delegation_type" in skill
    assert "Never retry" in skill


def test_load_skill_alias_team_lead():
    assert load_skill("team_lead") == load_skill("lead")


def test_load_skill_engineer():
    skill = load_skill("engineer")
    assert skill is not None
    assert "Software Engineer" in skill
    assert "evidence_required" in skill
    assert "reviewed_by" in skill


def test_load_skill_reviewer():
    skill = load_skill("reviewer")
    assert skill is not None
    assert "Code Reviewer" in skill or "reviewer" in skill.lower()
    assert "Next-run risk" in skill
    assert "Gate note" in skill


def test_load_skill_qa_conditional():
    skill = load_skill("qa")
    assert skill is not None
    assert "adversarial" in skill.lower()
    assert "AGENT-REPORT" in skill
    assert "inside the body of your final `add_comment`" in skill
    assert "notify_supervisor" in skill


def test_load_skill_test_designer_and_mcp_operator():
    test_designer = load_skill("test_designer") or ""
    assert "acceptance" in test_designer.lower()
    assert "inside the body of the final `add_comment`" in test_designer
    assert "notify_supervisor" in test_designer
    mcp_operator = load_skill("mcp_operator") or ""
    assert "allowlist" in mcp_operator.lower()
    assert "inside the body of the final" in mcp_operator
    assert "`add_comment` op" in mcp_operator
    assert "notify_supervisor" in mcp_operator
    assert "result: done | blocked" in mcp_operator
    assert "issue_status: done | blocked" in mcp_operator
    assert "health/recovery" in mcp_operator


def test_load_skill_test_runner():
    """test_runner.md must be discoverable as a Tier 3 scout skill."""
    skill = load_skill("test_runner")
    assert skill is not None
    assert "test_runner" in skill.lower() or "Test Runner" in skill
    assert "Tier 3" in skill


def test_load_skill_quorum_senior():
    skill = load_skill("quorum_senior")
    assert skill is not None
    assert "quorum" in skill.lower() or "senior" in skill.lower()
    assert "Cost note" in skill


def test_load_skill_file_scout():
    skill = load_skill("file_scout")
    assert skill is not None, "file_scout skill must be loadable"
    assert "File Scout" in skill
    assert "workspace_files" in skill
    assert "AGENT-REPORT" in skill


def test_load_skill_web_scout():
    skill = load_skill("web_scout")
    assert skill is not None, "web_scout skill must be loadable"
    assert "Web Scout" in skill
    assert "AGENT-REPORT" in skill


def test_load_skill_context_curator():
    skill = load_skill("context_curator")
    assert skill is not None, "context_curator skill must be loadable"
    assert "Context Curator" in skill
    assert "AGENT-REPORT" in skill


def test_load_skill_unknown_role():
    assert load_skill("wizard") is None


def test_load_skill_case_insensitive():
    assert load_skill("Lead") == load_skill("lead")
    assert load_skill("ENGINEER") == load_skill("engineer")


def test_load_skill_custom_dir(tmp_path):
    (tmp_path / "lead.md").write_text("# Custom Lead")
    skill = load_skill("lead", skills_dir=tmp_path)
    assert skill == "# Custom Lead"


def test_load_skill_missing_file_in_custom_dir(tmp_path):
    assert load_skill("lead", skills_dir=tmp_path) is None


def test_list_skills_returns_all_roles():
    roles = list_skills()
    assert "lead" in roles
    assert "engineer" in roles
    assert "reviewer" in roles
    assert "qa" in roles
    assert "test_designer" in roles
    assert "mcp_operator" in roles
    assert "quorum_senior" in roles
    # Tier 3 specialists must be discoverable
    assert "file_scout" in roles
    assert "web_scout" in roles
    assert "context_curator" in roles
    assert "test_runner" in roles


def test_list_skills_sorted():
    roles = list_skills()
    assert roles == sorted(roles)


def test_list_skills_empty_dir(tmp_path):
    assert list_skills(skills_dir=tmp_path) == []
