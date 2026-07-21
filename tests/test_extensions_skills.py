"""Project-scoped skills (self-extension PR 1): the owner attaches local
knowledge to one project; it composes ONTO the base role skill (never
replaces it) and only reaches the roles it declares."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiteam.extensions import (
    MAX_ACTIVE_SKILL_BYTES,
    MAX_LEARNED_SKILLS,
    delete_project_skill,
    list_project_skills,
    project_skills_for_role,
    propose_learned_skill,
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


def test_learned_skill_is_evidence_backed_and_inert_until_owner_approval(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    proposed = propose_learned_skill(
        rt,
        name="pytest on windows",
        body="Usa el launcher local del proyecto.",
        applies_to_roles=["engineer"],
        evidence=["run-1 falló con python global", "run-2 pasó con scripts/pytest_local.bat"],
        source_run_id="run-2",
    )

    assert proposed["origin"] == "learned"
    assert proposed["status"] == "proposed"
    assert project_skills_for_role(rt, "engineer") == []

    activated = set_project_skill_status(rt, name=proposed["name"], status="active")
    assert activated is not None
    assert activated["approved_by"] == "user"
    assert [item["name"] for item in project_skills_for_role(rt, "engineer")] == [proposed["name"]]


def test_learned_proposal_requires_evidence_and_has_quantity_limit(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    with pytest.raises(ValueError, match="evidence"):
        propose_learned_skill(
            rt, name="guess", body="Do X", applies_to_roles=[], evidence=[], source_run_id="run-1"
        )

    for index in range(MAX_LEARNED_SKILLS):
        propose_learned_skill(
            rt,
            name=f"learned-{index}",
            body=f"Observed rule {index}",
            applies_to_roles=["engineer"],
            evidence=[f"run-{index}"],
            source_run_id=f"run-{index}",
        )
    with pytest.raises(ValueError, match="learned skill limit"):
        propose_learned_skill(
            rt,
            name="one-too-many",
            body="Observed rule",
            applies_to_roles=["engineer"],
            evidence=["run-final"],
            source_run_id="run-final",
        )


def test_active_prompt_budget_is_enforced(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    half = MAX_ACTIVE_SKILL_BYTES // 2
    upsert_project_skill(rt, name="first", body="A" * half)
    upsert_project_skill(rt, name="second", body="B" * half)
    with pytest.raises(ValueError, match="prompt budget"):
        upsert_project_skill(rt, name="overflow", body="C")


def test_owner_edit_preserves_learned_provenance(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    propose_learned_skill(
        rt,
        name="local-rule",
        body="Original",
        applies_to_roles=["engineer"],
        evidence=["run-1"],
        source_run_id="run-1",
    )
    edited = upsert_project_skill(
        rt,
        name="local-rule",
        body="Corregida por el owner",
        applies_to_roles=["reviewer"],
        origin="owner",
        status="proposed",
        approved_by="user",
    )
    assert edited["origin"] == "learned"
    assert edited["edited_by_owner_at"]
    assert edited["evidence"] == ["run-1"]
