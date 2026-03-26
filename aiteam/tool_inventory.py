from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


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
        "root": str(root.resolve()),
        "total": len(records),
        "tools": [asdict(record) for record in records],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return payload


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
