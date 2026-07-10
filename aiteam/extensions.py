"""Project-scoped self-extension registry (DESIGN_SELF_EXTENSION.md).

PR 1 — SKILLS ONLY. This module owns ``.aiteam/extensions.json`` and the
``.aiteam/skills/`` directory: markdown skills the OWNER (or, later, the
system) attaches to a specific project so the team gains local knowledge
without a repo commit. MCP servers land here in a later PR — the ``version``
and top-level shape are chosen to grow into them without a migration.

Leaf module: stdlib + json only, no aiteam imports, so skills.py and the API
can both depend on it. Role matching mirrors ``aiteam.skills`` exactly.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXTENSIONS_FILE = "extensions.json"
SKILLS_DIRNAME = "skills"

SKILL_ORIGINS = frozenset({"owner", "learned", "catalog"})
SKILL_STATUSES = frozenset({"active", "proposed", "retired"})

# A skill name becomes a filename and a JSON key — keep it a safe slug so it
# can never traverse out of .aiteam/skills/.
_SLUG_RE = re.compile(r"[^a-z0-9._-]+")
_MAX_SKILL_BYTES = 24_000  # per-skill prompt budget guard


def _normalize_role(role: str) -> str:
    return str(role or "").strip().lower().replace(" ", "_").replace("-", "_")


def slugify_skill_name(name: str) -> str:
    slug = _SLUG_RE.sub("-", str(name or "").strip().lower()).strip("-._")
    return slug or "skill"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extensions_path(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / EXTENSIONS_FILE


def _skills_dir(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / SKILLS_DIRNAME


def read_extensions(runtime_dir: Path) -> dict[str, Any]:
    """Return the parsed registry, or an empty skeleton if absent/corrupt."""
    path = _extensions_path(runtime_dir)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "skills": {}, "mcp_servers": {}}
    if not isinstance(parsed, dict):
        return {"version": 1, "skills": {}, "mcp_servers": {}}
    parsed.setdefault("version", 1)
    parsed.setdefault("skills", {})
    parsed.setdefault("mcp_servers", {})
    if not isinstance(parsed["skills"], dict):
        parsed["skills"] = {}
    if not isinstance(parsed["mcp_servers"], dict):
        parsed["mcp_servers"] = {}
    return parsed


def _write_extensions(runtime_dir: Path, data: dict[str, Any]) -> None:
    path = _extensions_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


# ── Skills ────────────────────────────────────────────────────────────────────

def list_project_skills(runtime_dir: Path) -> list[dict[str, Any]]:
    """All registered project skills (any status), each with its ``name`` and
    (best-effort) markdown ``body`` for the Config editor."""
    registry = read_extensions(runtime_dir)
    out: list[dict[str, Any]] = []
    for name, entry in sorted(registry["skills"].items()):
        if not isinstance(entry, dict):
            continue
        item = {"name": name, **entry}
        body = _read_skill_body(runtime_dir, entry.get("path"))
        if body is not None:
            item["body"] = body
        out.append(item)
    return out


def project_skills_for_role(runtime_dir: Path, role: str) -> list[dict[str, Any]]:
    """Active project skills applying to *role*, in stable name order, each
    with its resolved markdown ``body``. Skills whose file is missing are
    skipped (the registry entry outlived its file)."""
    role_key = _normalize_role(role)
    registry = read_extensions(runtime_dir)
    out: list[dict[str, Any]] = []
    for name, entry in sorted(registry["skills"].items()):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("status") or "active") != "active":
            continue
        roles = {_normalize_role(r) for r in (entry.get("applies_to_roles") or [])}
        if roles and role_key not in roles:
            continue
        body = _read_skill_body(runtime_dir, entry.get("path"))
        if not body:
            continue
        out.append({"name": name, "body": body, **entry})
    return out


def _read_skill_body(runtime_dir: Path, rel_path: Any) -> str | None:
    rel = str(rel_path or "").strip()
    if not rel:
        return None
    # Confine to .aiteam/skills/ — never follow a path that escapes it.
    candidate = (Path(runtime_dir) / rel).resolve()
    skills_root = _skills_dir(runtime_dir).resolve()
    try:
        candidate.relative_to(skills_root)
    except ValueError:
        return None
    try:
        return candidate.read_text(encoding="utf-8")
    except Exception:
        return None


def upsert_project_skill(
    runtime_dir: Path,
    *,
    name: str,
    body: str,
    applies_to_roles: list[str] | None = None,
    origin: str = "owner",
    status: str = "active",
    approved_by: str = "user",
) -> dict[str, Any]:
    """Create or replace a project skill (markdown file + registry entry).

    Returns the registry entry (with its ``name``). Raises ValueError on an
    empty body, an over-budget body, or an invalid origin/status.
    """
    body = str(body or "")
    if not body.strip():
        raise ValueError("skill body must not be empty")
    if len(body.encode("utf-8")) > _MAX_SKILL_BYTES:
        raise ValueError(f"skill body exceeds {_MAX_SKILL_BYTES} bytes")
    if origin not in SKILL_ORIGINS:
        raise ValueError(f"origin must be one of {sorted(SKILL_ORIGINS)}")
    if status not in SKILL_STATUSES:
        raise ValueError(f"status must be one of {sorted(SKILL_STATUSES)}")

    slug = slugify_skill_name(name)
    roles = [_normalize_role(r) for r in (applies_to_roles or []) if str(r).strip()]

    skills_dir = _skills_dir(runtime_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    rel_path = f"{SKILLS_DIRNAME}/{slug}.md"
    (skills_dir / f"{slug}.md").write_text(body, encoding="utf-8")

    registry = read_extensions(runtime_dir)
    existing = registry["skills"].get(slug) if isinstance(registry["skills"].get(slug), dict) else {}
    entry = {
        "path": rel_path,
        "applies_to_roles": roles,
        "origin": origin,
        "status": status,
        "approved_by": approved_by,
        "created_at": existing.get("created_at") or _now(),
        "updated_at": _now(),
    }
    registry["skills"][slug] = entry
    _write_extensions(runtime_dir, registry)
    return {"name": slug, **entry}


def set_project_skill_status(runtime_dir: Path, *, name: str, status: str) -> dict[str, Any] | None:
    if status not in SKILL_STATUSES:
        raise ValueError(f"status must be one of {sorted(SKILL_STATUSES)}")
    slug = slugify_skill_name(name)
    registry = read_extensions(runtime_dir)
    entry = registry["skills"].get(slug)
    if not isinstance(entry, dict):
        return None
    entry["status"] = status
    entry["updated_at"] = _now()
    registry["skills"][slug] = entry
    _write_extensions(runtime_dir, registry)
    return {"name": slug, **entry}


def delete_project_skill(runtime_dir: Path, *, name: str) -> bool:
    """Remove the registry entry and its markdown file. Returns True if the
    entry existed."""
    slug = slugify_skill_name(name)
    registry = read_extensions(runtime_dir)
    entry = registry["skills"].pop(slug, None)
    if entry is None:
        return False
    _write_extensions(runtime_dir, registry)
    rel = str((entry or {}).get("path") or "").strip()
    if rel:
        candidate = (Path(runtime_dir) / rel).resolve()
        skills_root = _skills_dir(runtime_dir).resolve()
        try:
            candidate.relative_to(skills_root)
            candidate.unlink(missing_ok=True)
        except (ValueError, OSError):
            pass
    return True
