"""
Tool capability catalog — canonical list of capabilities agents can hold.

Each capability is a named slot that allows an agent to use a family of tools.
Capabilities are stored in agents.capabilities_json as a list of strings.
The executor checks capabilities before recording tool_access decisions.
"""
from __future__ import annotations

import json
from typing import Any


# Canonical capability identifiers  ── order is display order in the UI
CAPABILITY_CATALOG: dict[str, dict[str, str]] = {
    "repo_read": {
        "description": "Lectura de repositorio y archivos",
        "tool_family": "repo",
        "label": "Repo R",
    },
    "repo_write": {
        "description": "Escritura y publicación de cambios en el repositorio",
        "tool_family": "repo",
        "label": "Repo W",
    },
    "lsp_symbols": {
        "description": "Navegación semántica: símbolos, definiciones y diagnósticos",
        "tool_family": "lsp",
        "label": "LSP sym",
    },
    "lsp_references": {
        "description": "Impacto de referencias, rename y dependencias semánticas",
        "tool_family": "lsp",
        "label": "LSP ref",
    },
    "test_execute": {
        "description": "Ejecución de suites, checks y validaciones automatizadas",
        "tool_family": "execution",
        "label": "Tests",
    },
    "build_execute": {
        "description": "Builds, renders, empaquetado o pipelines de entrega",
        "tool_family": "execution",
        "label": "Build",
    },
    "browser_nav": {
        "description": "Navegación browser, DOM, captura y reproducción de pasos",
        "tool_family": "browser",
        "label": "Browser",
    },
    "browser_test": {
        "description": "Tests o verificaciones automatizadas de navegador/UI",
        "tool_family": "browser",
        "label": "Browser test",
    },
    "external_mcp": {
        "description": "Uso de MCP server o integración externa estructurada",
        "tool_family": "mcp",
        "label": "MCP",
    },
    "skill_run": {
        "description": "Ejecución guiada por skill/playbook operativo",
        "tool_family": "skill",
        "label": "Skills",
    },
}

# Sensible defaults per role
DEFAULT_CAPABILITIES_BY_ROLE: dict[str, list[str]] = {
    "lead":             ["repo_read", "repo_write", "skill_run"],
    "engineer":         ["repo_read", "repo_write", "lsp_symbols", "lsp_references",
                         "test_execute", "build_execute"],
    "reviewer":         ["repo_read", "lsp_symbols", "lsp_references"],
    "qa":               ["repo_read", "repo_write", "browser_test", "test_execute"],
    "test_designer":    ["repo_read", "repo_write", "lsp_symbols", "lsp_references"],
    "mcp_operator":     ["repo_read", "external_mcp", "skill_run"],
    # Tier 3 specialists — explicit entries so substring fallback is never needed
    "worker":           ["repo_read"],
    "file_scout":       ["repo_read"],
    "web_scout":        ["external_mcp"],
    "context_curator":  ["repo_read"],
    "scout":            ["repo_read", "external_mcp"],   # generic fallback for future scout roles
    "researcher":       ["repo_read", "external_mcp", "skill_run"],
    "quorum_senior":    ["repo_read", "skill_run"],
}


def get_agent_capabilities(agent: dict[str, Any]) -> list[str]:
    """Return the validated capability list for an agent (unknown keys stripped)."""
    raw = agent.get("capabilities_json") or agent.get("capabilities") or "[]"
    if isinstance(raw, list):
        caps: list[str] = raw
    else:
        try:
            caps = json.loads(raw)
        except Exception:
            caps = []
    known = set(CAPABILITY_CATALOG)
    return [c for c in caps if isinstance(c, str) and c in known]


def check_capability(
    agent: dict[str, Any],
    capability: str,
) -> tuple[bool, str]:
    """Return (allowed, reason) for a single capability check."""
    caps = get_agent_capabilities(agent)
    if capability in caps:
        return True, "capability_granted"
    return False, f"capability_not_granted:{capability}"


def default_capabilities_for_role(role: str) -> list[str]:
    """Return sensible defaults for a given role name string."""
    normalized = role.lower().replace(" ", "_").replace("-", "_")
    for key, caps in DEFAULT_CAPABILITIES_BY_ROLE.items():
        if key in normalized:
            return list(caps)
    return ["repo_read"]
