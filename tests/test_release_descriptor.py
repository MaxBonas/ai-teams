from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aiteam.release_descriptor import (
    ReleaseDescriptorError,
    validate_release_descriptor,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _write(
        root / "pyproject.toml",
        '[project]\nname = "fixture"\nversion = "1.2.3"\n',
    )
    _write(root / "LICENSE", "license\n")
    _write(root / "NOTICE", "notice\n")
    _write(root / "scripts/migrate_to_v2.py", "print('fixture')\n")
    _write(root / "docs/UPGRADE_AND_ROLLBACK.md", "# Upgrade\n")
    _write(
        root / "docs/releases/v1.2.3.md",
        "# AI Teams v1.2.3\n\n## Upgrade\n\n## Rollback\n",
    )
    descriptor = {
        "schema_version": "release_descriptor_v1",
        "version": "1.2.3",
        "tag": "v1.2.3",
        "notes_path": "docs/releases/v1.2.3.md",
        "upgrade_path": "docs/UPGRADE_AND_ROLLBACK.md",
        "required_note_headings": [
            "# AI Teams v1.2.3",
            "## Upgrade",
            "## Rollback",
        ],
        "database": {
            "migration_command_path": "scripts/migrate_to_v2.py",
            "backup_required": True,
            "rollback_requires_backup_restore": True,
        },
        "publish": {
            "github_release": True,
            "enabled": True,
            "prerelease": False,
        },
    }
    _write(
        root / "config/releases/v1.2.3.json",
        json.dumps(descriptor, indent=2) + "\n",
    )
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    return root


def test_current_release_descriptor_is_valid() -> None:
    descriptor = validate_release_descriptor(PROJECT_ROOT, "0.1.0")

    assert descriptor.tag == "v0.1.0"
    assert descriptor.notes_path.as_posix() == "docs/releases/v0.1.0.md"
    assert descriptor.prerelease is False
    assert descriptor.publication_enabled is False


def test_descriptor_requires_version_alignment(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _write(
        root / "pyproject.toml",
        '[project]\nname = "fixture"\nversion = "1.2.4"\n',
    )

    with pytest.raises(ReleaseDescriptorError, match="pyproject"):
        validate_release_descriptor(root, "1.2.3")


def test_descriptor_rejects_missing_note_heading(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _write(root / "docs/releases/v1.2.3.md", "# AI Teams v1.2.3\n")

    with pytest.raises(ReleaseDescriptorError, match="encabezado exacto"):
        validate_release_descriptor(root, "1.2.3")


def test_publication_requires_annotated_exact_tag(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _git(root, "tag", "v1.2.3")

    with pytest.raises(ReleaseDescriptorError, match="tag anotado"):
        validate_release_descriptor(root, "1.2.3", require_tag=True)

    _git(root, "tag", "-d", "v1.2.3")
    _git(root, "tag", "-a", "v1.2.3", "-m", "release")
    descriptor = validate_release_descriptor(root, "1.2.3", require_tag=True)
    assert descriptor.version == "1.2.3"


def test_disabled_candidate_cannot_be_published(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    descriptor_path = root / "config/releases/v1.2.3.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["publish"]["enabled"] = False
    _write(descriptor_path, json.dumps(descriptor, indent=2) + "\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "disable release")
    _git(root, "tag", "-a", "v1.2.3", "-m", "release")

    with pytest.raises(ReleaseDescriptorError, match="bloqueada"):
        validate_release_descriptor(root, "1.2.3", require_tag=True)
