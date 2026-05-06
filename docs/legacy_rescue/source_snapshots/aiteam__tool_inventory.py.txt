from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_INVENTORY_SCHEMA_VERSION = "tool_inventory_v2"
EFFECTIVE_TOOL_INVENTORY_SCHEMA_VERSION = "effective_tool_inventory_v1"

CANONICAL_TOOL_CAPABILITY_CATALOG: dict[str, dict[str, str]] = {
    "repo_read": {
        "description": "Lectura estructurada o exploratoria de repositorio y archivos",
        "tool_family": "repo",
    },
    "repo_write": {
        "description": "Edicion o publicacion de cambios sobre el repositorio",
        "tool_family": "repo",
    },
    "browser_nav": {
        "description": "Navegacion browser, DOM, captura y reproduccion de pasos",
        "tool_family": "browser",
    },
    "browser_test": {
        "description": "Tests o verificaciones automatizadas de navegador/UI",
        "tool_family": "browser",
    },
    "lsp_symbols": {
        "description": "Navegacion semantica por simbolos, definiciones y diagnosticos",
        "tool_family": "lsp",
    },
    "lsp_references": {
        "description": "Impacto de referencias, rename y dependencias semanticas",
        "tool_family": "lsp",
    },
    "test_execute": {
        "description": "Ejecucion de suites, checks y validaciones automatizadas",
        "tool_family": "execution",
    },
    "build_execute": {
        "description": "Builds, renders, empaquetado o pipelines de entrega",
        "tool_family": "execution",
    },
    "external_mcp": {
        "description": "Uso de MCP server o integracion externa estructurada",
        "tool_family": "mcp",
    },
    "skill_run": {
        "description": "Ejecucion guiada por skill/playbook operativo",
        "tool_family": "skill",
    },
}

_CAPABILITY_ALIAS_MAP: dict[str, str] = {
    "analysis": "repo_read",
    "knowledge_base": "repo_read",
    "documentation": "repo_read",
    "ground_truth": "repo_read",
    "github": "repo_read",
    "issue_triage": "repo_read",
    "pr_management": "repo_write",
    "messaging": "external_mcp",
    "automation": "external_mcp",
    "qa": "browser_test",
    "browser_testing": "browser_test",
    "android": "browser_nav",
    "rendering": "build_execute",
    "release": "build_execute",
    "video_generation": "build_execute",
    "multimodal": "skill_run",
}


@dataclass
class ToolRecord:
    project_name: str
    path: str
    tags: list[str]
    capabilities: list[str]
    role_targets: list[str]
    priority: str
    requires_approval: bool
    enabled: bool
    adapter_name: str
    category: str
    canonical_capabilities: list[str]
    cost_tier: str
    latency_tier: str
    risk_tier: str
    environment_targets: list[str]


def scan_tools(root: Path, limit: int = 200) -> list[ToolRecord]:
    if not root.exists() or not root.is_dir():
        return []

    records: list[ToolRecord] = []
    projects = [path for path in root.iterdir() if path.is_dir()]
    projects.sort(key=lambda item: item.name.lower())
    for project in projects[: max(1, limit)]:
        record = _build_record(project)
        if record is not None:
            records.append(record)
    return records


def write_inventory(root: Path, output_path: Path, limit: int = 200) -> dict:
    records = scan_tools(root, limit=limit)
    payload = {
        "schema_version": TOOL_INVENTORY_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root.resolve()),
        "total": len(records),
        "capability_catalog": canonical_tool_capabilities(),
        "tools": [asdict(record) for record in records],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return payload


def canonical_tool_capabilities() -> list[dict[str, str]]:
    return [
        {"name": name, **meta}
        for name, meta in sorted(CANONICAL_TOOL_CAPABILITY_CATALOG.items())
    ]


def normalize_tool_capabilities(
    capabilities: list[str] | set[str] | tuple[str, ...],
    *,
    tags: list[str] | None = None,
    category: str = "",
    adapter_name: str = "",
) -> list[str]:
    observed = {
        str(item or "").strip().lower()
        for item in list(capabilities or [])
        if str(item or "").strip()
    }
    tag_set = {
        str(item or "").strip().lower()
        for item in list(tags or [])
        if str(item or "").strip()
    }
    category_key = str(category or "").strip().lower()
    adapter_key = str(adapter_name or "").strip().lower()

    normalized: set[str] = set()
    for item in observed:
        mapped = _CAPABILITY_ALIAS_MAP.get(item)
        if mapped:
            normalized.add(mapped)
        if item in CANONICAL_TOOL_CAPABILITY_CATALOG:
            normalized.add(item)

    combined = observed | tag_set | {category_key, adapter_key}
    if {"browser", "playwright", "android", "qa"} & combined:
        normalized.update({"browser_nav", "browser_test"})
    if {"git", "repo", "readme", "documentation"} & combined:
        normalized.add("repo_read")
    if {"build", "release", "rendering", "video"} & combined:
        normalized.add("build_execute")
    if {"skill"} & combined:
        normalized.add("skill_run")
    if category_key == "mcp" or "mcp" in combined:
        normalized.add("external_mcp")
    if {"lsp", "symbols"} & combined:
        normalized.add("lsp_symbols")
    if {"references", "rename"} & combined:
        normalized.add("lsp_references")
    if {"test", "qa", "validation"} & combined:
        normalized.add("test_execute")
    if {"publish", "write", "release"} & combined:
        normalized.add("repo_write")

    return sorted(item for item in normalized if item in CANONICAL_TOOL_CAPABILITY_CATALOG)


def normalize_skill_targets(
    targets: list[str] | set[str] | tuple[str, ...] | None,
) -> list[str]:
    if not targets:
        return []
    return sorted(
        {
            str(item or "").strip().lower()
            for item in list(targets)
            if str(item or "").strip()
        }
    )


def normalize_lsp_targets(
    targets: list[str] | set[str] | tuple[str, ...] | None,
) -> list[str]:
    if not targets:
        return []
    normalized: set[str] = set()
    for item in list(targets):
        value = str(item or "").strip().lower()
        if not value:
            continue
        normalized.add(value)
        if value == "impact":
            normalized.update({"symbols", "references"})
    return sorted(normalized)


def derive_target_capabilities(
    *,
    skill_targets: list[str] | set[str] | tuple[str, ...] | None,
    lsp_targets: list[str] | set[str] | tuple[str, ...] | None,
) -> list[str]:
    derived: set[str] = set()
    if normalize_skill_targets(skill_targets):
        derived.add("skill_run")
    normalized_lsp = set(normalize_lsp_targets(lsp_targets))
    if {"symbols", "definitions", "diagnostics"} & normalized_lsp:
        derived.add("lsp_symbols")
    if {"references", "impact", "rename"} & normalized_lsp:
        derived.add("lsp_references")
    return sorted(derived)


def write_effective_inventory_snapshot(
    *,
    output_path: Path,
    project_root: Path,
    task_id: str,
    required_capabilities: list[str] | set[str] | tuple[str, ...],
    skill_targets: list[str] | set[str] | tuple[str, ...] | None,
    lsp_targets: list[str] | set[str] | tuple[str, ...] | None,
    selected_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = _load_json(output_path, default={"entries": []})
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        entries = []

    normalized_required = normalize_tool_capabilities(required_capabilities)
    normalized_required = sorted(
        {
            *normalized_required,
            *derive_target_capabilities(
                skill_targets=skill_targets,
                lsp_targets=lsp_targets,
            ),
        }
    )
    normalized_tools: list[dict[str, Any]] = []
    for item in selected_tools:
        if not isinstance(item, dict):
            continue
        tool_caps = normalize_tool_capabilities(
            item.get("capabilities", []),
            tags=item.get("tags", []),
            category=str(item.get("category", "") or ""),
            adapter_name=str(item.get("name", item.get("adapter_name", "")) or ""),
        )
        normalized_tools.append(
            {
                "name": str(item.get("name", item.get("adapter_name", "")) or "").strip().lower(),
                "category": str(item.get("category", "unknown") or "unknown").strip().lower(),
                "source": str(item.get("source", "") or "").strip(),
                "canonical_capabilities": tool_caps,
                "cost_tier": str(item.get("cost_tier", "unknown") or "unknown").strip().lower(),
                "latency_tier": str(item.get("latency_tier", "unknown") or "unknown").strip().lower(),
                "risk_tier": str(item.get("risk_tier", "unknown") or "unknown").strip().lower(),
                "environment_targets": [
                    str(env or "").strip().lower()
                    for env in list(item.get("environment_targets", []))
                    if str(env or "").strip()
                ],
            }
        )

    snapshot = {
        "task_id": str(task_id or "").strip(),
        "project_root": str(project_root.resolve()),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "required_capabilities": normalized_required,
        "skill_targets": normalize_skill_targets(skill_targets),
        "lsp_targets": normalize_lsp_targets(lsp_targets),
        "selected_tools": normalized_tools,
    }
    entries.append(snapshot)

    payload.update(
        {
            "schema_version": EFFECTIVE_TOOL_INVENTORY_SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(project_root.resolve()),
            "capability_catalog": canonical_tool_capabilities(),
            "entries": entries[-200:],
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return snapshot


def _build_record(project: Path) -> ToolRecord | None:
    name = project.name
    lower = name.lower()
    readme_text = _readme_text(project)
    corpus = f"{lower}\n{readme_text}".lower()

    if "whatsapp" in corpus:
        return ToolRecord(
            project_name=name,
            path=str(project.resolve()),
            tags=_tags(project, extra=["messaging", "sensitive"]),
            capabilities=["analysis", "knowledge_base", "messaging"],
            role_targets=["team_lead", "researcher"],
            priority="secondary",
            requires_approval=True,
            enabled=False,
            adapter_name="secretariawhatsapp",
            category="mcp",
            canonical_capabilities=normalize_tool_capabilities(
                ["analysis", "knowledge_base", "messaging"],
                tags=["messaging", "sensitive"],
                category="mcp",
                adapter_name="secretariawhatsapp",
            ),
            cost_tier="medium",
            latency_tier="medium",
            risk_tier="high",
            environment_targets=["dev", "stage"],
        )

    if "playstore" in corpus or "google play" in corpus:
        return ToolRecord(
            project_name=name,
            path=str(project.resolve()),
            tags=_tags(project, extra=["android", "release", "sensitive"]),
            capabilities=["release", "android", "automation"],
            role_targets=["engineer", "qa"],
            priority="secondary",
            requires_approval=True,
            enabled=False,
            adapter_name="playstore_publisher",
            category="cli",
            canonical_capabilities=normalize_tool_capabilities(
                ["release", "android", "automation"],
                tags=["android", "release", "sensitive"],
                category="cli",
                adapter_name="playstore_publisher",
            ),
            cost_tier="medium",
            latency_tier="high",
            risk_tier="high",
            environment_targets=["stage", "prod"],
        )

    if "androidweb" in corpus or "android web controller" in corpus:
        return ToolRecord(
            project_name=name,
            path=str(project.resolve()),
            tags=_tags(project, extra=["android", "qa", "browser"]),
            capabilities=["qa", "android", "browser_testing"],
            role_targets=["qa"],
            priority="secondary",
            requires_approval=False,
            enabled=True,
            adapter_name="android_browser_auditor",
            category="cli",
            canonical_capabilities=normalize_tool_capabilities(
                ["qa", "android", "browser_testing"],
                tags=["android", "qa", "browser"],
                category="cli",
                adapter_name="android_browser_auditor",
            ),
            cost_tier="low",
            latency_tier="medium",
            risk_tier="medium",
            environment_targets=["dev", "stage"],
        )

    if "remotion" in corpus or "video" in corpus or "veo" in corpus:
        return ToolRecord(
            project_name=name,
            path=str(project.resolve()),
            tags=_tags(project, extra=["video", "multimodal"]),
            capabilities=["multimodal", "video_generation", "rendering"],
            role_targets=["engineer", "researcher"],
            priority="secondary",
            requires_approval=False,
            enabled=False,
            adapter_name="video_editor_remotion",
            category="skill",
            canonical_capabilities=normalize_tool_capabilities(
                ["multimodal", "video_generation", "rendering"],
                tags=["video", "multimodal"],
                category="skill",
                adapter_name="video_editor_remotion",
            ),
            cost_tier="medium",
            latency_tier="high",
            risk_tier="low",
            environment_targets=["dev", "stage"],
        )

    return None


def _tags(project: Path, extra: list[str]) -> list[str]:
    tags = []
    if (project / "package.json").exists():
        tags.append("node")
    if (project / "requirements.txt").exists() or (project / "pyproject.toml").exists():
        tags.append("python")
    if list(project.glob("*.exe")):
        tags.append("exe")
    tags.extend(extra)
    unique = []
    for tag in tags:
        if tag not in unique:
            unique.append(tag)
    return unique


def _readme_text(project: Path) -> str:
    for file_name in ["README.md", "README.txt", "readme.md", "readme.txt"]:
        path = project / file_name
        if not path.exists():
            continue
        try:
            return path.read_text(encoding="utf-8", errors="ignore")[:4000]
        except OSError:
            return ""
    return ""


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)
    if not isinstance(payload, dict):
        return dict(default)
    return payload
