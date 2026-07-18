from aiteam.context_budget import evaluate_context_budget


def test_legacy_profiles_keep_safe_character_threshold() -> None:
    below = evaluate_context_budget(
        unsynthesized_chars=7_999, base_payload_chars=20_000, adapter_config={}
    )
    at = evaluate_context_budget(
        unsynthesized_chars=8_000, base_payload_chars=0, adapter_config={}
    )
    assert below.policy == "legacy_char_threshold"
    assert below.should_compact is False
    assert at.should_compact is True


def test_declared_model_waits_until_comfort_budget_not_hard_limit() -> None:
    config = {
        "context_window_tokens": 32_000,
        "comfortable_context_ratio": 0.75,
        "reserved_output_tokens": 4_000,
        "reserved_tool_tokens": 2_000,
        "chars_per_token": 4,
    }
    comfortable = 18_000
    below = evaluate_context_budget(
        unsynthesized_chars=40_000, base_payload_chars=20_000, adapter_config=config
    )
    above = evaluate_context_budget(
        unsynthesized_chars=52_000, base_payload_chars=20_000, adapter_config=config
    )
    assert below.comfortable_input_tokens == comfortable
    assert below.estimated_input_tokens == 15_000
    assert below.should_compact is False
    assert above.estimated_input_tokens == 18_000
    assert above.should_compact is True


def test_large_fixed_payload_does_not_spawn_for_tiny_reclaimable_thread() -> None:
    decision = evaluate_context_budget(
        unsynthesized_chars=2_000,
        base_payload_chars=100_000,
        adapter_config={"context_window_tokens": 32_000},
    )
    assert decision.estimated_input_tokens > decision.comfortable_input_tokens
    assert decision.should_compact is False
