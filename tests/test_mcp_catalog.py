"""Catálogo MCP curado: contratos estáticos, exactos y sin instalación."""
from __future__ import annotations

from pathlib import Path

import pytest

from aiteam.mcp_catalog import (
    CATALOG_VERSION,
    get_mcp_catalog_entry,
    list_mcp_catalog,
    resolve_catalog_proposal,
)


def test_initial_catalog_is_reviewed_exact_and_non_installing() -> None:
    entries = list_mcp_catalog()
    assert CATALOG_VERSION == 1
    assert {entry["id"] for entry in entries} == {
        "github-readonly", "playwright-browser", "filesystem-workspace",
    }
    forbidden_sources = {"npx", "npm", "pnpm", "yarn", "bunx", "docker", "cmd", "pwsh"}
    for entry in entries:
        assert entry["source"] not in forbidden_sources
        assert "latest" not in entry["version"].lower()
        assert "latest" not in entry["distribution_version"].lower()
        assert entry["homepage"].startswith("https://")
        assert entry["reviewed_at"] == "2026-07-20"


def test_catalog_listing_returns_copies() -> None:
    first = list_mcp_catalog()
    first[0]["args"].append("--unsafe")
    assert "--unsafe" not in list_mcp_catalog()[0]["args"]


def test_filesystem_descriptor_resolves_versioned_package_and_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "global-bin"
    entrypoint = bin_dir / "node_modules" / "@modelcontextprotocol" / "server-filesystem" / "dist" / "index.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("// server", encoding="utf-8")
    (entrypoint.parent.parent / "package.json").write_text(
        '{"version":"2026.7.10"}', encoding="utf-8"
    )
    shim = bin_dir / "mcp-server-filesystem.cmd"
    shim.write_text("@echo off", encoding="utf-8")
    monkeypatch.setattr("aiteam.mcp_catalog.shutil.which", lambda command: str(shim))

    resolved = resolve_catalog_proposal(
        {
            "reason": "extension_install_requested",
            "catalog_id": "filesystem-workspace",
            "justification": "Reviewer needs an independently inventoried workspace view.",
        },
        workspace_root=tmp_path,
    )
    assert resolved["source"] == "node"
    assert resolved["args"] == [str(entrypoint.resolve()), str(tmp_path.resolve())]
    assert resolved["version"] == "0.2.0"
    assert resolved["catalog_artifact_version"] == "2026.7.10"


def test_catalog_contract_cannot_be_overridden(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot override source"):
        resolve_catalog_proposal(
            {
                "catalog_id": "github-readonly",
                "source": "evil-mcp",
                "justification": "x",
            },
            workspace_root=tmp_path,
        )


def test_unknown_catalog_entry_fails_closed() -> None:
    with pytest.raises(LookupError):
        get_mcp_catalog_entry("unknown")
