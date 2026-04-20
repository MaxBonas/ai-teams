from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelProfile:
    adapter_name: str
    provider: str
    model: str
    tier: str
    intelligence_rank: int
    coding_rank: int
    reasoning_rank: int
    trust_rank: int
    local_allowed_for_team_lead: bool
    api_allowed_for_team_lead: bool
    notes: str = ""


def default_model_catalog() -> dict[str, ModelProfile]:
    # Cadena de fallback para Team Lead (router selecciona por tier + rank):
    #
    #  PRO SUBSCRIPTION (canal subscription, require CLI):
    #    1. openai_pro_cli  → OpenAI Codex CLI (gpt-5.4) ← modelo mas avanzado disponible
    #    2. claude_pro_cli  → Claude Pro CLI (claude-sonnet-4-6)
    #    3. gemini_pro_cli  → Gemini CLI (gemini-3-flash via CLI)
    #
    #  API senior_cloud (sin CLI, solo key):
    #    4. claude_opus     → claude-opus-4-6        (Anthropic, maximo razonamiento)
    #    5. claude_sonnet   → claude-sonnet-4-6       (Anthropic, balance calidad/precio)
    #    6. gemini31_pro    → gemini-3.1-pro-preview  (Google, frontier)
    #    7. gemini3_pro     → gemini-3-pro-preview    (Google)
    #    8. gemini3_flash   → gemini-3-flash-preview  (Google, rapido)
    #
    #  API advanced_api con api_allowed_for_team_lead=True:
    #    9. openai_codex_mini → gpt-5-mini           (OpenAI API, barato para Solo Lead)
    #   10. groq_gpt120b      → openai/gpt-oss-120b  (Groq GRATIS, 120B params)
    #   11. groq_kimi_k2      → kimi-k2-instruct     (Groq GRATIS, frontier)
    #
    #  WORKER (budget_api — Engineer, Reviewer, QA, Researcher):
    #    claude_haiku_api, gemini_flash, gemini_flash_lite,
    #    groq_compound, groq_llama4, groq_llama33, groq_llama8b
    #
    profiles = [
        # ── TEST ALIASES (mantener compatibilidad con tests) ──────────────
        ModelProfile(
            "openai_pro",
            "openai",
            "gpt-pro",
            "senior_cloud",
            100, 100, 99, 94,
            False, True,
            "Alias de tests para runtime OpenAI subscription",
        ),
        ModelProfile(
            "claude_pro",
            "anthropic",
            "claude-pro",
            "senior_cloud",
            98, 97, 99, 92,
            False, True,
            "Alias de tests para runtime Claude subscription",
        ),
        ModelProfile(
            "gemini_pro",
            "google",
            "gemini-pro",
            "senior_cloud",
            97, 94, 98, 90,
            False, True,
            "Alias de tests para runtime Gemini subscription",
        ),

        # ── PRO SUBSCRIPTION CLIs ─────────────────────────────────────────
        ModelProfile(
            "openai_pro_cli",
            "openai",
            "gpt-5.4",                          # Codex CLI usa gpt-5.4 (confirmado)
            "senior_cloud",
            99, 99, 98, 95,
            False, True,
            "OpenAI Codex CLI — gpt-5.4 (mas avanzado disponible, confirmado 2026-03-27)",
        ),
        ModelProfile(
            "claude_pro_cli",
            "anthropic",
            "claude-sonnet-4-6",
            "senior_cloud",
            97, 96, 97, 94,
            False, True,
            "Claude Pro CLI — Sonnet 4.6 (no disponible dentro de sesion Claude Code)",
        ),
        ModelProfile(
            "gemini_pro_cli",
            "google",
            "gemini-3-flash-preview",
            "senior_cloud",
            97, 95, 97, 91,
            False, True,
            "Gemini Pro CLI — Gemini 3 Flash (CLI no configurado aun, usa API como fallback)",
        ),

        # ── API SENIOR — Team Lead primary API fallback ───────────────────
        ModelProfile(
            "claude_opus",
            "anthropic",
            "claude-opus-4-6",
            "senior_cloud",
            100, 98, 100, 96,
            False, True,
            "Claude Opus 4.6 — maximo razonamiento, TL API primario (2393ms)",
        ),
        ModelProfile(
            "claude_sonnet",
            "anthropic",
            "claude-sonnet-4-6",
            "senior_cloud",
            97, 96, 97, 94,
            False, True,
            "Claude Sonnet 4.6 — balance calidad/precio, TL API secundario (2451ms)",
        ),
        ModelProfile(
            "gemini31_pro",
            "google",
            "gemini-3.1-pro-preview",
            "senior_cloud",
            98, 96, 98, 92,
            False, True,
            "Gemini 3.1 Pro Preview — frontier Google, TL API (6210ms)",
        ),
        ModelProfile(
            "gemini3_pro",
            "google",
            "gemini-3-pro-preview",
            "senior_cloud",
            97, 95, 97, 91,
            False, True,
            "Gemini 3 Pro Preview — Google frontier (7236ms)",
        ),
        ModelProfile(
            "gemini3_flash",
            "google",
            "gemini-3-flash-preview",
            "senior_cloud",
            95, 94, 95, 90,
            False, True,
            "Gemini 3 Flash Preview — rapido y capable (2419ms)",
        ),
        ModelProfile(
            "gemini25_pro",
            "google",
            "gemini-2.5-pro",
            "senior_cloud",
            96, 94, 96, 91,
            False, True,
            "Gemini 2.5 Pro — solido pero lento (11991ms), usar solo si Gemini 3 falla",
        ),

        # ── API ADVANCED — TL fallback y Solo Lead barato ─────────────────
        ModelProfile(
            "openai_codex_mini",
            "openai",
            "gpt-5-mini",
            "advanced_api",
            90, 91, 90, 90,
            False, True,
            "OpenAI gpt-5-mini — API barata para perfil Solo Lead estilo Codex",
        ),
        ModelProfile(
            "groq_gpt120b",
            "groq",
            "openai/gpt-oss-120b",
            "advanced_api",
            90, 89, 90, 82,
            False, True,
            "GPT OSS 120B via Groq GRATIS — 593ms, TL fallback de emergencia",
        ),
        ModelProfile(
            "groq_kimi_k2",
            "groq",
            "moonshotai/kimi-k2-instruct",
            "advanced_api",
            88, 87, 88, 80,
            False, True,
            "Kimi K2 via Groq GRATIS — 131K ctx, 2280ms, frontier open model",
        ),

        # ── WORKER MODELS — Engineer, Reviewer, QA, Researcher ───────────
        ModelProfile(
            "claude_haiku_api",
            "anthropic",
            "claude-haiku-4-5-20251001",
            "budget_api",
            82, 83, 82, 87,
            False, False,
            "Claude Haiku 4.5 — rapido y barato, ideal Engineer/QA (849ms)",
        ),
        ModelProfile(
            "gemini_flash",
            "google",
            "gemini-2.5-flash",
            "budget_api",
            86, 84, 86, 84,
            False, False,
            "Gemini 2.5 Flash — Google worker principal (4874ms)",
        ),
        ModelProfile(
            "gemini_flash_lite",
            "google",
            "gemini-2.5-flash-lite",
            "budget_api",
            78, 76, 78, 80,
            False, False,
            "Gemini 2.5 Flash Lite — mas rapido de Google (458ms), tareas simples",
        ),
        ModelProfile(
            "groq_compound",
            "groq",
            "groq/compound",
            "budget_api",
            84, 83, 84, 78,
            False, False,
            "Groq Compound — router inteligente Groq GRATIS (649ms)",
        ),
        ModelProfile(
            "groq_fallback",
            "groq",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "budget_api",
            79, 78, 79, 74,
            False, False,
            "Llama 4 Scout via Groq GRATIS — 131K ctx, 556ms",
        ),
        ModelProfile(
            "groq_llama33",
            "groq",
            "llama-3.3-70b-versatile",
            "budget_api",
            82, 81, 82, 76,
            False, False,
            "Llama 3.3 70B via Groq GRATIS — solido worker (753ms)",
        ),
        ModelProfile(
            "groq_llama8b",
            "groq",
            "llama-3.1-8b-instant",
            "budget_api",
            68, 66, 68, 65,
            False, False,
            "Llama 3.1 8B instant via Groq GRATIS — ultra rapido (548ms), tareas triviales",
        ),

        # ── ALIASES compatibilidad ────────────────────────────────────────
        ModelProfile(
            "openai_api",
            "openai",
            "gpt-4.1-mini",
            "advanced_api",
            82, 84, 83, 86,
            False, True,
            "OpenAI gpt-4.1-mini (quota agotada, mantener para cuando se renueve)",
        ),
        ModelProfile(
            "openai_api_mini",
            "openai",
            "gpt-4.1-mini",
            "advanced_api",
            82, 84, 83, 86,
            False, True,
            "Alias openai_api_mini — quota agotada",
        ),
        ModelProfile(
            "gpt-4o-mini",
            "openai",
            "gpt-4o-mini",
            "budget_api",
            80, 81, 80, 85,
            False, True,
            "gpt-4o-mini — quota agotada",
        ),
        ModelProfile(
            "openai_api_fast",
            "openai",
            "gpt-4o-mini",
            "budget_api",
            80, 81, 80, 85,
            False, True,
            "Alias openai_api_fast — quota agotada",
        ),
        ModelProfile(
            "groq_fallback",
            "groq",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "budget_api",
            79,
            78,
            79,
            74,
            False,
            False,
            "Fast free-tier fallback (Llama 4 Scout via Groq)",
        ),
        ModelProfile(
            "ollama_qwen_coder_local",
            "local",
            "qwen2.5-coder:14b",
            "local",
            78,
            88,
            80,
            76,
            False,
            False,
            "Local coding fallback, never team lead",
        ),
    ]
    return {profile.adapter_name: profile for profile in profiles}


def load_model_catalog(
    project_root: Path | None = None, runtime_dir: Path | None = None
) -> dict[str, ModelProfile]:
    catalog = default_model_catalog()
    if project_root is None and runtime_dir is None:
        return catalog
    candidates: list[Path] = []
    if project_root is not None:
        project_root = Path(project_root)
        candidates.extend(
            [
                project_root / "config" / "model_catalog.json",
                project_root / ".aiteam" / "model_catalog.json",
                project_root / "runtime" / "model_catalog.json",
            ]
        )
    if runtime_dir is not None:
        candidates.append(runtime_dir / "model_catalog.json")
    for path in candidates:
        loaded = _load_model_catalog_file(path)
        if loaded:
            catalog.update(loaded)
    return catalog


def write_default_model_catalog_example(path: Path) -> None:
    payload = {"models": [asdict(item) for item in default_model_catalog().values()]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _load_model_catalog_file(path: Path) -> dict[str, ModelProfile]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("models", [])
    if not isinstance(rows, list):
        return {}
    loaded: dict[str, ModelProfile] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            profile = ModelProfile(
                adapter_name=str(item.get("adapter_name", "")).strip(),
                provider=str(item.get("provider", "")).strip().lower(),
                model=str(item.get("model", "")).strip(),
                tier=str(item.get("tier", "standard_api")).strip().lower(),
                intelligence_rank=int(item.get("intelligence_rank", 50)),
                coding_rank=int(item.get("coding_rank", 50)),
                reasoning_rank=int(item.get("reasoning_rank", 50)),
                trust_rank=int(item.get("trust_rank", 50)),
                local_allowed_for_team_lead=bool(
                    item.get("local_allowed_for_team_lead", False)
                ),
                api_allowed_for_team_lead=bool(
                    item.get("api_allowed_for_team_lead", False)
                ),
                notes=str(item.get("notes", "")),
            )
        except (TypeError, ValueError):
            continue
        if profile.adapter_name:
            loaded[profile.adapter_name] = profile
    return loaded


def provider_smoke_status(runtime_dir: Path | None) -> dict[str, bool]:
    if runtime_dir is None:
        return {}
    path = runtime_dir / "provider_smoke.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("smoke", [])
    if not isinstance(rows, list):
        return {}
    return {
        str(item.get("name", "")).strip(): bool(item.get("healthy", False))
        for item in rows
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    }


def provider_smoke_details(runtime_dir: Path | None) -> dict[str, dict[str, object]]:
    if runtime_dir is None:
        return {}
    path = runtime_dir / "provider_smoke.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("smoke", [])
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        result[name] = dict(item)
    return result
