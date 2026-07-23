from aiteam.skills import load_skill


def test_lead_does_not_invent_programming_work_for_research_objectives() -> None:
    skill = load_skill("lead")

    assert skill is not None
    assert "Non-code and research objectives" in skill
    assert "do not create Engineer" in skill
    assert "do not invent source files or" in skill
    assert "Acceptance evidence is then source coverage" in skill
