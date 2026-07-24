from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers.user_adapters import _codex_auth_info, _discover_api_catalog
from aiteam.project_adapters import choose_adapter_for_role
from aiteam.user_config import (
    _cmd_command,
    _write_windows_login_launcher,
    cli_status,
    inject_adapter_secrets,
    load_adapter_profiles,
    executable_model_options,
    model_fallback_for_role,
    observed_profile_cli_version,
    _powershell_command,
    record_model_catalog,
    record_model_health,
    resolve_adapter_config,
    save_adapter_health,
    store_secret,
    validate_model_selection,
)


def test_observed_profile_cli_version_uses_local_provider_transport(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "aiteam.user_config._resolve_cli_executable",
        lambda command: f"C:/tools/{command}.exe",
    )
    monkeypatch.setattr(
        "aiteam.user_config.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="ollama version is 0.32.1\n", stderr=""
        ),
    )
    profile = {
        "adapter_type": "subscription_cli",
        "config": {
            "cli_kind": "codex",
            "command": ["codex"],
            "local_provider": "ollama",
        },
    }

    assert observed_profile_cli_version(profile) == "0.32.1"


def test_observed_profile_cli_version_does_not_mislabel_lmstudio_as_codex(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "aiteam.user_config._resolve_cli_executable",
        lambda _command: (_ for _ in ()).throw(AssertionError("unexpected CLI probe")),
    )
    profile = {
        "adapter_type": "subscription_cli",
        "config": {
            "cli_kind": "codex",
            "command": ["codex"],
            "local_provider": "lmstudio",
        },
    }

    assert observed_profile_cli_version(profile) is None


def test_user_adapter_profiles_include_subscriptions_and_local_models(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))

    profiles = {profile["id"]: profile for profile in load_adapter_profiles()}

    assert profiles["codex_subscription"]["adapter_type"] == "subscription_cli"
    assert profiles["codex_subscription"]["config"]["api_key_ref"] == "secret:openai:default"
    assert "gemini_subscription" not in profiles
    assert profiles["gemini_api"]["config"]["api_key_ref"] == "secret:google:default"
    assert profiles["gemini_api_free"]["config"]["free_tier"] is True
    assert profiles["gemini_api_free"]["config"]["api_key_ref"] == "secret:google-free:default"
    assert profiles["groq_api_free"]["adapter_type"] == "openai_compatible_api"
    assert profiles["groq_api_free"]["config"]["api_key_ref"] == "secret:groq:default"
    assert profiles["groq_api_free"]["config"]["api_quota_source"] == (
        "provider_response_headers"
    )
    assert "subscription_quota" not in profiles["groq_api_free"]["config"]
    assert profiles["antigravity_subscription"]["config"]["cli_kind"] == "antigravity"
    assert profiles["antigravity_subscription"]["config"]["model"] == "gemini-3.1-pro-high"
    assert profiles["opencode_zen_free"]["config"]["cli_kind"] == "opencode"
    assert profiles["opencode_zen_free"]["data_policy"] == "non_confidential_only"
    assert profiles["claude_subscription_blocked"]["status"] == "blocked_by_provider"
    assert profiles["local_qwen_ollama"]["config"]["model"] == "qwen2.5-coder:14b"
    assert profiles["local_gem4_lmstudio"]["config"]["local_provider"] == "lmstudio"
    assert profiles["openai_api"]["model_options"][0]["value"] == "gpt-5.6-sol"
    assert profiles["gemini_api"]["config"]["model"] == "gemini-3.6-flash"
    assert profiles["anthropic_api"]["config"]["model"] == "claude-sonnet-5"
    assert {item["value"] for item in profiles["antigravity_subscription"]["model_options"]} >= {
        "gemini-3.6-flash-high", "gemini-3.6-flash-medium", "gemini-3.6-flash-low",
        "gemini-3.1-pro-high", "gemini-3.1-pro-low", "gemini-3.5-flash-high",
        "claude-opus-4-6-thinking", "gpt-oss-120b-medium",
    }
    assert profiles["openai_api"]["health"]["status"] == "untested"


def test_opencode_free_models_follow_runtime_catalog(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    import aiteam.user_config as config_mod

    monkeypatch.setattr(config_mod, "_opencode_model_names", lambda _config: [
        "opencode/nemotron-3-ultra-free",
        "opencode/north-mini-code-free",
    ])
    options, catalog = executable_model_options("opencode_zen_free")
    by_value = {item["value"]: item for item in options}

    assert catalog == {"status": "current", "source": "opencode models opencode", "count": 2}
    assert by_value["opencode/nemotron-3-ultra-free"]["available"] is True
    assert by_value["opencode/deepseek-v4-flash-free"]["available"] is False
    assert by_value["opencode/north-mini-code-free"]["tier"] == "budget"


def test_opencode_read_only_profile_is_never_assigned_to_engineer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    import aiteam.user_config as config_mod
    monkeypatch.setattr(config_mod, "_opencode_model_names", lambda _config: [
        "opencode/nemotron-3-ultra-free",
        "opencode/deepseek-v4-flash-free",
    ])
    profile = next(p for p in load_adapter_profiles() if p["id"] == "opencode_zen_free")

    assert choose_adapter_for_role("engineer", "standard", [profile]) is None
    assert choose_adapter_for_role(
        "lead", "lead", [profile], data_class="public", run_profile="full_team"
    )["model"] == "opencode/nemotron-3-ultra-free"


def test_opencode_laguna_requires_exact_probe_before_selection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    import aiteam.user_config as config_mod

    model = "opencode/laguna-s-2.1-free"
    monkeypatch.setattr(config_mod, "_opencode_model_names", lambda _config: [model])

    options, _catalog = executable_model_options("opencode_zen_free")
    selected = next(item for item in options if item["value"] == model)
    assert selected["availability"] == "catalogued"
    assert selected["selectable"] is False

    record_model_health(
        "opencode_zen_free", model, available=True, reason="run_completed"
    )
    options, _catalog = executable_model_options("opencode_zen_free")
    selected = next(item for item in options if item["value"] == model)
    assert selected["availability"] == "verified"
    assert selected["selectable"] is True


def test_codex_models_are_visible_but_disabled_when_cli_cannot_read_catalog(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    import aiteam.user_config as config_mod

    monkeypatch.setattr(config_mod, "_codex_catalog_compatibility", lambda _config: {
        "status": "cli_update_required",
        "source": "codex models_cache.json",
        "installed_version": "0.128.0",
        "catalog_client_version": "0.145.0",
        "models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"],
    })

    options, catalog = executable_model_options("codex_subscription")

    assert catalog["status"] == "cli_update_required"
    assert options
    assert all(item["available"] is False for item in options)
    assert "actualizar Codex CLI" in options[0]["availability_reason"]


def test_completed_run_evidence_reenables_exact_model_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    import aiteam.user_config as config_mod

    monkeypatch.setattr(config_mod, "_codex_catalog_compatibility", lambda _config: {
        "status": "cli_update_required",
        "installed_version": "0.128.0",
        "catalog_client_version": "0.145.0",
        "models": [],
    })
    record_model_health("codex_subscription", "gpt-5.6-luna", available=True, reason="run_completed")

    options, _catalog = executable_model_options("codex_subscription")
    by_value = {item["value"]: item for item in options}

    assert by_value["gpt-5.6-luna"]["available"] is True
    assert by_value["gpt-5.6-luna"]["availability"] == "verified"
    assert by_value["gpt-5.6-sol"]["available"] is False

    save_adapter_health("codex_subscription", {"status": "ok", "reason": "auth_present"})
    persisted = json.loads(
        (tmp_path / "user-config" / "adapter_health.json").read_text(encoding="utf-8")
    )["profiles"]["codex_subscription"]
    assert persisted["verified_models"] == ["gpt-5.6-luna"]


def test_api_catalogue_is_visible_but_not_selectable_until_exact_probe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))

    options, _catalog = executable_model_options("openai_api")
    by_value = {item["value"]: item for item in options}
    assert by_value["gpt-5.6-sol"]["available"] is True
    assert by_value["gpt-5.6-sol"]["selectable"] is False
    assert by_value["gpt-5.6-sol"]["verification_status"] == "catalogued"

    record_model_catalog(
        "openai_api", ["gpt-5.6-sol", "gpt-5.6-terra"],
        source="OpenAI API", status="current",
    )
    discovered, catalog = executable_model_options("openai_api")
    discovered_by_value = {item["value"]: item for item in discovered}
    assert catalog["status"] == "current"
    assert discovered_by_value["gpt-5.6-sol"]["selectable"] is False
    assert discovered_by_value["gpt-5.6-luna"]["available"] is False

    record_model_health(
        "openai_api", "gpt-5.6-sol", available=True, reason="live_test_completed"
    )
    verified, _catalog = executable_model_options("openai_api")
    verified_by_value = {item["value"]: item for item in verified}
    assert verified_by_value["gpt-5.6-sol"]["selectable"] is True
    assert verified_by_value["gpt-5.6-terra"]["selectable"] is False


def test_rate_limited_model_remains_catalogued_but_not_selectable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    record_model_health(
        "openai_api", "gpt-5.6-terra", available=False,
        reason="http_429", status="rate_limited",
    )

    options, _catalog = executable_model_options("openai_api")
    terra = next(item for item in options if item["value"] == "gpt-5.6-terra")
    assert terra["available"] is True
    assert terra["selectable"] is False
    assert terra["verification_status"] == "rate_limited"


def test_fallback_excludes_retired_and_rate_limited_models(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    record_model_health("openai_api", "gpt-5.6-sol", available=True, reason="verified")
    record_model_health(
        "openai_api", "gpt-5.6-terra", available=False,
        reason="model retired", status="retired",
    )
    fallback = model_fallback_for_role("openai_api", "gpt-5.6-terra", "engineer")
    assert fallback is not None and fallback["value"] == "gpt-5.6-sol"

    record_model_health(
        "openai_api", "gpt-5.6-sol", available=False,
        reason="http_429", status="rate_limited",
    )
    assert model_fallback_for_role("openai_api", "gpt-5.6-terra", "engineer") is None


def test_authenticated_openai_discovery_reads_ids_without_leaking_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    store_secret(provider="openai", name="default", secret="sk-never-return")
    profile = next(item for item in load_adapter_profiles() if item["id"] == "openai_api")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return json.dumps({
                "data": [{"id": "gpt-5.6-sol"}, {"id": "provider/extra-model"}],
            }).encode("utf-8")

    import api.routers.user_adapters as router_mod
    monkeypatch.setattr(router_mod.urllib.request, "urlopen", lambda _request, timeout: _Response())

    result = _discover_api_catalog(profile)

    assert result["status"] == "current"
    assert result["models"] == ["gpt-5.6-sol", "provider/extra-model"]
    assert "sk-never-return" not in json.dumps(result)


def test_antigravity_team_options_follow_exact_cli_catalog(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    import aiteam.user_config as config_mod

    monkeypatch.setattr(
        config_mod,
        "_antigravity_model_names",
        lambda _config: ["gemini-3.1-pro-high", "gemini-3.5-flash-low"],
    )
    options, catalog = executable_model_options("antigravity_subscription")
    by_value = {item["value"]: item for item in options}

    assert catalog == {"status": "current", "source": "agy models", "count": 2}
    assert by_value["gemini-3.1-pro-high"]["available"] is True
    assert by_value["gemini-3.5-flash-high"]["available"] is False


def test_antigravity_new_catalogue_models_require_exact_probe(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    import aiteam.user_config as config_mod

    monkeypatch.setattr(
        config_mod,
        "_antigravity_model_names",
        lambda _config: ["gemini-3.6-flash-medium"],
    )
    options, _catalog = executable_model_options("antigravity_subscription")
    candidate = next(item for item in options if item["value"] == "gemini-3.6-flash-medium")

    assert candidate["available"] is True
    assert candidate["availability"] == "catalogued"
    assert candidate["selectable"] is False
    assert "probe estructurado" in candidate["availability_reason"]

    record_model_health(
        "antigravity_subscription",
        "gemini-3.6-flash-medium",
        available=True,
        reason="exact submit completed",
    )
    options, _catalog = executable_model_options("antigravity_subscription")
    verified = next(item for item in options if item["value"] == "gemini-3.6-flash-medium")
    assert verified["selectable"] is True
    assert verified["availability"] == "verified"


def test_antigravity_saved_display_alias_is_normalized_before_execution(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))

    cfg = resolve_adapter_config(
        "subscription_cli",
        {"profile_id": "antigravity_subscription", "model": "Gemini 3.5 Flash (High)"},
    )

    assert cfg["model"] == "gemini-3.5-flash-high"


def test_team_save_rejects_known_unavailable_model(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    import aiteam.user_config as config_mod

    monkeypatch.setattr(config_mod, "_codex_catalog_compatibility", lambda _config: {
        "status": "cli_update_required",
        "installed_version": "0.128.0",
        "catalog_client_version": "0.145.0",
        "models": [],
    })

    with pytest.raises(ValueError, match="not executable"):
        validate_model_selection({"profile_id": "codex_subscription", "model": "gpt-5.6-luna"})


def test_model_fallback_stays_in_profile_and_excludes_manual_options(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    import aiteam.user_config as config_mod

    monkeypatch.setattr(config_mod, "_codex_catalog_compatibility", lambda _config: {
        "status": "cli_update_required",
        "installed_version": "0.128.0",
        "catalog_client_version": "0.145.0",
        "models": [],
    })
    record_model_health("codex_subscription", "gpt-5.6-terra", available=True, reason="run_completed")

    fallback = model_fallback_for_role(
        "codex_subscription", "gpt-5.6-sol", "context_curator"
    )

    assert fallback is not None
    assert fallback["value"] == "gpt-5.6-terra"
    assert fallback["changes_family"] is False
    assert fallback["changes_tier"] is True


def test_user_secret_store_roundtrip_and_env_injection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))

    ref = store_secret(provider="openai", name="default", secret="sk-test")
    env = inject_adapter_secrets({}, "openai_api", {"api_key_ref": ref})

    assert ref == "secret:openai:default"
    assert env["OPENAI_API_KEY"] == "sk-test"


def test_resolve_adapter_config_merges_profile(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))

    cfg = resolve_adapter_config("subscription_cli", {"profile_id": "local_qwen_ollama", "model": "qwen3:32b"})

    assert cfg["cli_kind"] == "codex"
    assert cfg["oss"] is True
    assert cfg["local_provider"] == "ollama"
    assert cfg["model"] == "qwen3:32b"


def test_codex_profile_reads_context_capacity_from_local_catalog(tmp_path: Path, monkeypatch) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir()
    (codex_root / "models_cache.json").write_text(json.dumps({
        "models": [{
            "slug": "test-model",
            "context_window": 100_000,
            "effective_context_window_percent": 80,
        }]
    }), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_root))
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))

    cfg = resolve_adapter_config(
        "subscription_cli", {"profile_id": "codex_subscription", "model": "test-model"}
    )

    assert cfg["context_window_tokens"] == 80_000
    assert cfg["context_window_source"] == "codex_models_cache"


def test_user_adapters_api_rejects_inline_secret(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.setenv("AITEAM_API_KEY", "test-key")

    client = TestClient(app)
    headers = {"x-aiteam-api-key": "test-key"}

    ok = client.get("/api/user-adapters", headers=headers)
    assert ok.status_code == 200
    assert any(p["id"] == "codex_subscription" for p in ok.json()["profiles"])

    bad = client.post(
        "/api/user-adapters/profiles",
        headers=headers,
        json={
            "id": "bad",
            "label": "Bad",
            "adapter_type": "openai_api",
            "config": {"api_key": "sk-nope"},
        },
    )
    assert bad.status_code == 400

    saved = client.post(
        "/api/user-adapters/secrets",
        headers=headers,
        json={"provider": "google", "name": "default", "secret": "gemini-key"},
    )
    assert saved.status_code == 200
    assert saved.json()["ref"] == "secret:google:default"

    # Avoid leaking test auth into other TestClient lifespans.
    monkeypatch.delenv("AITEAM_API_KEY", raising=False)
    os.environ.pop("AITEAM_API_KEY", None)


def test_custom_profile_preserves_governance_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.setenv("AITEAM_API_KEY", "test-key")
    client = TestClient(app)
    headers = {"x-aiteam-api-key": "test-key"}

    response = client.post(
        "/api/user-adapters/profiles",
        headers=headers,
        json={
            "id": "custom_readonly",
            "label": "Custom readonly",
            "adapter_type": "openai_compatible_api",
            "provider": "custom-host",
            "supported_roles": ["code_reviewer", "web_scout"],
            "data_policy": "non_confidential_only",
            "privacy_note": "No enviar secretos.",
            "capabilities": ["reasoning", "synthesis"],
            "workspace_mode": "read",
            "mcp_transport": "none",
            "structured_output": "json_schema",
            "config": {"model": "custom/model"},
            "model_options": [{
                "value": "custom/model",
                "label": "Custom model",
                "tier": "standard",
                "caps": ["reasoning", "synthesis"],
                "allowed_roles": ["code_reviewer", "web_scout"],
            }],
        },
    )

    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["supported_roles"] == ["reviewer", "web_scout"]
    assert profile["workspace_mode"] == "read"
    assert profile["identity"]["capacity_pool"] == "custom_readonly"
    assert profile["model_options"][0]["allowed_roles"] == ["reviewer", "web_scout"]
    record_model_health(
        "custom_readonly", "custom/model", available=True, reason="test fixture"
    )

    compatibility = client.post(
        "/api/user-adapters/compatibility",
        headers=headers,
        json={
            "profile_id": "custom_readonly",
            "model": "custom/model",
            "role": "code_reviewer",
            "criticality": "medium",
            "data_class": "public",
        },
    )
    assert compatibility.status_code == 200
    assert compatibility.json()["compatibility"]["allowed"] is True

    invalid = client.post(
        "/api/user-adapters/profiles",
        headers=headers,
        json={
            "id": "bad_role",
            "label": "Bad role",
            "adapter_type": "openai_compatible_api",
            "supported_roles": ["wizard"],
        },
    )
    assert invalid.status_code == 400

    monkeypatch.delenv("AITEAM_API_KEY", raising=False)
    os.environ.pop("AITEAM_API_KEY", None)


def test_models_api_exposes_compatibility_without_overwriting_availability(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    record_model_health("openai_api", "gpt-5.6-sol", available=True, reason="test")
    record_model_health("openai_api", "gpt-5.6-luna", available=True, reason="test")
    monkeypatch.setenv("AITEAM_API_KEY", "test-key")
    client = TestClient(app)

    response = client.get(
        "/api/user-adapters/models",
        headers={"x-aiteam-api-key": "test-key"},
        params={
            "profile_id": "openai_api",
            "role": "lead",
            "run_profile": "full_team",
            "criticality": "high",
            "data_class": "internal",
        },
    )

    assert response.status_code == 200
    options = {item["value"]: item for item in response.json()["options"]}
    assert options["gpt-5.6-sol"]["compatibility"]["allowed"] is True
    assert options["gpt-5.6-luna"]["available"] is True
    assert options["gpt-5.6-luna"]["compatibility"]["code"] == "model_tier_insufficient"

    monkeypatch.delenv("AITEAM_API_KEY", raising=False)
    os.environ.pop("AITEAM_API_KEY", None)


def test_user_adapters_login_endpoint_launches_cli(monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_API_KEY", "test-key")

    import api.routers.user_adapters as router_mod

    def fake_launch(cli_id: str) -> dict:
        assert cli_id == "codex"
        return {"cli_id": cli_id, "command": ["codex", "login"], "launched": True}

    monkeypatch.setattr(router_mod, "launch_subscription_login", fake_launch)
    client = TestClient(app)
    response = client.post(
        "/api/user-adapters/login",
        headers={"x-aiteam-api-key": "test-key"},
        json={"cli_id": "codex"},
    )

    assert response.status_code == 200
    assert response.json()["launched"] is True
    monkeypatch.delenv("AITEAM_API_KEY", raising=False)


def test_opencode_status_guides_personal_key_without_collecting_it() -> None:
    status = next(item for item in cli_status() if item["id"] == "opencode")

    assert status["login_command"].endswith(
        "'auth' 'login' '--provider' 'opencode'"
    ) or status["login_command"] == "opencode auth login --provider opencode"
    assert status["setup_url"] == "https://opencode.ai/auth"
    assert len(status["setup_steps"]) == 4
    assert any("solo en la terminal de OpenCode" in step for step in status["setup_steps"])
    assert "no la persiste" in status["credential_storage"]
    assert status["post_login_check"] == "opencode auth list"


def test_windows_login_command_uses_powershell_call_operator() -> None:
    command = _powershell_command([
        r"C:\Users\testuser\AppData\Local\OpenAI\Codex\app\resources\codex.EXE",
        "login",
    ])

    assert command == "& 'C:\\Users\\testuser\\AppData\\Local\\OpenAI\\Codex\\app\\resources\\codex.EXE' 'login'"


def test_windows_login_command_can_be_run_through_cmd() -> None:
    command = _cmd_command([
        r"C:\Users\testuser\AppData\Local\OpenAI\Codex\app\resources\codex.EXE",
        "login",
    ])

    assert command == '"C:\\Users\\testuser\\AppData\\Local\\OpenAI\\Codex\\app\\resources\\codex.EXE" "login"'


def test_windows_login_launcher_writes_plain_quoted_batch_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))

    script = _write_windows_login_launcher(
        "codex",
        [r"C:\Program Files\WindowsApps\OpenAI.Codex\app\resources\codex.EXE", "login"],
    )
    body = script.read_text(encoding="utf-8")

    assert 'call "C:\\Program Files\\WindowsApps\\OpenAI.Codex\\app\\resources\\codex.EXE" "login"' in body
    assert r"\"" not in body


def test_windows_resolver_skips_extensionless_npm_shim(monkeypatch) -> None:
    import aiteam.user_config as config_mod

    monkeypatch.setattr(config_mod.os, "name", "nt")
    monkeypatch.setattr(config_mod, "_known_cli_candidates", lambda _command: [])
    monkeypatch.setattr(
        config_mod,
        "_where_candidates",
        lambda _command: [r"C:\npm\codex", r"C:\npm\codex.cmd"],
    )

    assert config_mod._resolve_cli_executable("codex") == r"C:\npm\codex.cmd"


def test_user_adapter_test_reports_missing_secret(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.setenv("AITEAM_API_KEY", "test-key")

    client = TestClient(app)
    response = client.post(
        "/api/user-adapters/test",
        headers={"x-aiteam-api-key": "test-key"},
        json={"profile_id": "openai_api"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["health"]["status"] == "failed"
    assert payload["health"]["reason"] == "missing_secret"
    monkeypatch.delenv("AITEAM_API_KEY", raising=False)


def test_user_adapter_test_persists_discovery_and_exact_model_probe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.setenv("AITEAM_API_KEY", "test-key")
    store_secret(provider="openai", name="default", secret="sk-test")
    import api.routers.user_adapters as router_mod

    monkeypatch.setattr(router_mod, "_discover_api_catalog", lambda _profile: {
        "status": "current",
        "source": "OpenAI API",
        "models": ["gpt-5.6-sol", "gpt-5.6-terra"],
        "reason": "authenticated_discovery",
    })
    monkeypatch.setattr(router_mod, "_test_profile", lambda _profile, model=None: {
        "status": "ok",
        "reason": "live_test_completed",
        "detail": "ok",
        "tested_model": model,
    })

    response = TestClient(app).post(
        "/api/user-adapters/test",
        headers={"x-aiteam-api-key": "test-key"},
        json={"profile_id": "openai_api", "model": "gpt-5.6-sol"},
    )

    assert response.status_code == 200
    assert response.json()["catalog"] == {
        "status": "current",
        "source": "OpenAI API",
        "reason": "authenticated_discovery",
        "count": 2,
    }
    options, _catalog = executable_model_options("openai_api")
    by_value = {item["value"]: item for item in options}
    assert by_value["gpt-5.6-sol"]["selectable"] is True
    assert by_value["gpt-5.6-terra"]["selectable"] is False
    monkeypatch.delenv("AITEAM_API_KEY", raising=False)


def test_codex_auth_probe_supports_current_tokens_layout(tmp_path: Path, monkeypatch) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        json.dumps({
            "auth_mode": "chatgpt",
            "tokens": {"access_token": "secret-never-returned", "account_id": "acct"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    assert _codex_auth_info() == {"email": None}
