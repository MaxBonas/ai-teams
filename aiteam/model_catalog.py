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
    profiles = [
        ModelProfile(
            "openai_pro",
            "openai",
            "gpt-pro",
            "senior_cloud",
            100,
            100,
            99,
            94,
            False,
            True,
            "Alias for tests and generic OpenAI senior subscription runtime",
        ),
        ModelProfile(
            "openai_pro_cli",
            "openai",
            "gpt-4o",
            "senior_cloud",
            96,
            97,
            95,
            94,
            False,
            True,
            "Primary senior coding lead",
        ),
        ModelProfile(
            "claude_pro",
            "anthropic",
            "claude-pro",
            "senior_cloud",
            98,
            97,
            99,
            92,
            False,
            True,
            "Alias for tests and generic Claude senior subscription runtime",
        ),
        ModelProfile(
            "claude_pro_cli",
            "anthropic",
            "claude-3-5-sonnet-20241022",
            "senior_cloud",
            95,
            95,
            95,
            92,
            False,
            True,
            "Strong reviewer and reasoning lead",
        ),
        ModelProfile(
            "gemini_pro",
            "google",
            "gemini-pro",
            "senior_cloud",
            97,
            94,
            98,
            90,
            False,
            True,
            "Alias for tests and generic Gemini senior subscription runtime",
        ),
        ModelProfile(
            "gemini_pro_cli",
            "google",
            "gemini-1.5-pro",
            "senior_cloud",
            92,
            90,
            92,
            88,
            False,
            True,
            "High-capacity cloud fallback",
        ),
        ModelProfile(
            "openai_api",
            "openai",
            "gpt-4.1-mini",
            "advanced_api",
            82,
            84,
            83,
            86,
            False,
            True,
            "Efficient API fallback",
        ),
        ModelProfile(
            "gpt-4o-mini",
            "openai",
            "gpt-4o-mini",
            "budget_api",
            80,
            81,
            80,
            85,
            False,
            True,
            "Cheap advanced API fallback",
        ),
        ModelProfile(
            "groq_fallback",
            "groq",
            "llama-3.3-70b-versatile",
            "budget_api",
            74,
            73,
            74,
            70,
            False,
            False,
            "Reasonable non-primary API",
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
        candidates.extend(
            [
                project_root / "config" / "model_catalog.json",
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
