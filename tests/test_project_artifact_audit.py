from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from aiteam.project_artifact_audit import (
    AuditOptions,
    audit_project_root,
    write_audit_receipt,
)


def _create_aiteam_project(path: Path, *, legacy: bool = False) -> None:
    dotdir = path / ".aiteam"
    dotdir.mkdir(parents=True)
    with sqlite3.connect(dotdir / "aiteam.db") as conn:
        if legacy:
            conn.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY, payload TEXT)")
            return
        conn.executescript(
            """
            CREATE TABLE goals (
                id TEXT PRIMARY KEY,
                source TEXT,
                created_at TEXT
            );
            CREATE TABLE agents (id TEXT PRIMARY KEY);
            CREATE TABLE issues (id TEXT PRIMARY KEY);
            CREATE TABLE runs (id TEXT PRIMARY KEY);
            INSERT INTO goals (id, source, created_at)
            VALUES ('private-goal-id', 'guided_setup', '2026-07-30T00:00:00Z');
            """
        )


def _entry(report: dict, name: str) -> dict:
    return next(item for item in report["entries"] if item["relative_path"] == name)


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_classification_is_conservative_and_receipt_is_redacted(tmp_path: Path) -> None:
    root = (tmp_path / "mixed-projects").resolve()
    root.mkdir()
    personal = root / "Cliente Personal 2"
    personal.mkdir()
    (personal / "contrato-secreto.txt").write_text("keep", encoding="utf-8")

    active = root / "Active"
    _create_aiteam_project(active)
    candidate = root / "Demo 42"
    _create_aiteam_project(candidate, legacy=True)
    candidate_starting_with_one = root / "Demo 10"
    _create_aiteam_project(candidate_starting_with_one)
    preserve = root / "Unnumbered AI Teams"
    _create_aiteam_project(preserve)
    corrupt = root / "Demo 43"
    (corrupt / ".aiteam").mkdir(parents=True)
    (corrupt / ".aiteam" / "aiteam.db").write_bytes(b"not-sqlite")

    before = {
        path.relative_to(root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
    }
    report = audit_project_root(
        root,
        active_workspace=active,
        options=AuditOptions(workers=2),
    )
    after = {
        path.relative_to(root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
    }

    assert before == after
    assert _entry(report, personal.name)["classification"] == "personal_protected"
    assert _entry(report, active.name)["classification"] == "active_current_project"
    assert _entry(report, candidate.name)["classification"] == "aiteam_disposable_candidate"
    assert (
        _entry(report, candidate_starting_with_one.name)["classification"]
        == "aiteam_disposable_candidate"
    )
    assert _entry(report, preserve.name)["classification"] == "aiteam_preserve_or_migrate"
    assert _entry(report, corrupt.name)["classification"] == "ambiguous_owner_review_required"
    serialized = json.dumps(report, ensure_ascii=False)
    assert str(root) not in serialized
    assert "contrato-secreto.txt" not in serialized
    assert "private-goal-id" not in serialized
    assert report["safety"]["cleanup_authorized"] is False
    assert report["content_sha256"]


@pytest.mark.skipif(
    _git(Path.cwd(), "--version").returncode != 0,
    reason="git no está disponible",
)
def test_git_work_and_remote_are_preserved_without_leaking_url_secret(tmp_path: Path) -> None:
    root = (tmp_path / "projects").resolve()
    root.mkdir()
    project = root / "Demo 9"
    _create_aiteam_project(project)
    assert _git(project, "init").returncode == 0
    assert (
        _git(
            project,
            "remote",
            "add",
            "origin",
            "https://user:super-secret-token@example.com/private/repo.git",
        ).returncode
        == 0
    )
    report = audit_project_root(root)
    entry = _entry(report, project.name)
    serialized = json.dumps(report)

    assert entry["classification"] == "aiteam_preserve_or_migrate"
    assert entry["evidence"]["git"]["untracked"] is True
    assert entry["evidence"]["git"]["remote_count"] == 1
    assert entry["evidence"]["git"]["remote_hosts"] == ["example.com"]
    assert entry["evidence"]["git"]["branch"]["ref_sha256"]
    assert "super-secret-token" not in serialized
    assert "private/repo" not in serialized
    assert "local-untracked.txt" not in serialized
    assert '"master"' not in serialized


def test_unknown_git_state_and_incomplete_size_never_become_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "projects").resolve()
    root.mkdir()
    project = root / "Demo 10"
    _create_aiteam_project(project)
    (project / ".git").mkdir()

    monkeypatch.setattr(
        "aiteam.project_artifact_audit._inspect_git",
        lambda _path, *, timeout: {"state": "status_timeout"},
    )
    report = audit_project_root(root)

    assert _entry(report, project.name)["classification"] == "ambiguous_owner_review_required"


def test_clean_remote_alone_forces_preservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "projects").resolve()
    root.mkdir()
    project = root / "Demo 11"
    _create_aiteam_project(project)
    (project / ".git").mkdir()
    monkeypatch.setattr(
        "aiteam.project_artifact_audit._inspect_git",
        lambda _path, *, timeout: {
            "state": "observed",
            "dirty": False,
            "untracked": False,
            "remote_count": 1,
            "remote_hosts": ["example.com"],
        },
    )

    entry = _entry(audit_project_root(root), project.name)

    assert entry["classification"] == "aiteam_preserve_or_migrate"
    assert entry["reasons"] == ["git_has_remote"]


def test_receipt_must_live_outside_audited_root(tmp_path: Path) -> None:
    root = (tmp_path / "projects").resolve()
    root.mkdir()
    report = audit_project_root(root)

    with pytest.raises(ValueError, match="dentro de la raíz auditada"):
        write_audit_receipt(report, root / "receipt.json", audited_root=root)

    output = tmp_path / "receipts" / "audit.json"
    write_audit_receipt(report, output, audited_root=root)
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"].endswith("_v1")


def test_cli_writes_only_the_external_redacted_receipt(tmp_path: Path) -> None:
    root = (tmp_path / "projects").resolve()
    root.mkdir()
    (root / "Personal").mkdir()
    output = tmp_path / "receipt.json"
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "audit_project_artifacts.py"),
            "--root",
            str(root),
            "--output",
            str(output),
            "--workers",
            "1",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in root.iterdir()) == ["Personal"]
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["root"] == "<selected-projects-root>"
    assert receipt["safety"]["project_writes_performed"] == 0
    assert json.loads(result.stdout)["cleanup_authorized"] is False


def test_relative_root_is_rejected() -> None:
    with pytest.raises(ValueError, match="absoluta"):
        audit_project_root(Path("relative-project-root"))


def test_symlink_or_reparse_point_is_not_followed(tmp_path: Path) -> None:
    root = (tmp_path / "projects").resolve()
    root.mkdir()
    target = tmp_path / "outside"
    _create_aiteam_project(target)
    link = root / "Demo 88"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("La máquina no permite crear symlinks de directorio")

    report = audit_project_root(root)
    entry = _entry(report, link.name)

    assert entry["classification"] == "ambiguous_owner_review_required"
    assert entry["evidence"]["reparse_point"] is True
    assert entry["evidence"]["database"]["state"] == "not_inspected_reparse_point"
