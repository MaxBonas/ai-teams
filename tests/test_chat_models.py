from api.chat_models import TeamChatRequest


def test_team_chat_request_defaults_match_test_run_profile() -> None:
    request = TeamChatRequest(message="Plan a generic change")

    assert request.mode == "sprint5"
    assert request.complexity == "medium"
    assert request.criticality == "low"
    assert request.strict_mode is False
    assert request.auto_extend_weak_runs is False
    assert request.repair_first_mode is False
    assert request.run_profile == "team_advanced"
    assert request.allow_low_productivity_override is True


def test_team_chat_request_accepts_solo_lead_profile() -> None:
    request = TeamChatRequest(message="Implement a tiny fix", run_profile="solo_lead")

    assert request.run_profile == "solo_lead"
