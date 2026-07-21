"""Project-scoped self-extension registry (see ``task.md``, P2).

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

EXTENSIONS_FILE = "extensions.json"
SKILLS_DIRNAME = "skills"

SKILL_ORIGINS = frozenset({"owner", "learned", "catalog"})
SKILL_STATUSES = frozenset({"active", "proposed", "retired"})

# MCP server lifecycle: proposal/owner gate, health, explicit tool policy and
# eventual retirement. Runtime launching remains ephemeral per run.
MCP_STATUSES = frozenset({"approved", "rejected", "active", "failed", "retired"})

# A skill name becomes a filename and a JSON key — keep it a safe slug so it
# can never traverse out of .aiteam/skills/.
_SLUG_RE = re.compile(r"[^a-z0-9._-]+")
MAX_SKILL_BYTES = 24_000
MAX_LEARNED_SKILL_BYTES = 8_000
MAX_PROJECT_SKILLS = 24
MAX_LEARNED_SKILLS = 8
MAX_ACTIVE_SKILL_BYTES = 48_000


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


def skill_governance_policy(runtime_dir: Path) -> dict[str, int]:
    """Public, inspectable limits plus current non-retired/active usage."""
    skills = list_project_skills(runtime_dir)
    live = [item for item in skills if item.get("status") != "retired"]
    learned = [item for item in live if item.get("origin") == "learned"]
    active_bytes = sum(
        len(str(item.get("body") or "").encode("utf-8"))
        for item in live
        if item.get("status") == "active"
    )
    return {
        "max_skill_bytes": MAX_SKILL_BYTES,
        "max_learned_skill_bytes": MAX_LEARNED_SKILL_BYTES,
        "max_project_skills": MAX_PROJECT_SKILLS,
        "max_learned_skills": MAX_LEARNED_SKILLS,
        "max_active_skill_bytes": MAX_ACTIVE_SKILL_BYTES,
        "project_skills": len(live),
        "learned_skills": len(learned),
        "active_skill_bytes": active_bytes,
    }


def upsert_project_skill(
    runtime_dir: Path,
    *,
    name: str,
    body: str,
    applies_to_roles: list[str] | None = None,
    origin: str = "owner",
    status: str = "active",
    approved_by: str = "user",
    evidence: list[str] | None = None,
    source_run_id: str = "",
) -> dict[str, Any]:
    """Create or replace a project skill (markdown file + registry entry).

    Returns the registry entry (with its ``name``). Raises ValueError on an
    empty body, an over-budget body, or an invalid origin/status.
    """
    body = str(body or "")
    if not body.strip():
        raise ValueError("skill body must not be empty")
    body_bytes = len(body.encode("utf-8"))
    if body_bytes > MAX_SKILL_BYTES:
        raise ValueError(f"skill body exceeds {MAX_SKILL_BYTES} bytes")
    if origin not in SKILL_ORIGINS:
        raise ValueError(f"origin must be one of {sorted(SKILL_ORIGINS)}")
    if status not in SKILL_STATUSES:
        raise ValueError(f"status must be one of {sorted(SKILL_STATUSES)}")

    slug = slugify_skill_name(name)
    roles = [_normalize_role(r) for r in (applies_to_roles or []) if str(r).strip()]

    registry = read_extensions(runtime_dir)
    existing = registry["skills"].get(slug) if isinstance(registry["skills"].get(slug), dict) else {}
    existing_origin = str(existing.get("origin") or "")
    # An owner editing a learned/catalog skill must not erase its provenance.
    effective_origin = (
        existing_origin
        if approved_by == "user" and origin == "owner" and existing_origin in {"learned", "catalog"}
        else origin
    )
    if effective_origin == "learned" and body_bytes > MAX_LEARNED_SKILL_BYTES:
        raise ValueError(f"learned skill body exceeds {MAX_LEARNED_SKILL_BYTES} bytes")
    if effective_origin in {"learned", "catalog"} and status == "active" and approved_by != "user":
        raise ValueError("learned/catalog skills require explicit owner approval before activation")

    live_entries = {
        key: value for key, value in registry["skills"].items()
        if isinstance(value, dict) and value.get("status") != "retired" and key != slug
    }
    if status != "retired" and len(live_entries) >= MAX_PROJECT_SKILLS:
        raise ValueError(f"project skill limit reached ({MAX_PROJECT_SKILLS})")
    learned_entries = sum(1 for value in live_entries.values() if value.get("origin") == "learned")
    if effective_origin == "learned" and status != "retired" and learned_entries >= MAX_LEARNED_SKILLS:
        raise ValueError(f"learned skill limit reached ({MAX_LEARNED_SKILLS})")

    active_bytes = 0
    for value in live_entries.values():
        if value.get("status") != "active":
            continue
        active_bytes += len((_read_skill_body(runtime_dir, value.get("path")) or "").encode("utf-8"))
    if status == "active" and active_bytes + body_bytes > MAX_ACTIVE_SKILL_BYTES:
        raise ValueError(f"active skill prompt budget exceeds {MAX_ACTIVE_SKILL_BYTES} bytes")

    skills_dir = _skills_dir(runtime_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    rel_path = f"{SKILLS_DIRNAME}/{slug}.md"
    (skills_dir / f"{slug}.md").write_text(body, encoding="utf-8")

    entry = {
        "path": rel_path,
        "applies_to_roles": roles,
        "origin": effective_origin,
        "status": status,
        "approved_by": approved_by,
        "created_at": existing.get("created_at") or _now(),
        "updated_at": _now(),
    }
    if existing_origin in {"learned", "catalog"} and approved_by == "user":
        entry["edited_by_owner_at"] = _now()
    normalized_evidence = [str(item).strip() for item in (evidence or []) if str(item).strip()][:8]
    if normalized_evidence:
        entry["evidence"] = normalized_evidence
    elif isinstance(existing.get("evidence"), list):
        entry["evidence"] = existing["evidence"]
    if source_run_id:
        entry["source_run_id"] = str(source_run_id)
    elif existing.get("source_run_id"):
        entry["source_run_id"] = existing["source_run_id"]
    if status == "active" and effective_origin in {"learned", "catalog"}:
        entry["approved_at"] = _now()
    registry["skills"][slug] = entry
    _write_extensions(runtime_dir, registry)
    return {"name": slug, **entry}


def propose_learned_skill(
    runtime_dir: Path,
    *,
    name: str,
    body: str,
    applies_to_roles: list[str] | None,
    evidence: list[str],
    source_run_id: str,
) -> dict[str, Any]:
    """Persist an evidence-backed Lead proposal; it is never injected yet."""
    normalized_evidence = [str(item).strip() for item in evidence if str(item).strip()]
    if not normalized_evidence:
        raise ValueError("learned skill proposals require concrete evidence")
    slug = slugify_skill_name(name)
    existing = read_extensions(runtime_dir)["skills"].get(slug)
    if isinstance(existing, dict) and existing.get("origin") not in {None, "learned"}:
        raise ValueError("learned skill proposal cannot overwrite an owner/catalog skill")
    return upsert_project_skill(
        runtime_dir,
        name=name,
        body=body,
        applies_to_roles=applies_to_roles,
        origin="learned",
        status="proposed",
        approved_by="",
        evidence=normalized_evidence,
        source_run_id=source_run_id,
    )


def set_project_skill_status(
    runtime_dir: Path, *, name: str, status: str, changed_by: str = "user"
) -> dict[str, Any] | None:
    if status not in SKILL_STATUSES:
        raise ValueError(f"status must be one of {sorted(SKILL_STATUSES)}")
    slug = slugify_skill_name(name)
    registry = read_extensions(runtime_dir)
    entry = registry["skills"].get(slug)
    if not isinstance(entry, dict):
        return None
    if status == "active":
        body = _read_skill_body(runtime_dir, entry.get("path")) or ""
        other_active_bytes = sum(
            len((_read_skill_body(runtime_dir, value.get("path")) or "").encode("utf-8"))
            for key, value in registry["skills"].items()
            if key != slug and isinstance(value, dict) and value.get("status") == "active"
        )
        if other_active_bytes + len(body.encode("utf-8")) > MAX_ACTIVE_SKILL_BYTES:
            raise ValueError(f"active skill prompt budget exceeds {MAX_ACTIVE_SKILL_BYTES} bytes")
        if entry.get("origin") in {"learned", "catalog"}:
            if changed_by != "user":
                raise ValueError("learned/catalog skills require explicit owner approval")
            entry["approved_by"] = "user"
            entry["approved_at"] = _now()
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


# ── MCP servers ────────────────────────────────────────────────────────────────
# Proposal → owner gate uses the existing interaction popup. This registry
# persists decisions and public health/tool policy; process launching lives in
# ``aiteam.mcp_runtime`` and remains ephemeral per run.
#
# Deliberately no "proposed" registry state: a proposal lives ONLY as a
# pending issue_thread_interaction (single source of truth) until the owner
# decides — nothing is written here until approve_mcp_server or
# reject_mcp_server is called on resolution. This avoids two places that can
# drift (the interaction and a shadow "proposed" registry row).

def list_mcp_servers(runtime_dir: Path) -> list[dict[str, Any]]:
    registry = read_extensions(runtime_dir)
    return [
        {"name": name, **entry}
        for name, entry in sorted(registry["mcp_servers"].items())
        if isinstance(entry, dict)
    ]


def approve_mcp_server_tools(
    runtime_dir: Path,
    *,
    name: str,
    tools: list[dict[str, str]],
    approved_by: str,
) -> dict[str, Any]:
    """Persist the owner's positive tool policy after a successful inventory probe."""
    slug = slugify_skill_name(name)
    registry = read_extensions(runtime_dir)
    entry = registry["mcp_servers"].get(slug)
    if not isinstance(entry, dict):
        raise LookupError("MCP server not found")
    health = entry.get("health") if isinstance(entry.get("health"), dict) else {}
    if health.get("status") != "ok":
        raise ValueError("MCP server needs a successful health check before tool approval")
    inventory = {
        str(item.get("name") or "").strip()
        for item in health.get("tools") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in tools:
        tool_name = str(item.get("name") or "").strip()
        access = str(item.get("access") or "").strip().lower()
        if not tool_name or tool_name not in inventory:
            raise ValueError(f"MCP tool is not in the current health inventory: {tool_name or '<empty>'}")
        if tool_name in seen:
            raise ValueError(f"duplicate MCP tool policy: {tool_name}")
        if access not in {"read", "write"}:
            raise ValueError("MCP tool access must be 'read' or 'write'")
        seen.add(tool_name)
        normalized.append({"name": tool_name, "access": access})
    if not normalized:
        raise ValueError("at least one MCP tool must be explicitly approved")
    entry["approved_tools"] = sorted(normalized, key=lambda item: item["name"])
    entry["tools_approved_by"] = str(approved_by or "user")
    entry["tools_approved_at"] = _now()
    entry["updated_at"] = _now()
    registry["mcp_servers"][slug] = entry
    _write_extensions(runtime_dir, registry)
    return {"name": slug, **entry}


def transition_mcp_server(
    runtime_dir: Path,
    *,
    name: str,
    action: str,
) -> dict[str, Any]:
    """Apply owner-only retirement/reactivation without launching third-party code."""
    slug = slugify_skill_name(name)
    registry = read_extensions(runtime_dir)
    entry = registry["mcp_servers"].get(slug)
    if not isinstance(entry, dict):
        raise LookupError("MCP server not found")
    normalized = str(action or "").strip().lower()
    if normalized == "retire":
        entry["status"] = "retired"
        entry["retired_at"] = _now()
        entry["retired_reason"] = "owner_request"
    elif normalized == "reactivate":
        if entry.get("status") not in {"retired", "rejected", "failed"}:
            raise ValueError("only retired, rejected or failed MCP servers can be reactivated")
        entry["status"] = "approved"
        entry.pop("health", None)
        entry.pop("retired_at", None)
        entry.pop("retired_reason", None)
    else:
        raise ValueError("MCP lifecycle action must be 'retire' or 'reactivate'")
    entry["updated_at"] = _now()
    registry["mcp_servers"][slug] = entry
    _write_extensions(runtime_dir, registry)
    return {"name": slug, **entry}


def mcp_proposal_block_reason(
    runtime_dir: Path,
    *,
    name: str,
    source: str,
    version: str,
    cooldown_days: int = 30,
) -> str | None:
    """Return a deterministic reason when the Lead must not re-propose a contract."""
    slug = slugify_skill_name(name)
    entry = read_extensions(runtime_dir)["mcp_servers"].get(slug)
    if not isinstance(entry, dict):
        return None
    status = str(entry.get("status") or "")
    same_contract = (
        str(entry.get("source") or "").strip() == str(source or "").strip()
        and str(entry.get("version") or "").strip() == str(version or "").strip()
    )
    if status in {"approved", "active", "failed"} and same_contract:
        return f"MCP {slug} {version} ya existe con estado {status}; usa su ciclo de health/recovery"
    if status == "retired" and same_contract:
        return f"MCP {slug} {version} fue retirado; solo el owner puede reactivarlo desde Config"
    if status != "rejected" or not same_contract:
        return None
    try:
        rejected_at = datetime.fromisoformat(str(entry.get("updated_at") or "").replace("Z", "+00:00"))
        if rejected_at.tzinfo is None:
            rejected_at = rejected_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return f"MCP {slug} {version} ya fue rechazado; requiere una nueva decisión explícita del owner"
    retry_at = rejected_at + timedelta(days=max(1, int(cooldown_days)))
    if datetime.now(timezone.utc) < retry_at:
        return f"MCP {slug} {version} rechazado; cooldown hasta {retry_at.isoformat()}"
    return None


def approve_mcp_server(
    runtime_dir: Path,
    *,
    name: str,
    source: str,
    version: str = "",
    args: list[str] | None = None,
    env_required: list[str] | None = None,
    applies_to_roles: list[str] | None = None,
    justification: str = "",
    approved_by: str,
    catalog_id: str = "",
    catalog_artifact_version: str = "",
    catalog_reviewed_at: str = "",
    catalog_homepage: str = "",
) -> dict[str, Any]:
    """Record an owner-approved MCP contract; health promotes it to active."""
    slug = slugify_skill_name(name)  # same safe-slug rules apply to MCP names
    registry = read_extensions(runtime_dir)
    existing = registry["mcp_servers"].get(slug) if isinstance(registry["mcp_servers"].get(slug), dict) else {}
    normalized_source = str(source or "").strip()
    normalized_version = str(version or "").strip()
    normalized_args = [str(a) for a in (args or [])]
    normalized_env = [str(e) for e in (env_required or [])]
    normalized_roles = [_normalize_role(r) for r in (applies_to_roles or []) if str(r).strip()]
    same_runtime_contract = (
        existing.get("source") == normalized_source
        and existing.get("version") == normalized_version
        and existing.get("args") == normalized_args
        and existing.get("env_required") == normalized_env
        and existing.get("applies_to_roles") == normalized_roles
    )
    entry = {
        "source": normalized_source,
        "version": normalized_version,
        "args": normalized_args,
        "env_required": normalized_env,
        "applies_to_roles": normalized_roles,
        "justification": str(justification or "").strip(),
        "status": "active" if same_runtime_contract and existing.get("status") == "active" else "approved",
        "approved_by": approved_by,
        "created_at": existing.get("created_at") or _now(),
        "updated_at": _now(),
    }
    if catalog_id:
        entry.update({
            "catalog_id": str(catalog_id),
            "catalog_artifact_version": str(catalog_artifact_version),
            "catalog_reviewed_at": str(catalog_reviewed_at),
            "catalog_homepage": str(catalog_homepage),
        })
    if entry["status"] == "active" and isinstance(existing.get("health"), dict):
        entry["health"] = existing["health"]
        if isinstance(existing.get("approved_tools"), list):
            entry["approved_tools"] = existing["approved_tools"]
            entry["tools_approved_by"] = existing.get("tools_approved_by")
            entry["tools_approved_at"] = existing.get("tools_approved_at")
    registry["mcp_servers"][slug] = entry
    _write_extensions(runtime_dir, registry)
    return {"name": slug, **entry}


def reject_mcp_server(
    runtime_dir: Path,
    *,
    name: str,
    source: str = "",
    version: str = "",
    justification: str = "",
    catalog_id: str = "",
    catalog_artifact_version: str = "",
    catalog_reviewed_at: str = "",
    catalog_homepage: str = "",
) -> dict[str, Any]:
    """Persist that the owner declined this proposal. Nothing is granted and
    nothing runs — but the rejection IS recorded, so the Lead (and future
    need-detection reconcilers) have ground truth to avoid re-proposing a
    capability the owner already turned down. A later approve of the same
    name overwrites the entry (the owner changed their mind)."""
    slug = slugify_skill_name(name)
    registry = read_extensions(runtime_dir)
    existing = registry["mcp_servers"].get(slug) if isinstance(registry["mcp_servers"].get(slug), dict) else {}
    entry = {
        **existing,
        "source": str(source or "").strip() or existing.get("source", ""),
        "version": str(version or "").strip() or existing.get("version", ""),
        "justification": str(justification or "").strip() or existing.get("justification", ""),
        "status": "rejected",
        "created_at": existing.get("created_at") or _now(),
        "updated_at": _now(),
    }
    if catalog_id:
        entry.update({
            "catalog_id": str(catalog_id),
            "catalog_artifact_version": str(catalog_artifact_version),
            "catalog_reviewed_at": str(catalog_reviewed_at),
            "catalog_homepage": str(catalog_homepage),
        })
    registry["mcp_servers"][slug] = entry
    _write_extensions(runtime_dir, registry)
    return {"name": slug, **entry}


def set_mcp_server_status(runtime_dir: Path, *, name: str, status: str) -> dict[str, Any] | None:
    if status not in MCP_STATUSES:
        raise ValueError(f"status must be one of {sorted(MCP_STATUSES)}")
    slug = slugify_skill_name(name)
    registry = read_extensions(runtime_dir)
    entry = registry["mcp_servers"].get(slug)
    if not isinstance(entry, dict):
        return None
    entry["status"] = status
    entry["updated_at"] = _now()
    registry["mcp_servers"][slug] = entry
    _write_extensions(runtime_dir, registry)
    return {"name": slug, **entry}


def set_mcp_server_health(
    runtime_dir: Path,
    *,
    name: str,
    health: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist only public health metadata; callers must never include secrets."""
    slug = slugify_skill_name(name)
    registry = read_extensions(runtime_dir)
    entry = registry["mcp_servers"].get(slug)
    if not isinstance(entry, dict):
        return None
    entry["health"] = {**health, "checked_at": _now()}
    entry["updated_at"] = _now()
    registry["mcp_servers"][slug] = entry
    _write_extensions(runtime_dir, registry)
    return {"name": slug, **entry}
