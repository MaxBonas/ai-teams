from __future__ import annotations

import os
from pathlib import Path

import pytest

from aiteam.project_hygiene import (
    observe_project_hygiene,
    validate_project_hygiene,
)


def test_clean_root_is_read_only_and_path_redacted(tmp_path: Path) -> None:
    root = (tmp_path / "projects").resolve()
    root.mkdir()
    (root / "Personal").mkdir()
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    report = observe_project_hygiene(root, configured=True)

    assert report["status"] == "clean"
    assert report["scope"]["paths_emitted"] is False
    assert str(root) not in str(report)
    assert before == sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    validate_project_hygiene(report)


def test_known_legacy_families_tombstones_and_staging_are_aggregated(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "projects").resolve()
    root.mkdir()
    for name in ("Demo 2", "Solo 10"):
        (root / name / ".aiteam").mkdir(parents=True)
        (root / name / ".aiteam" / "aiteam.db").touch()
    (root / "Demo 2" / ".aiteam-staging-interrupted").mkdir()
    (root / ".aiteam-deleted-Demo").mkdir()
    (root / ".aiteam-project-staging-interrupted").mkdir()

    report = observe_project_hygiene(root, configured=True)

    assert report["status"] == "legacy_artifacts_detected"
    assert report["counts"]["legacy_numbered"] == 2
    assert report["counts"]["legacy_tombstones"] == 1
    assert report["counts"]["staging_leftovers"] == 2
    assert report["recommended_action"]["code"] == "run_project_artifact_audit"
    assert report["recommended_action"]["mutates_state"] is False
    assert report["legacy_families"] == [
        {"family": "Demo", "count": 1},
        {"family": "Solo", "count": 1},
    ]


def test_numbered_personal_folder_is_not_attributed_without_aiteam_identity(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "projects").resolve()
    root.mkdir()
    (root / "Demo 99").mkdir()

    report = observe_project_hygiene(root, configured=True)

    assert report["status"] == "clean"
    assert report["counts"]["legacy_numbered"] == 0
    assert report["counts"]["aiteam_projects"] == 0


@pytest.mark.parametrize(
    ("root_factory", "configured", "expected"),
    [
        (lambda path: None, False, "not_configured"),
        (lambda path: path / "missing", True, "root_missing"),
    ],
)
def test_unavailable_roots_are_explained_without_creation(
    tmp_path: Path,
    root_factory,
    configured: bool,
    expected: str,
) -> None:
    root = root_factory(tmp_path)

    report = observe_project_hygiene(root, configured=configured)

    assert report["status"] == expected
    assert report["requires_attention"] is True
    assert report["recommended_action"]["mutates_state"] is False
    if root is not None:
        assert not root.exists()


def test_symlink_root_is_never_followed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "Demo 2" / ".aiteam").mkdir(parents=True)
    link = tmp_path / "linked-root"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("La máquina no permite symlinks")

    report = observe_project_hygiene(link, configured=True)

    assert report["status"] == "review_required"
    assert report["root"]["reparse_point"] is True
    assert report["counts"]["aiteam_projects"] == 0
    assert report["scope"]["symlinks_followed"] is False
