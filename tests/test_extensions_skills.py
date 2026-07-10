"""Project-scoped skills (self-extension PR 1): the owner attaches local
knowledge to one project; it composes ONTO the base role skill (never
replaces it) and only reaches the roles it declares."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiteam.extensions import (
    delete_project_skill,
    list_project_skills,
    project_skills_for_role,
    set_project_skill_status,
    slugify_skill_name,
    upsert_project_skill,
)
from aiteam.skills import compose_skill, load_skill


def _runtime(tmp_path: Path) -> Path:
    d = tmp_path / ".aiteam"
    d.mkdir()
    return d


def test_upsert_creates_file_and_registry_entry(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    entry = upsert_project_skill(
        rt, name="Unity Scene Regeneration", body="Use Tools > Create Test Scene.",
        applies_to_roles=["engineer", "reviewer"],
    )
    assert entry["name"] == "unity-scene-regeneration"
    assert (rt / "skills" / "unity-scene-regeneration.md").read_text(encoding="utf-8").startswith("Use Tools")
    registry = json.loads((rt / "extensions.json").read_text(encoding="utf-8"))
    assert registry["skills"]["unity-scene-regeneration"]["applies_to_roles"] == ["engineer", "reviewer"]
    assert registry["version"] == 1


def test_role_filter_only_matching_roles(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    upsert_project_skill(rt, name="eng-only", body="engineer knowledge", applies_to_roles=["engineer"])

    assert [s["name"] for s in project_skills_for_role(rt, "engineer")] == ["eng-only"]
    assert project_skills_for_role(rt, "reviewer") == []
    # software_engineer normalizes distinctly; declared role must match as given
    assert project_skills_for_role(rt, "reviewer") == []


def test_empty_applies_to_roles_matches_all(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    upsert_project_skill(rt, name="global", body="applies to everyone", applies_to_roles=[])
    assert [s["name"] for s in project_skills_for_role(rt, "engineer")] == ["global"]
    assert [s["name"] for s in project_skills_for_role(rt, "lead")] == ["global"]


def test_retired_skill_not_injected(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    upsert_project_skill(rt, name="temp", body="x", applies_to_roles=["engineer"])
    set_project_skill_status(rt, name="temp", status="retired")
    assert project_skills_for_role(rt, "engineer") == []


def test_compose_appends_project_skill_after_base(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    upsert_project_skill(rt, name="local", body="LOCAL-MARKER-123", applies_to_roles=["engineer"])

    composed = compose_skill("engineer", rt)
    base = load_skill("engineer")

    assert base is not None
    assert composed is not None
    assert composed.startswith(base)  # base leads
    assert "LOCAL-MARKER-123" in composed
    assert composed.index("LOCAL-MARKER-123") > composed.index(base[:40])  # project after


def test_compose_without_project_skills_is_plain_base(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    assert compose_skill("engineer", rt) == load_skill("engineer")


def test_compose_none_runtime_is_base(tmp_path: Path) -> None:
    assert compose_skill("engineer", None) == load_skill("engineer")


def test_delete_removes_file_and_entry(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    upsert_project_skill(rt, name="doomed", body="x", applies_to_roles=["engineer"])
    assert delete_project_skill(rt, name="doomed") is True
    assert not (rt / "skills" / "doomed.md").exists()
    assert list_project_skills(rt) == []
    assert delete_project_skill(rt, name="doomed") is False  # already gone


def test_empty_body_rejected(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    with pytest.raises(ValueError):
        upsert_project_skill(rt, name="x", body="   ", applies_to_roles=["engineer"])


def test_oversized_body_rejected(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    with pytest.raises(ValueError):
        upsert_project_skill(rt, name="x", body="A" * 25_000, applies_to_roles=["engineer"])


def test_slug_prevents_path_traversal(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    entry = upsert_project_skill(rt, name="../../etc/passwd", body="x", applies_to_roles=[])
    slug = entry["name"]
    assert "/" not in slug and ".." not in slug
    assert (rt / "skills" / f"{slug}.md").exists()
    # nothing was written outside the skills dir
    assert not (tmp_path / "etc").exists()


def test_orphaned_registry_entry_skipped(tmp_path: Path) -> None:
    """A registry entry whose .md file was manually deleted must not crash
    injection — the skill is simply skipped."""
    rt = _runtime(tmp_path)
    upsert_project_skill(rt, name="ghost", body="x", applies_to_roles=["engineer"])
    (rt / "skills" / "ghost.md").unlink()
    assert project_skills_for_role(rt, "engineer") == []


def test_slugify_examples() -> None:
    assert slugify_skill_name("Unity Scene Regen!") == "unity-scene-regen"
    assert slugify_skill_name("") == "skill"
