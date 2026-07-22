from aiteam.model_tiers import audit_model_tier_matrix
from aiteam.user_config import DEFAULT_ADAPTER_PROFILES, MODEL_OPTIONS_BY_PROFILE, model_options


def test_every_builtin_model_has_capability_economy_and_speed_tier_evidence() -> None:
    report = audit_model_tier_matrix(DEFAULT_ADAPTER_PROFILES, MODEL_OPTIONS_BY_PROFILE)

    assert report["ok"] is True
    assert report["models_audited"] == sum(
        len(options) for options in MODEL_OPTIONS_BY_PROFILE.values()
    )


def test_public_model_options_expose_three_axis_tier_metadata() -> None:
    options = model_options()

    for profile_options in options.values():
        for option in profile_options:
            assert option["capability_band"] == option["tier"]
            assert option["economy"]["quota_unlimited"] is False
            assert option["speed_class"]
            assert option["speed_source"]
