"""Catálogo MCP curado: descriptores revisados, nunca instaladores.

Los entries solo nombran ejecutables que el owner debe haber instalado por su
cuenta. Resolver un entry prepara una propuesta Lead; no escribe el registry,
no ejecuta el binario y no concede aprobación ni tools.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path
from typing import Any

CATALOG_VERSION = 1
_EXACT_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][a-zA-Z0-9.-]+)?$")
_FORBIDDEN_SOURCES = frozenset({
    "npx", "npm", "pnpm", "yarn", "bunx", "docker", "cmd", "powershell", "pwsh", "sh", "bash",
})
_FORBIDDEN_ARG_FRAGMENTS = ("latest", "@latest", "-y", "|", "&&", ";")

_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "github-readonly",
        "name": "github-readonly",
        "display_name": "GitHub MCP Server (solo lectura)",
        "description": "Repositorios, issues y pull requests mediante el servidor oficial de GitHub.",
        "publisher": "GitHub",
        "homepage": "https://github.com/github/github-mcp-server",
        "source": "github-mcp-server",
        "required_local_command": "github-mcp-server",
        "version": "1.6.0",
        "distribution_version": "1.6.0",
        "args": ["stdio", "--read-only", "--toolsets", "context,repos,issues,pull_requests"],
        "env_required": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        "applies_to_roles": ["lead", "reviewer"],
        "capabilities": ["github_context", "repository_read", "issues_read", "pull_requests_read"],
        "risk": "Accede a datos GitHub permitidos por el token; usar scopes mínimos.",
        "reviewed_at": "2026-07-20",
    },
    {
        "id": "playwright-browser",
        "name": "playwright-browser",
        "display_name": "Playwright MCP",
        "description": "Automatización e inspección accesible de navegador con el servidor oficial de Microsoft.",
        "publisher": "Microsoft",
        "homepage": "https://github.com/microsoft/playwright-mcp",
        "source": "node",
        "required_local_command": "playwright-mcp",
        "package_entry": "node_modules/@playwright/mcp/cli.js",
        "version": "0.0.78",
        "distribution_version": "0.0.78",
        "args": ["{package_entry}", "--headless", "--isolated"],
        "env_required": [],
        "applies_to_roles": ["engineer", "reviewer"],
        "capabilities": ["browser_automation", "accessibility_snapshot", "web_verification"],
        "risk": "Puede navegar y actuar sobre sitios; revisar cada tool y evitar sesiones con credenciales sensibles.",
        "reviewed_at": "2026-07-20",
    },
    {
        "id": "filesystem-workspace",
        "name": "filesystem-workspace",
        "display_name": "Filesystem MCP (workspace limitado)",
        "description": "Operaciones de archivos limitadas al workspace mediante el servidor de referencia MCP.",
        "publisher": "Model Context Protocol / LF Projects",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        "source": "node",
        "required_local_command": "mcp-server-filesystem",
        "package_entry": "node_modules/@modelcontextprotocol/server-filesystem/dist/index.js",
        # La distribución 2026.7.10 declara actualmente 0.2.0 en serverInfo.
        "version": "0.2.0",
        "distribution_version": "2026.7.10",
        "args": ["{package_entry}", "{workspace}"],
        "env_required": [],
        "applies_to_roles": ["engineer", "reviewer"],
        "capabilities": ["workspace_read", "workspace_write", "file_search"],
        "risk": "Incluye tools destructivas; el path queda confinado al workspace y la allowlist empieza vacía.",
        "reviewed_at": "2026-07-20",
    },
)


def _validate_entry(entry: dict[str, Any]) -> None:
    required = {
        "id", "name", "display_name", "description", "publisher", "homepage",
        "source", "required_local_command", "version", "distribution_version", "args", "env_required",
        "applies_to_roles", "capabilities", "risk", "reviewed_at",
    }
    missing = sorted(required - set(entry))
    if missing:
        raise ValueError(f"catalog entry missing fields: {', '.join(missing)}")
    source = str(entry["source"]).strip()
    if not source or source.lower() in _FORBIDDEN_SOURCES or any(char.isspace() for char in source):
        raise ValueError(f"unsafe catalog source: {source!r}")
    for field in ("version", "distribution_version"):
        if not _EXACT_VERSION_RE.fullmatch(str(entry[field]).strip()):
            raise ValueError(f"catalog {field} must be exact: {entry[field]!r}")
    if not str(entry["homepage"]).startswith("https://"):
        raise ValueError("catalog homepage must use https")
    if not isinstance(entry["args"], list) or not all(isinstance(item, str) for item in entry["args"]):
        raise ValueError("catalog args must be a string list")
    if "{package_entry}" in entry["args"] and not str(entry.get("package_entry") or "").strip():
        raise ValueError("catalog package entry placeholder needs package_entry")
    for arg in entry["args"]:
        lowered = arg.lower()
        if any(fragment in lowered for fragment in _FORBIDDEN_ARG_FRAGMENTS):
            raise ValueError(f"unsafe catalog argument: {arg!r}")


for _entry in _CATALOG:
    _validate_entry(_entry)
if len({entry["id"] for entry in _CATALOG}) != len(_CATALOG):
    raise ValueError("duplicate MCP catalog id")


def list_mcp_catalog() -> list[dict[str, Any]]:
    return copy.deepcopy(list(_CATALOG))


def get_mcp_catalog_entry(catalog_id: str) -> dict[str, Any]:
    normalized = str(catalog_id or "").strip().lower()
    for entry in _CATALOG:
        if entry["id"] == normalized:
            return copy.deepcopy(entry)
    raise LookupError(f"MCP catalog entry not found: {normalized or '<empty>'}")


def resolve_catalog_proposal(
    payload: dict[str, Any], *, workspace_root: Path
) -> dict[str, Any]:
    """Expand a catalog_id and reject any attempted contract substitution."""
    catalog_id = str(payload.get("catalog_id") or "").strip().lower()
    entry = get_mcp_catalog_entry(catalog_id)
    package_entry = _resolve_package_entry(entry) if entry.get("package_entry") else ""
    canonical = {
        "name": entry["name"],
        "source": entry["source"],
        "version": entry["version"],
        "args": [
            str(workspace_root.resolve()) if item == "{workspace}"
            else package_entry if item == "{package_entry}"
            else item
            for item in entry["args"]
        ],
        "env_required": entry["env_required"],
        "applies_to_roles": entry["applies_to_roles"],
    }
    for field, expected in canonical.items():
        supplied = payload.get(field)
        if supplied not in (None, "", []) and supplied != expected:
            raise ValueError(f"catalog proposal cannot override {field}")
    return {
        **payload,
        **canonical,
        "catalog_id": catalog_id,
        "catalog_artifact_version": entry["distribution_version"],
        "catalog_reviewed_at": entry["reviewed_at"],
        "catalog_homepage": entry["homepage"],
    }


def _resolve_package_entry(entry: dict[str, Any]) -> str:
    """Resolve an installed npm shim to its real, version-checked JS entrypoint."""
    command = str(entry.get("required_local_command") or "")
    shim = shutil.which(command)
    if not shim:
        raise LookupError(f"catalog executable is not installed: {command}")
    shim_dir = Path(shim).resolve().parent
    relative = Path(str(entry["package_entry"]))
    package_entry = (shim_dir / relative).resolve()
    if not package_entry.is_file():
        raise LookupError(f"catalog package entry not found for {command}: {package_entry}")
    package_parts = relative.parts[:3] if len(relative.parts) > 2 and relative.parts[1].startswith("@") else relative.parts[:2]
    package_root = (shim_dir.joinpath(*package_parts)).resolve()
    package_json = package_root / "package.json"
    try:
        installed_version = str(json.loads(package_json.read_text(encoding="utf-8")).get("version") or "")
    except (OSError, json.JSONDecodeError) as exc:
        raise LookupError(f"catalog package metadata is unreadable: {package_json}") from exc
    expected = str(entry["distribution_version"])
    if installed_version != expected:
        raise ValueError(
            f"catalog package version mismatch for {command}: expected {expected}, got {installed_version or '<empty>'}"
        )
    return str(package_entry)
