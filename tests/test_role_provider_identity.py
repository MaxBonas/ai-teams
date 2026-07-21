from __future__ import annotations

from aiteam.policies import canonical_role, forbidden_ops_for_role, role_status, role_tier
from aiteam.provider_identity import (
    capacity_pool_key,
    model_vendor,
    perspective_key,
    profile_identity,
)


def test_role_taxonomy_normalizes_aliases_without_losing_status() -> None:
    assert canonical_role("software_engineer") == "engineer"
    assert canonical_role("QA_ENGINEER") == "qa"
    assert role_tier("architect") == 1
    assert role_tier("worker") == 3
    assert role_status("qa_engineer") == "conditional"
    assert role_status("researcher") == "legacy"
    assert role_status("invented") == "unknown"


def test_worker_uses_tier3_authority_contract() -> None:
    denied = forbidden_ops_for_role("worker")
    assert "create_issue" in denied
    assert "write_file" in denied


def test_perspective_is_model_vendor_not_transport_profile() -> None:
    assert perspective_key("openai-codex", "gpt-5.6-sol") == "openai"
    assert perspective_key("openai", "gpt-5.6-terra") == "openai"
    assert perspective_key("google-antigravity", "claude-sonnet-5") == "anthropic"
    assert model_vendor("nemotron-3-ultra-free", fallback_provider="opencode-zen") == "nvidia"


def test_profile_identity_separates_capacity_from_perspective() -> None:
    profile = {
        "id": "codex_subscription",
        "provider": "openai-codex",
        "channel": "subscription",
        "config": {"model": "gpt-5.6-sol"},
    }
    identity = profile_identity(profile)
    assert identity == {
        "provider_org": "openai",
        "model_vendor": "openai",
        "perspective_key": "openai",
        "transport": "subscription",
        "capacity_pool": "codex_subscription",
    }
    assert capacity_pool_key(
        profile_id="openai_api_work", provider="openai", config={}
    ) == "openai_api_work"
