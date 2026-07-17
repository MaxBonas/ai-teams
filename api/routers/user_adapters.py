from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import shutil
import subprocess

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.utils import _require_api_auth_request
from aiteam.adapters.registry import build_default_registry
from aiteam.user_config import (
    ROLE_CAPABILITY_PROFILES,
    inject_adapter_secrets,
    model_options,
    model_options_for_role,
    cli_status,
    launch_subscription_login,
    list_secrets,
    load_adapter_profiles,
    read_secret,
    resolve_adapter_config,
    save_adapter_health,
    store_secret,
    upsert_adapter_profile,
)

router = APIRouter()


class StoreSecretRequest(BaseModel):
    provider: str
    name: str = "default"
    secret: str


class UpsertProfileRequest(BaseModel):
    id: str
    label: str
    adapter_type: str
    channel: str = "api"
    provider: str = ""
    config: dict[str, Any] = {}
    status: str | None = None


class LoginRequest(BaseModel):
    cli_id: str


class TestAdapterRequest(BaseModel):
    profile_id: str


@router.get("/api/user-adapters/models")
async def get_model_options_for_role(request: Request, profile_id: str = "", role: str = ""):
    """Return model options for a profile, sorted and annotated for a specific role.

    Query params:
      profile_id  — adapter profile id (e.g. ``openai_api``, ``codex_subscription``)
      role        — agent role (e.g. ``engineer``, ``lead``, ``reviewer``)

    If role is provided the options are sorted by role fit score and include
    ``recommended``, ``fit_reason`` and ``role_score`` fields.
    If role is omitted returns options in default order without role annotation.
    Also returns the role capability profile so the UI can show workspace/capability notes.
    """
    _require_api_auth_request(request)
    if role:
        options = model_options_for_role(profile_id, role)
        role_profile = ROLE_CAPABILITY_PROFILES.get(role.lower(), {})
    else:
        options = model_options().get(profile_id, [])
        role_profile = {}
    return {
        "success": True,
        "profile_id": profile_id,
        "role": role,
        "role_profile": role_profile,
        "options": options,
    }


@router.get("/api/user-adapters")
async def get_user_adapters(request: Request):
    _require_api_auth_request(request)
    registry = build_default_registry()
    return {
        "success": True,
        "profiles": load_adapter_profiles(),
        "secrets": list_secrets(),
        "cli_status": cli_status(),
        "registered_adapters": [d.__dict__ for d in registry.descriptors()],
        "model_options": model_options(),
        # Role capability profiles let the UI show model recommendations per role
        "role_capability_profiles": ROLE_CAPABILITY_PROFILES,
    }


@router.post("/api/user-adapters/secrets")
async def post_user_adapter_secret(body: StoreSecretRequest, request: Request):
    _require_api_auth_request(request)
    try:
        ref = store_secret(provider=body.provider, name=body.name, secret=body.secret)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, "ref": ref, "has_secret": True}


@router.post("/api/user-adapters/profiles")
async def post_user_adapter_profile(body: UpsertProfileRequest, request: Request):
    _require_api_auth_request(request)
    try:
        profile = upsert_adapter_profile(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, "profile": profile}


@router.post("/api/user-adapters/login")
async def post_user_adapter_login(body: LoginRequest, request: Request):
    _require_api_auth_request(request)
    try:
        result = launch_subscription_login(body.cli_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"CLI not found: {exc.filename or exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, **result}


@router.post("/api/user-adapters/test")
async def post_user_adapter_test(body: TestAdapterRequest, request: Request):
    _require_api_auth_request(request)
    profiles = {str(p.get("id") or ""): p for p in load_adapter_profiles()}
    profile = profiles.get(body.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Adapter profile not found")
    result = _test_profile(profile)
    save_adapter_health(body.profile_id, result)
    return {"success": result["status"] in {"ok", "installed"}, "profile_id": body.profile_id, "health": result}


def _test_profile(profile: dict[str, Any]) -> dict[str, Any]:
    adapter_type = str(profile.get("adapter_type") or "")
    profile_id = str(profile.get("id") or "")
    config = resolve_adapter_config(adapter_type, {"profile_id": profile_id})
    now = datetime.now(timezone.utc).isoformat()

    if adapter_type == "subscription_cli":
        command = config.get("command") if isinstance(config.get("command"), list) else []
        executable = str(command[0]) if command else "codex"
        resolved = shutil.which(executable)
        if resolved is None:
            return {"status": "failed", "checked_at": now, "reason": "cli_not_found", "detail": executable}
        try:
            proc = subprocess.run([resolved, "--version"], capture_output=True, text=True, timeout=15)
        except Exception as exc:
            return {"status": "failed", "checked_at": now, "reason": "cli_probe_failed", "detail": str(exc)}
        if proc.returncode == 0:
            auth = _subscription_auth_probe(profile, config)
            if auth.get("status") == "ok":
                return {
                    "status": "ok",
                    "checked_at": now,
                    "reason": auth.get("reason") or "subscription_auth_present",
                    "detail": auth.get("detail") or (proc.stdout or proc.stderr or "").strip()[:300],
                }
            return {
                "status": "installed",
                "checked_at": now,
                "reason": auth.get("reason") or "cli_installed_auth_not_verified",
                "detail": (proc.stdout or proc.stderr or "").strip()[:300],
                "hint": auth.get("hint") or "Login local o API key configurada, y luego vuelve a probar.",
            }
        return {
            "status": "failed",
            "checked_at": now,
            "reason": "cli_version_failed",
            "detail": (proc.stdout + proc.stderr).strip()[:300],
        }

    ref = str(config.get("api_key_ref") or "")
    if adapter_type in {"openai_api", "gemini_api", "anthropic_api", "anthropic_sonnet"} and not read_secret(ref):
        return {"status": "failed", "checked_at": now, "reason": "missing_secret", "detail": ref or "default secret"}

    registry = build_default_registry()
    runtime = registry.get(adapter_type)
    if runtime is None:
        return {"status": "failed", "checked_at": now, "reason": "adapter_not_registered", "detail": adapter_type}
    env = runtime.build_env(
        run_id="adapter-health-check",
        wake_context={
            "issue_id": "",
            "reason": "adapter_health_check",
            "agent_role": "lead",
            "agent_skill": "Eres un agente de prueba. Devuelve una respuesta minima correcta.",
            "wake_payload_json": '{"health_check": true}',
        },
    )
    if config.get("model"):
        env = {**env, "AITEAM_OPENAI_MODEL": str(config["model"]), "AITEAM_GEMINI_MODEL": str(config["model"])}
    env = inject_adapter_secrets(env, adapter_type, config)
    result = runtime.execute({"id": "adapter-health-check", "issue_id": ""}, env)
    if result.status == "completed":
        return {"status": "ok", "checked_at": now, "reason": "live_test_completed", "detail": result.output or ""}
    return {
        "status": "failed",
        "checked_at": now,
        "reason": result.error_code or "live_test_failed",
        "detail": result.error or result.output or "",
    }


def _subscription_auth_probe(profile: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    provider = str(profile.get("provider") or "").lower()
    cli_kind = str(config.get("cli_kind") or "").lower()
    ref = str(config.get("api_key_ref") or "").strip()
    if ref and read_secret(ref):
        return {"status": "ok", "reason": "api_key_present", "detail": f"{ref} guardada en vault local"}

    # Perfiles LOCALES (codex --oss contra Ollama/LM Studio): no necesitan
    # auth de ChatGPT — la verificación honesta es que el runtime local
    # responda y tenga el modelo descargado. Antes quedaban eternamente en
    # "auth sin verificar" pidiendo un login que no aplica.
    if config.get("oss") or str(config.get("local_provider") or "").strip():
        return _local_runtime_probe(config)

    if cli_kind == "codex" or "codex" in provider:
        auth = _codex_auth_info()
        if auth:
            email = str(auth.get("email") or "").strip()
            detail = f"Codex auth local detectado{f' para {email}' if email else ''}."
            return {"status": "ok", "reason": "codex_native_auth_present", "detail": detail}
        return {
            "status": "installed",
            "reason": "codex_auth_not_verified",
            "hint": "Ejecuta `codex login` o guarda una OpenAI API key.",
        }

    if "gemini" in provider:
        if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            return {"status": "ok", "reason": "gemini_env_key_present", "detail": "GEMINI_API_KEY/GOOGLE_API_KEY presente"}
        return {
            "status": "installed",
            "reason": "gemini_auth_not_verified",
            "hint": "Ejecuta `gemini auth login` o guarda una Google Gemini API key.",
        }

    return {"status": "installed", "reason": "cli_installed_auth_not_verified"}


def _local_runtime_probe(config: dict[str, Any]) -> dict[str, Any]:
    """Salud real de un perfil local: ¿responde el runtime y tiene el modelo?"""
    import urllib.request

    local_provider = str(config.get("local_provider") or "ollama").strip().lower()
    model = str(config.get("model") or "").strip()
    if local_provider == "ollama":
        url, models_key, name_key = "http://localhost:11434/api/tags", "models", "name"
    else:  # lmstudio y compatibles OpenAI
        url, models_key, name_key = "http://localhost:1234/v1/models", "data", "id"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        names = [str(m.get(name_key) or "") for m in (payload.get(models_key) or [])]
    except Exception as exc:
        return {
            "status": "installed",
            "reason": f"{local_provider}_not_running",
            "hint": f"Arranca {local_provider} y vuelve a probar ({exc.__class__.__name__}).",
        }
    if model and not any(n == model or n.startswith(model.split(":")[0]) for n in names):
        return {
            "status": "installed",
            "reason": "local_model_missing",
            "hint": f"El runtime responde pero no tiene {model!r}: descárgalo (p.ej. `ollama pull {model}`).",
        }
    return {
        "status": "ok",
        "reason": "local_runtime_ready",
        "detail": f"{local_provider} responde{f' con {model}' if model else ''} ({len(names)} modelo(s))",
    }


def _codex_auth_info() -> dict[str, Any] | None:
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    auth_path = codex_home / "auth.json"
    try:
        parsed = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    auth_block = parsed.get("https://api.openai.com/auth")
    if isinstance(auth_block, dict):
        token = auth_block.get("access_token") or auth_block.get("accessToken")
        email = auth_block.get("chatgpt_user_email") or auth_block.get("email")
        if token:
            return {"email": email or None}
    if parsed.get("access_token") or parsed.get("accessToken"):
        return {"email": parsed.get("email")}
    # Codex Desktop/CLI actuales persisten la sesión de ChatGPT bajo `tokens`.
    # Solo comprobamos presencia; nunca devolvemos ni registramos credenciales.
    tokens = parsed.get("tokens")
    if isinstance(tokens, dict) and (
        tokens.get("access_token") or tokens.get("accessToken")
    ):
        return {"email": tokens.get("email") or parsed.get("email")}
    return None
