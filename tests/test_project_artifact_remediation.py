from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from aiteam.project_artifact_audit import AuditOptions
from aiteam.project_artifact_remediation import (
    RemediationPlanError,
    build_remediation_manifest,
    verify_remediation_manifest,
    write_remediation_manifest,
)


def _project(path: Path, *, corrupt: bool = False) -> None:
    dotdir = path / ".aiteam"
    dotdir.mkdir(parents=True)
    db = dotdir / "aiteam.db"
    if corrupt:
        db.write_bytes(b"not sqlite")
        return
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE goals (id TEXT PRIMARY KEY, source TEXT, created_at TEXT);
            CREATE TABLE agents (id TEXT PRIMARY KEY);
            CREATE TABLE issues (id TEXT PRIMARY KEY);
            CREATE TABLE runs (id TEXT PRIMARY KEY);
            INSERT INTO goals VALUES ('goal', 'fixture', '2026-07-30T00:00:00Z');
            """
        )


def _options() -> AuditOptions:
    return AuditOptions(workers=1, git_timeout_seconds=1)


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_all_candidates_become_exact_non_executable_proposals(tmp_path: Path) -> None:
    root = (tmp_path / "legacy").resolve()
    root.mkdir()
    _project(root / "Demo 10")
    _project(root / "Solo 2")
    personal = root / "Personal 99"
    personal.mkdir()
    (personal / "keep.txt").write_text("mine", encoding="utf-8")
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    manifest = build_remediation_manifest(
        root,
        include_all_candidates=True,
        audit_options=_options(),
    )
    after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    assert before == after
    assert manifest["summary"]["proposed_count"] == 2
    assert manifest["summary"]["denied_count"] == 0
    assert manifest["summary"]["ready_for_owner_review"] is True
    assert manifest["safety"]["execution_authorized"] is False
    assert manifest["next_step"]["available"] is False
    assert all(Path(item["resolved_path"]).parent == root for item in manifest["proposals"])
    assert all(item["recoverability"]["implemented_in_this_step"] is False for item in manifest["proposals"])
    assert verify_remediation_manifest(manifest)


def test_personal_active_and_registered_targets_are_denied(tmp_path: Path) -> None:
    root = (tmp_path / "legacy").resolve()
    root.mkdir()
    personal = root / "Personal"
    personal.mkdir()
    active = root / "Demo 2"
    _project(active)
    registered = root / "Solo 3"
    _project(registered)

    manifest = build_remediation_manifest(
        root,
        target_names=[personal.name, active.name, registered.name],
        active_workspace=active,
        registry_workspaces=[registered],
        audit_options=_options(),
    )
    reasons = {
        item["relative_name"]: item["denial_reasons"]
        for item in manifest["denied"]
    }

    assert manifest["summary"]["proposed_count"] == 0
    assert manifest["summary"]["denied_count"] == 3
    assert manifest["summary"]["ready_for_owner_review"] is False
    assert any(reason.startswith("classification_personal") for reason in reasons[personal.name])
    assert "active_current_project" in reasons[active.name]
    assert "referenced_by_workspace_registry" in reasons[registered.name]


@pytest.mark.skipif(
    _git(Path.cwd(), "--version").returncode != 0,
    reason="git no está disponible",
)
def test_live_revalidation_denies_new_git_work_and_remote(tmp_path: Path) -> None:
    root = (tmp_path / "legacy").resolve()
    root.mkdir()
    project = root / "Demo 4"
    _project(project)
    initial = build_remediation_manifest(
        root,
        target_names=[project.name],
        audit_options=_options(),
    )
    assert initial["summary"]["proposed_count"] == 1

    assert _git(project, "init").returncode == 0
    assert (
        _git(project, "remote", "add", "origin", "https://token@example.com/repo.git").returncode
        == 0
    )
    revalidated = build_remediation_manifest(
        root,
        target_names=[project.name],
        audit_options=_options(),
    )

    assert revalidated["summary"]["proposed_count"] == 0
    assert revalidated["summary"]["denied_count"] == 1
    reasons = revalidated["denied"][0]["denial_reasons"]
    assert "git_untracked" in reasons
    assert "git_remote_present" in reasons
    assert "token" not in json.dumps(revalidated)


def test_corrupt_database_and_unknown_identity_are_denied(tmp_path: Path) -> None:
    root = (tmp_path / "legacy").resolve()
    root.mkdir()
    corrupt = root / "Demo 5"
    _project(corrupt, corrupt=True)

    manifest = build_remediation_manifest(
        root,
        target_names=[corrupt.name],
        audit_options=_options(),
    )

    reasons = manifest["denied"][0]["denial_reasons"]
    assert "database_not_valid" in reasons
    assert manifest["safety"]["cleanup_authorized"] is False


@pytest.mark.parametrize(
    "target",
    ["*", "Demo ?", "Demo [2]", ".", "..", "nested/name", r"nested\name"],
)
def test_patterns_roots_and_nested_paths_are_rejected(tmp_path: Path, target: str) -> None:
    root = (tmp_path / "legacy").resolve()
    root.mkdir()

    with pytest.raises(RemediationPlanError):
        build_remediation_manifest(
            root,
            target_names=[target],
            audit_options=_options(),
        )


def test_missing_prefix_is_not_expanded(tmp_path: Path) -> None:
    root = (tmp_path / "legacy").resolve()
    root.mkdir()
    _project(root / "Demo 20")

    with pytest.raises(RemediationPlanError, match="hijo directo exacto"):
        build_remediation_manifest(
            root,
            target_names=["Demo"],
            audit_options=_options(),
        )


def test_clean_root_does_not_generate_all_candidates_plan(tmp_path: Path) -> None:
    root = (tmp_path / "clean").resolve()
    root.mkdir()
    (root / "Personal").mkdir()

    with pytest.raises(RemediationPlanError, match="árbol limpio"):
        build_remediation_manifest(
            root,
            include_all_candidates=True,
            audit_options=_options(),
        )


def test_symlink_is_recorded_as_denied_without_following(tmp_path: Path) -> None:
    root = (tmp_path / "legacy").resolve()
    root.mkdir()
    outside = tmp_path / "outside"
    _project(outside)
    link = root / "Demo 6"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("La máquina no permite crear symlinks")

    manifest = build_remediation_manifest(
        root,
        target_names=[link.name],
        audit_options=_options(),
    )

    assert manifest["summary"]["proposed_count"] == 0
    assert "symlink_or_reparse_point" in manifest["denied"][0]["denial_reasons"]
    assert manifest["denied"][0]["evidence"]["database"]["state"] == "not_inspected_reparse_point"


def test_manifest_is_exclusive_and_must_live_outside_root(tmp_path: Path) -> None:
    root = (tmp_path / "legacy").resolve()
    root.mkdir()
    _project(root / "Demo 7")
    manifest = build_remediation_manifest(
        root,
        include_all_candidates=True,
        audit_options=_options(),
    )

    with pytest.raises(RemediationPlanError, match="fuera"):
        write_remediation_manifest(manifest, root / "plan.json", audited_root=root)

    output = tmp_path / "receipts" / "plan.json"
    write_remediation_manifest(manifest, output, audited_root=root)
    with pytest.raises(RemediationPlanError, match="inmutable"):
        write_remediation_manifest(manifest, output, audited_root=root)
    assert verify_remediation_manifest(json.loads(output.read_text(encoding="utf-8")))


def test_cli_generates_manifest_but_never_authorizes_execution(tmp_path: Path) -> None:
    root = (tmp_path / "legacy").resolve()
    root.mkdir()
    _project(root / "Demo 8")
    output = tmp_path / "plan.json"
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "plan_project_artifact_remediation.py"),
            "--root",
            str(root),
            "--output",
            str(output),
            "--include-all-candidates",
            "--workers",
            "1",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert summary["execution_authorized"] is False
    assert manifest["safety"]["moves_performed"] == 0
    assert manifest["approval"]["status"] == "not_approved"
