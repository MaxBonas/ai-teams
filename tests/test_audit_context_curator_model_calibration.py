from scripts.audit_context_curator_model_calibration import build_report


def test_frozen_context_curator_matrix_promotes_luna_medium_as_tier3() -> None:
    report = build_report()

    assert report["matrix_balanced"] is True
    assert report["arms"]["gpt_5_5_control_low"]["accepted"] == 6
    assert report["arms"]["luna_medium_prompt_v3"]["accepted"] == 6
    assert report["arms"]["luna_low_original"]["accepted"] == 3
    assert report["arms"]["luna_low_prompt_v2"]["accepted"] == 4
    assert report["arms"]["terra_low_diagnostic"]["accepted"] == 5
    assert report["conclusion"]["promotion_allowed"] is True
    assert report["conclusion"]["selected_model"] == "gpt-5.6-luna"
    assert report["conclusion"]["reasoning_effort"] == "medium"
