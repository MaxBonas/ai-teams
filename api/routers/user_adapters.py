from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.utils import (
    _require_api_auth_request,
    get_current_workspace,
    resolve_runtime_dir,
)
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
    record_model_catalog,
    record_model_health,
    resolve_adapter_config,
    save_adapter_health,
    store_secret,
    upsert_adapter_profile,
)
from aiteam.policies import canonical_role, role_status
from aiteam.provider_identity import profile_identity
from aiteam.model_compatibility import compatibility_decision
from aiteam.model_catalog_api import (
    catalog_selection_reason,
    rank_catalog_candidates_for_role,
)
from aiteam.model_catalog_service import (
    get_current_model_catalog,
    invalidate_model_catalog_cache,
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
    config: dict[str, Any] = Field(default_factory=dict)
    status: str | None = None
    supported_roles: list[str] | None = None
    data_policy: str | None = None
    privacy_note: str | None = None
    capabilities: list[str] | None = None
    workspace_mode: str | None = None
    mcp_transport: str | None = None
    structured_output: str | None = None
    model_options: list[dict[str, Any]] | None = None


class LoginRequest(BaseModel):
    cli_id: str


class TestAdapterRequest(BaseModel):
    profile_id: str
    model: str | None = None


class CompatibilityRequest(BaseModel):
    profile_id: str
    model: str
    role: str
    run_profile: str = ""
    criticality: str = "medium"
    data_class: str = ""
    required_capabilities: list[str] = Field(default_factory=list)


@router.get("/api/user-adapters/models")
async def get_model_options_for_role(
    request: Request,
    profile_id: str = "",
    role: str = "",
    run_profile: str = "",
    criticality: str = "medium",
    data_class: str = "",
    required_capabilities: str = "",
):
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
        profiles = {str(item.get("id") or ""): item for item in load_adapter_profiles()}
        profile = profiles.get(profile_id, {"id": profile_id})
        # Equipo debe mostrar el catálogo completo: disponibilidad y
        # compatibilidad deshabilitan opciones, no las borran. El ranking por
        # rol no debe perder los campos de inventario/health del catálogo.
        catalog_options = (
            profile.get("model_options")
            if isinstance(profile.get("model_options"), list)
            else []
        )
        catalog_by_value = {
            str(item.get("value") or ""): item for item in catalog_options
        }
        ranked_options = model_options_for_role(profile_id, role, executable_only=False)
        options = [
            {**catalog_by_value.get(str(option.get("value") or ""), {}), **option}
            for option in ranked_options
        ]
        # Compatibilidad transitoria: se conservan todos los campos legacy,
        # pero identidad, score base, orden y razón vienen del read model M.3.
        db_path = resolve_runtime_dir(get_current_workspace()) / "aiteam.db"
        read_model = get_current_model_catalog(
            db_paths=(db_path,) if db_path.is_file() else ()
        )
        catalog_ranked = rank_catalog_candidates_for_role(read_model, role)
        catalog_by_model = {
            str(item.get("identity", {}).get("model_id") or ""): item
            for item in catalog_ranked
            if str(item.get("identity", {}).get("profile_id") or "") == profile_id
        }
        legacy_order = {
            str(option.get("value") or ""): index
            for index, option in enumerate(options)
        }
        canonical_order = {
            str(item.get("identity", {}).get("model_id") or ""): index
            for index, item in enumerate(catalog_ranked)
            if str(item.get("identity", {}).get("profile_id") or "") == profile_id
        }
        options = [
            {
                **option,
                "catalog_candidate_id": catalog_by_model.get(
                    str(option.get("value") or ""), {}
                ).get("candidate_id"),
                "model_role_score": (
                    catalog_by_model.get(str(option.get("value") or ""), {})
                    .get("role_evaluation", {})
                    .get("score")
                ),
                "selection_reason": (
                    catalog_selection_reason(
                        catalog_by_model[str(option.get("value") or "")][
                            "role_evaluation"
                        ]
                    )
                    if str(option.get("value") or "") in catalog_by_model
                    else "role_score_missing"
                ),
            }
            for option in options
        ]
        options.sort(
            key=lambda option: (
                0 if str(option.get("value") or "") in canonical_order else 1,
                canonical_order.get(str(option.get("value") or ""), 10**9),
                legacy_order.get(str(option.get("value") or ""), 10**9),
            )
        )
        role_profile = ROLE_CAPABILITY_PROFILES.get(canonical_role(role), {})
        required = [
            item.strip() for item in required_capabilities.split(",") if item.strip()
        ]
        options = [
            {
                **option,
                "compatibility": compatibility_decision(
                    profile=profile,
                    model=option,
                    role=role,
                    run_profile=run_profile,
                    criticality=criticality,
                    data_class=data_class,
                    required_capabilities=required,
                    role_profile=role_profile,
                    # El POST usa el orden persistido del perfil. Mantenerlo
                    # aquí garantiza paridad aunque la respuesta se reordene.
                    candidate_models=catalog_options,
                ),
            }
            for option in options
        ]
    else:
        profiles = {str(item.get("id") or ""): item for item in load_adapter_profiles()}
        options = profiles.get(profile_id, {}).get(
            "model_options", model_options().get(profile_id, [])
        )
        role_profile = {}
    return {
        "success": True,
        "profile_id": profile_id,
        "role": role,
        "role_profile": role_profile,
        "compatibility_context": {
            "run_profile": run_profile or None,
            "criticality": criticality,
            "data_class": data_class or None,
            "required_capabilities": [
                item.strip()
                for item in required_capabilities.split(",")
                if item.strip()
            ],
        },
        "options": options,
    }


@router.post("/api/user-adapters/compatibility")
async def post_model_compatibility(body: CompatibilityRequest, request: Request):
    """Decisión explicable que compartirán Equipo, mutaciones y preflight."""
    _require_api_auth_request(request)
    profiles = {str(item.get("id") or ""): item for item in load_adapter_profiles()}
    profile = profiles.get(body.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Adapter profile not found")
    options = (
        profile.get("model_options")
        if isinstance(profile.get("model_options"), list)
        else []
    )
    selected = next(
        (item for item in options if str(item.get("value") or "") == body.model),
        None,
    )
    role_profile = ROLE_CAPABILITY_PROFILES.get(canonical_role(body.role), {})
    decision = compatibility_decision(
        profile=profile,
        model=selected,
        role=body.role,
        run_profile=body.run_profile,
        criticality=body.criticality,
        data_class=body.data_class,
        required_capabilities=body.required_capabilities,
        role_profile=role_profile,
        candidate_models=options,
    )
    return {"success": True, "compatibility": decision}


@router.get("/api/user-adapters")
async def get_user_adapters(request: Request):
    _require_api_auth_request(request)
    registry = build_default_registry()
    return {
        "success": True,
        "profiles": [
            {**profile, "identity": profile_identity(profile)}
            for profile in load_adapter_profiles()
        ],
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
    invalidate_model_catalog_cache()
    return {"success": True, "ref": ref, "has_secret": True}


@router.post("/api/user-adapters/profiles")
async def post_user_adapter_profile(body: UpsertProfileRequest, request: Request):
    _require_api_auth_request(request)
    try:
        payload = body.model_dump(exclude_none=True)
        if body.supported_roles is not None:
            normalized_roles: list[str] = []
            for raw_role in body.supported_roles:
                normalized = canonical_role(raw_role)
                if role_status(normalized) == "unknown":
                    raise ValueError(f"unknown supported role: {raw_role}")
                if normalized not in normalized_roles:
                    normalized_roles.append(normalized)
            payload["supported_roles"] = normalized_roles
        if body.workspace_mode not in {None, "none", "read", "write"}:
            raise ValueError("workspace_mode must be one of: none, read, write")
        if body.mcp_transport not in {None, "none", "governed"}:
            raise ValueError("mcp_transport must be one of: none, governed")
        if body.structured_output not in {None, "none", "json_object", "json_schema"}:
            raise ValueError(
                "structured_output must be one of: none, json_object, json_schema"
            )
        if body.model_options is not None:
            normalized_options: list[dict[str, Any]] = []
            seen_models: set[str] = set()
            for option in body.model_options:
                value = str(option.get("value") or "").strip()
                if not value or value in seen_models:
                    raise ValueError("each model option needs a unique non-empty value")
                tier = str(option.get("tier") or "").strip().lower()
                if tier not in {"budget", "standard", "premium"}:
                    raise ValueError(
                        f"model {value!r} needs tier budget, standard or premium"
                    )
                normalized_option = {**option, "value": value, "tier": tier}
                for field in ("allowed_roles", "denied_roles", "best_for"):
                    if field not in option:
                        continue
                    roles: list[str] = []
                    for raw_role in option.get(field) or []:
                        normalized = canonical_role(raw_role)
                        if role_status(normalized) == "unknown":
                            raise ValueError(
                                f"unknown role in model {value!r}: {raw_role}"
                            )
                        if normalized not in roles:
                            roles.append(normalized)
                    normalized_option[field] = roles
                normalized_options.append(normalized_option)
                seen_models.add(value)
            payload["model_options"] = normalized_options
        profile = upsert_adapter_profile(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    invalidate_model_catalog_cache()
    return {
        "success": True,
        "profile": {**profile, "identity": profile_identity(profile)},
    }


@router.post("/api/user-adapters/login")
async def post_user_adapter_login(body: LoginRequest, request: Request):
    _require_api_auth_request(request)
    try:
        result = launch_subscription_login(body.cli_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"CLI not found: {exc.filename or exc}"
        )
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
    catalog_result: dict[str, Any] | None = None
    if str(profile.get("channel") or "") == "api":
        catalog_result = _discover_api_catalog(profile)
        record_model_catalog(
            body.profile_id,
            catalog_result.get("models") or [],
            source=str(catalog_result.get("source") or "provider API"),
            status=str(catalog_result.get("status") or "unverified"),
            reason=str(catalog_result.get("reason") or ""),
        )
    result = _test_profile(profile, model=body.model)
    save_adapter_health(body.profile_id, result)
    tested_model = str(result.get("tested_model") or "").strip()
    if tested_model:
        success = result["status"] == "ok"
        record_model_health(
            body.profile_id,
            tested_model,
            available=success,
            reason=str(
                result.get("reason")
                or ("live_test_completed" if success else "live_test_failed")
            ),
            status="verified" if success else _model_probe_failure_status(result),
        )
    invalidate_model_catalog_cache()
    return {
        "success": result["status"] in {"ok", "installed"},
        "profile_id": body.profile_id,
        "tested_model": tested_model or None,
        "catalog": _redact_catalog_result(catalog_result),
        "health": result,
    }


def _test_profile(
    profile: dict[str, Any], *, model: str | None = None
) -> dict[str, Any]:
    adapter_type = str(profile.get("adapter_type") or "")
    profile_id = str(profile.get("id") or "")
    config = resolve_adapter_config(adapter_type, {"profile_id": profile_id})
    if str(model or "").strip():
        config["model"] = str(model).strip()
    now = datetime.now(timezone.utc).isoformat()

    if adapter_type == "subscription_cli":
        command = (
            config.get("command") if isinstance(config.get("command"), list) else []
        )
        executable = str(command[0]) if command else "codex"
        resolved = _resolve_cli_executable_for_probe(executable)
        if resolved is None:
            return {
                "status": "failed",
                "checked_at": now,
                "reason": "cli_not_found",
                "detail": executable,
            }
        try:
            proc = subprocess.run(
                [resolved, "--version"], capture_output=True, text=True, timeout=15
            )
        except Exception as exc:
            return {
                "status": "failed",
                "checked_at": now,
                "reason": "cli_probe_failed",
                "detail": str(exc),
            }
        if proc.returncode == 0:
            auth = _subscription_auth_probe(profile, config)
            if auth.get("status") == "ok":
                return {
                    "status": "ok",
                    "checked_at": now,
                    "reason": auth.get("reason") or "subscription_auth_present",
                    "detail": auth.get("detail")
                    or (proc.stdout or proc.stderr or "").strip()[:300],
                }
            return {
                "status": "installed",
                "checked_at": now,
                "reason": auth.get("reason") or "cli_installed_auth_not_verified",
                "detail": (proc.stdout or proc.stderr or "").strip()[:300],
                "hint": auth.get("hint")
                or "Login local o API key configurada, y luego vuelve a probar.",
            }
        return {
            "status": "failed",
            "checked_at": now,
            "reason": "cli_version_failed",
            "detail": (proc.stdout + proc.stderr).strip()[:300],
        }

    ref = str(config.get("api_key_ref") or "")
    if str(profile.get("channel") or "") == "api" and not read_secret(ref):
        return {
            "status": "failed",
            "checked_at": now,
            "reason": "missing_secret",
            "detail": ref or "default secret",
        }

    registry = build_default_registry()
    runtime = registry.get(adapter_type)
    if runtime is None:
        return {
            "status": "failed",
            "checked_at": now,
            "reason": "adapter_not_registered",
            "detail": adapter_type,
        }
    with_config = getattr(runtime, "with_config", None)
    if callable(with_config):
        runtime = with_config(config)
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
        env = {
            **env,
            "AITEAM_OPENAI_MODEL": str(config["model"]),
            "AITEAM_GEMINI_MODEL": str(config["model"]),
            "AITEAM_MODEL": str(config["model"]),
        }
    env = inject_adapter_secrets(env, adapter_type, config)
    result = runtime.execute({"id": "adapter-health-check", "issue_id": ""}, env)
    if result.status == "completed":
        return {
            "status": "ok",
            "checked_at": now,
            "reason": "live_test_completed",
            "detail": result.output or "",
            "tested_model": str(config.get("model") or "") or None,
        }
    return {
        "status": "failed",
        "checked_at": now,
        "reason": result.error_code or "live_test_failed",
        "detail": result.error or result.output or "",
        "tested_model": str(config.get("model") or "") or None,
    }


def _discover_api_catalog(profile: dict[str, Any]) -> dict[str, Any]:
    """Authenticated catalog discovery; never treats discovery as a probe."""
    adapter_type = str(profile.get("adapter_type") or "")
    profile_id = str(profile.get("id") or "")
    config = resolve_adapter_config(adapter_type, {"profile_id": profile_id})
    ref = str(config.get("api_key_ref") or "")
    secret = read_secret(ref)
    source = str(profile.get("provider") or adapter_type or "provider API")
    if not secret:
        return {
            "status": "missing_secret",
            "source": source,
            "reason": "missing_secret",
            "models": [],
        }

    provider = str(profile.get("provider") or "").strip().lower()
    if adapter_type == "gemini_api" or provider in {"google", "gemini"}:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models?"
            + urllib.parse.urlencode({"key": secret})
        )
        headers: dict[str, str] = {}
        list_key, id_key = "models", "name"
    else:
        base_url = str(config.get("base_url") or "").rstrip("/")
        if not base_url:
            base_url = (
                "https://api.anthropic.com/v1"
                if "anthropic" in provider
                else "https://api.openai.com/v1"
            )
        url = f"{base_url}/models"
        if "anthropic" in provider:
            headers = {"x-api-key": secret, "anthropic-version": "2023-06-01"}
        else:
            headers = {"Authorization": f"Bearer {secret}"}
        list_key, id_key = "data", "id"
    try:
        models: list[str] = []
        next_token = ""
        for _page in range(20):
            page_url = url
            if next_token:
                token_key = "pageToken" if list_key == "models" else "after_id"
                separator = "&" if "?" in page_url else "?"
                page_url = f"{page_url}{separator}{urllib.parse.urlencode({token_key: next_token})}"
            request = urllib.request.Request(
                page_url,
                headers={**headers, "Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            page_items = payload.get(list_key) or []
            for item in page_items:
                if not isinstance(item, dict):
                    continue
                value = str(item.get(id_key) or "").strip()
                if value.startswith("models/"):
                    value = value.removeprefix("models/")
                methods = item.get("supportedGenerationMethods")
                if methods and "generateContent" not in methods:
                    continue
                if value:
                    models.append(value)
            if list_key == "models":
                next_token = str(payload.get("nextPageToken") or "")
            elif payload.get("has_more") and page_items:
                next_token = str(
                    payload.get("last_id") or page_items[-1].get("id") or ""
                )
            else:
                next_token = ""
            if not next_token:
                break
        return {
            "status": "current",
            "source": source,
            "models": sorted(set(models)),
            "reason": "authenticated_discovery",
        }
    except urllib.error.HTTPError as exc:
        return {
            "status": "rate_limited" if exc.code == 429 else "failed",
            "source": source,
            "models": [],
            "reason": f"http_{exc.code}",
        }
    except Exception as exc:
        return {
            "status": "failed",
            "source": source,
            "models": [],
            "reason": exc.__class__.__name__,
        }


def _model_probe_failure_status(result: dict[str, Any]) -> str:
    reason = str(result.get("reason") or "").lower()
    detail = str(result.get("detail") or "").lower()
    combined = f"{reason} {detail}"
    if any(token in combined for token in ("429", "rate_limit", "quota")):
        return "rate_limited"
    if any(token in combined for token in ("retired", "deprecated", "removed")):
        return "retired"
    return "unavailable"


def _redact_catalog_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {key: value for key, value in result.items() if key != "models"} | {
        "count": len(result.get("models") or []),
    }


def _subscription_auth_probe(
    profile: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    provider = str(profile.get("provider") or "").lower()
    cli_kind = str(config.get("cli_kind") or "").lower()
    ref = str(config.get("api_key_ref") or "").strip()
    if ref and read_secret(ref):
        return {
            "status": "ok",
            "reason": "api_key_present",
            "detail": f"{ref} guardada en vault local",
        }

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
            return {
                "status": "ok",
                "reason": "codex_native_auth_present",
                "detail": detail,
            }
        return {
            "status": "installed",
            "reason": "codex_auth_not_verified",
            "hint": "Ejecuta `codex login` o guarda una OpenAI API key.",
        }

    if "antigravity" in provider or cli_kind == "antigravity":
        command = (
            config.get("command")
            if isinstance(config.get("command"), list)
            else ["agy"]
        )
        executable = _resolve_cli_executable_for_probe(str(command[0] or "agy"))
        if executable:
            try:
                probe = subprocess.run(
                    [
                        executable,
                        "--new-project",
                        "--print",
                        "Reply exactly OK",
                        "--mode",
                        "plan",
                        "--sandbox",
                        "--print-timeout",
                        "30s",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=45,
                    encoding="utf-8",
                    errors="replace",
                )
                if probe.returncode == 0 and "OK" in (probe.stdout or ""):
                    return {
                        "status": "ok",
                        "reason": "antigravity_keyring_auth_present",
                        "detail": "Antigravity auth verificado en vivo",
                    }
                return {
                    "status": "installed",
                    "reason": "antigravity_auth_not_verified",
                    "hint": (probe.stderr or probe.stdout or "").strip()[:300],
                }
            except Exception as exc:
                return {
                    "status": "installed",
                    "reason": "antigravity_auth_probe_failed",
                    "hint": str(exc)[:300],
                }

    if "opencode" in provider or cli_kind == "opencode":
        command = (
            config.get("command")
            if isinstance(config.get("command"), list)
            else ["opencode"]
        )
        executable = _resolve_cli_executable_for_probe(str(command[0] or "opencode"))
        if executable:
            try:
                probe = subprocess.run(
                    [executable, "auth", "list"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    encoding="utf-8",
                    errors="replace",
                )
                output = ((probe.stdout or "") + (probe.stderr or "")).strip()
                if probe.returncode == 0 and "opencode" in output.lower():
                    return {
                        "status": "ok",
                        "reason": "opencode_zen_auth_present",
                        "detail": "Sesión OpenCode Zen detectada; la credencial permanece bajo control de OpenCode.",
                    }
                return {
                    "status": "installed",
                    "reason": "opencode_auth_not_verified",
                    "hint": (
                        "OpenCode Zen exige una API key personal incluso para modelos de "
                        "precio temporalmente cero. Ejecuta "
                        "`opencode auth login --provider opencode`, conecta tu cuenta y "
                        "acepta sus condiciones; AI Teams no puede automatizar esa decisión."
                    ),
                }
            except Exception as exc:
                return {
                    "status": "installed",
                    "reason": "opencode_auth_probe_failed",
                    "hint": str(exc)[:300],
                }

    return {"status": "installed", "reason": "cli_installed_auth_not_verified"}


def _resolve_cli_executable_for_probe(name: str) -> str | None:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    if os.name == "nt" and name.lower() == "agy":
        candidate = (
            Path(os.environ.get("LOCALAPPDATA") or "") / "agy" / "bin" / "agy.exe"
        )
        if candidate.is_file():
            return str(candidate)
    return None


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
    if model and not any(
        n == model or n.startswith(model.split(":")[0]) for n in names
    ):
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
