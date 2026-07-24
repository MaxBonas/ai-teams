from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PUSH_WORKFLOWS = (
    "frontend-quality.yml",
    "polyglot-fixtures.yml",
    "windows-clean-room.yml",
)


def test_push_workflows_include_the_repository_default_branch() -> None:
    for name in PUSH_WORKFLOWS:
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        )
        triggers = workflow.get("on") or workflow.get(True)
        branches = triggers["push"]["branches"]
        assert "master" in branches, f"{name} no escucha la rama por defecto master"
