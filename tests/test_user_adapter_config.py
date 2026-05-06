from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app
from aiteam.user_config import (
    _cmd_command,
    _write_windows_login_launcher,
    inject_adapter_secrets,
    load_adapter_profiles,
    _powershell_command,
    resolve_adapter_config,
    store_secret,
)


def test_user_adapter_profiles_include_subscriptions_and_local_models(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))

    profiles = {profile["id"]: profile for profile in load_adapter_profiles()}

    assert profiles["codex_subscription"]["adapter_type"] == "subscription_cli"
    assert profiles["codex_subscription"]["config"]["api_key_ref"] == "secret:openai:default"
    assert profiles["gemini_subscription"]["provider"] == "google-gemini"
    assert profiles["gemini_subscription"]["config"]["api_key_ref"] == "secret:google:default"
    assert profiles["claude_subscription_blocked"]["status"] == "blocked_by_provider"
    assert profiles["local_qwen_ollama"]["config"]["model"] == "qwen2.5-coder:14b"
    assert profiles["local_gem4_lmstudio"]["config"]["local_provider"] == "lmstudio"
    assert profiles["openai_api"]["model_options"][0]["value"] in {"o3", "o4-mini", "gpt-4.1", "gpt-4.1-mini"}
    assert profiles["openai_api"]["health"]["status"] == "untested"


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
