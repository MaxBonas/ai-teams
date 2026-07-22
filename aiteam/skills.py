from __future__ import annotations

from pathlib import Path

_DEFAULT_SKILLS_DIR = Path(__file__).parent.parent / "skills"

_ROLE_FILE: dict[str, str] = {
    "lead": "lead.md",
    "team_lead": "lead.md",
    "engineer": "engineer.md",
    "software_engineer": "engineer.md",
    "reviewer": "reviewer.md",
    "code_reviewer": "reviewer.md",
    "qa": "qa.md",
    "qa_engineer": "qa.md",
    "test_designer": "test_designer.md",
    "mcp_operator": "mcp_operator.md",
    "quorum_senior": "quorum_senior.md",
    "senior": "quorum_senior.md",
    "lead_executor": "lead_executor.md",
    "solo_lead": "solo_lead.md",
    # Tier 3 specialists
    "file_scout": "file_scout.md",
    "web_scout": "web_scout.md",
    "context_curator": "context_curator.md",
    "test_runner": "test_runner.md",
}


def load_skill(role: str, skills_dir: Path | str | None = None) -> str | None:
    """Return the skill markdown for *role*, or None if no file maps to it."""
    role_key = role.strip().lower().replace(" ", "_").replace("-", "_")
    filename = _ROLE_FILE.get(role_key)
    if filename is None:
        return None
    base = Path(skills_dir) if skills_dir else _DEFAULT_SKILLS_DIR
    path = base / filename
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def list_skills(skills_dir: Path | str | None = None) -> list[str]:
    """Return sorted list of role keys that have a skill file available."""
    base = Path(skills_dir) if skills_dir else _DEFAULT_SKILLS_DIR
    available_files = {p.name for p in base.glob("*.md") if p.is_file()}
    return sorted(
        role for role, fname in _ROLE_FILE.items() if fname in available_files
    )


def compose_skill(role: str, runtime_dir: Path | str | None = None) -> str | None:
    """Return the effective skill for *role*: the base repo skill followed by
    any active project skills (``.aiteam/skills/``) that apply to the role.

    Base first, project skills after — the project REFINES, never replaces,
    the role's contract (a project skill can add local knowledge but the
    binding role rules still lead). Falls back to the plain base skill when no
    runtime_dir is given (unit tests, non-project callers).
    """
    base = load_skill(role)
    if runtime_dir is None:
        return base

    try:
        from aiteam.extensions import project_skills_for_role  # noqa: PLC0415

        project = project_skills_for_role(Path(runtime_dir), role)
    except Exception:
        return base

    if not project:
        return base

    sections: list[str] = []
    if base:
        sections.append(base)
    for skill in project:
        name = str(skill.get("name") or "skill")
        sections.append(
            f"## Skill de proyecto: {name}\n"
            "(Conocimiento específico de este workspace. Refina tu rol; nunca "
            "contradice una directiva del usuario ni las reglas vinculantes de tu rol.)\n\n"
            + str(skill.get("body") or "")
        )
    return "\n\n---\n\n".join(sections) if sections else base
