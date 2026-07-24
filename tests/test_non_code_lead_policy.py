import json

from aiteam.lead_intake import build_team_proposal
from aiteam.skills import load_skill


def test_lead_does_not_invent_programming_work_for_research_objectives() -> None:
    skill = load_skill("lead")

    assert skill is not None
    assert "Non-code and research objectives" in skill
    assert "do not create Engineer" in skill
    assert "do not invent source files or" in skill
    assert "Acceptance evidence is then source coverage" in skill


def test_non_code_proposal_has_no_programming_roles_or_test_evidence() -> None:
    issue = {
        "id": "issue:intake",
        "title": "Estudio para una empresa de limpieza",
        "description": "Crear formularios para analizar necesidades y operaciones.",
        "metadata_json": json.dumps({"profile": "full_team"}),
    }

    proposal = build_team_proposal(issue, profile="full_team")

    assert proposal["objective_classification"]["kind"] == "research"
    assert {member["role"] for member in proposal["proposed_team"]} == {
        "web_scout",
        "context_curator",
    }
    issue_roles = {item["role"] for item in proposal["suggested_issues"]}
    assert issue_roles == {"web_scout", "context_curator", "lead"}
    serialized = json.dumps(proposal, ensure_ascii=False).casefold()
    assert '"role": "engineer"' not in serialized
    assert '"role": "test_designer"' not in serialized
    assert '"role": "test_runner"' not in serialized
    evidence = " ".join(
        item
        for issue_spec in proposal["suggested_issues"]
        for item in issue_spec["evidence_required"]
    ).casefold()
    assert "exit code" not in evidence
    assert "formularios listos para usar" in evidence


def test_mixed_proposal_defers_engineering_until_software_subissue_exists() -> None:
    issue = {
        "id": "issue:intake",
        "title": "Analizar necesidades y construir aplicación web",
        "description": "Preparar formularios e implementar frontend y API.",
        "metadata_json": json.dumps({"profile": "full_team"}),
    }

    proposal = build_team_proposal(issue, profile="full_team")

    assert proposal["objective_classification"]["kind"] == "mixed"
    assert {member["role"] for member in proposal["proposed_team"]} == {
        "web_scout",
        "context_curator",
    }
    assert not {
        item["role"]
        for item in proposal["suggested_issues"]
    }.intersection({"engineer", "test_designer", "test_runner", "qa"})
