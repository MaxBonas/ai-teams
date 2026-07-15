"""Tests del VCS de workspaces capa-2 (diffs como recibo, rollback)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aiteam.workspace_git import commit_run_snapshot, init_managed_repo, is_git_managed

_git_available = subprocess.run(
    ["git", "--version"], capture_output=True
).returncode == 0

pytestmark = pytest.mark.skipif(not _git_available, reason="git no disponible")


def test_init_managed_repo_creates_repo_marker_and_gitignore(tmp_path: Path) -> None:
    assert init_managed_repo(tmp_path) is True

    assert (tmp_path / ".git").exists()
    assert (tmp_path / ".aiteam" / "git_managed").exists()
    assert ".aiteam/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert is_git_managed(tmp_path)


def test_init_managed_repo_never_touches_external_repos(tmp_path: Path) -> None:
    """Workspace externo del usuario con su propio .git: ni init ni marker —
    y sin marker, commit_run_snapshot es un no-op para siempre."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)

    assert init_managed_repo(tmp_path) is False
    assert not (tmp_path / ".aiteam" / "git_managed").exists()
    assert not is_git_managed(tmp_path)

    (tmp_path / "user_file.txt").write_text("suyo", encoding="utf-8")
    assert commit_run_snapshot(tmp_path, run_id="run:x", agent_id="a") is None


def test_commit_run_snapshot_produces_receipt_and_history(tmp_path: Path) -> None:
    init_managed_repo(tmp_path)
    (tmp_path / "notas.py").write_text("print('v1')\n", encoding="utf-8")

    receipt = commit_run_snapshot(tmp_path, run_id="run:1", agent_id="role:engineer", issue_id="issue-1")

    assert receipt is not None
    assert receipt["commit"] != "?"
    assert "notas.py" in receipt["diffstat"]

    # El mensaje lleva la atribución run/agente/issue (el recibo es auditable).
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "run:1" in log and "role:engineer" in log and "issue-1" in log

    # Sin cambios nuevos: no hay commit vacío.
    assert commit_run_snapshot(tmp_path, run_id="run:2", agent_id="a") is None

    # Rollback determinista: revertir al bootstrap elimina el archivo.
    subprocess.run(["git", "checkout", "HEAD~1", "--", "."], cwd=tmp_path, capture_output=True)


def test_aiteam_dir_is_ignored_by_the_receipt(tmp_path: Path) -> None:
    """El control plane (.aiteam/aiteam.db) churnea en cada tick — jamás debe
    entrar en la historia del workspace."""
    init_managed_repo(tmp_path)
    (tmp_path / ".aiteam" / "aiteam.db").write_bytes(b"sqlite fake")

    assert commit_run_snapshot(tmp_path, run_id="run:1", agent_id="a") is None
