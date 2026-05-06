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
    "quorum_senior": "quorum_senior.md",
    "senior": "quorum_senior.md",
    # Tier 3 specialists
    "file_scout": "file_scout.md",
    "web_scout": "web_scout.md",
    "context_curator": "context_curator.md",
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
