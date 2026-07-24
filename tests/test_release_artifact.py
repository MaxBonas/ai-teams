from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from aiteam.release_artifact import (
    ReleaseArtifactError,
    build_release_artifact,
    verify_release_artifact,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_SOURCE = PROJECT_ROOT / "config" / "release_artifact.v1.json"


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


def _fixture_repository(tmp_path: Path, *, with_license: bool = True) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    contract = json.loads(CONTRACT_SOURCE.read_text(encoding="utf-8"))
    _write(root / "config/release_artifact.v1.json", json.dumps(contract))
    _write(root / "README.md", "# AI Teams fixture\n")
    _write(root / "package.json", '{"name":"fixture","version":"1.2.3"}\n')
    _write(
        root / "pyproject.toml",
        (
            "[project]\n"
            'name = "fixture"\n'
            'version = "1.2.3"\n'
            'dependencies = ["fastapi>=1"]\n'
        ),
    )
    _write(
        root / "uv.lock",
        (
            "version = 1\n"
            'requires-python = ">=3.10"\n\n'
            "[[package]]\n"
            'name = "fastapi"\n'
            'version = "1.0.0"\n'
            'source = { registry = "https://pypi.org/simple" }\n'
            'sdist = { url = "https://example.invalid/fastapi.tar.gz", '
            'hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }\n\n'
            "[[package]]\n"
            'name = "fixture"\n'
            'version = "1.2.3"\n'
            'source = { editable = "." }\n'
            'dependencies = [{ name = "fastapi" }]\n'
        ),
    )
    _write(root / "requirements.lock", "fastapi==1.0.0 --hash=sha256:aaaa\n")
    _write(root / "requirements-dev.lock", "fastapi==1.0.0 --hash=sha256:aaaa\n")
    _write(root / "ide-frontend/package.json", '{"name":"web","version":"1.2.3"}\n')
    _write(
        root / "ide-frontend/package-lock.json",
        json.dumps(
            {
                "name": "web",
                "version": "1.2.3",
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "web", "version": "1.2.3"},
                    "node_modules/react": {
                        "version": "19.2.8",
                        "license": "MIT",
                        "integrity": "sha512-fixture",
                    },
                },
            }
        ),
    )
    _write(root / "config/installation_support.v1.json", "{}\n")
    _write(root / "config/release_descriptor.v1.schema.json", "{}\n")
    _write(root / "config/releases/v0.1.0.json", "{}\n")
    _write(root / "docs/INSTALLATION_AND_INTEGRATION.md", "# Install\n")
    _write(root / "docs/UPGRADE_AND_ROLLBACK.md", "# Upgrade\n")
    _write(root / "docs/releases/v0.1.0.md", "# Release\n")
    _write(root / "scripts/prepare_dev_env.bat", "@echo off\r\n")
    _write(root / "scripts/prepare_dev_env.sh", "#!/bin/sh\n")
    _write(root / "scripts/accept_release_archive.py", "print('fixture')\n")
    _write(root / "scripts/accept_posix_clean_room.py", "print('fixture')\n")
    _write(root / "scripts/accept_windows_clean_room.py", "print('fixture')\n")
    _write(root / "scripts/validate_release_candidate.py", "print('fixture')\n")
    _write(root / "scripts/verify_release_artifact.py", "print('fixture')\n")
    if with_license:
        _write(root / "LICENSE", "Fixture license\n")
    _write(root / "NOTICE", "Fixture notice\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    return root


def _archive_members(archive: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(archive) as value:
        return {name: value.read(name) for name in value.namelist()}


def test_release_is_deterministic_and_checksums_cover_every_payload(
    tmp_path: Path,
) -> None:
    root = _fixture_repository(tmp_path)
    first = build_release_artifact(root, tmp_path / "one", "1.2.3")
    second = build_release_artifact(root, tmp_path / "two", "1.2.3")

    assert first.archive.read_bytes() == second.archive.read_bytes()
    assert (
        first.archive_sha256 == hashlib.sha256(first.archive.read_bytes()).hexdigest()
    )
    assert first.checksum.read_text(encoding="utf-8") == (
        f"{first.archive_sha256}  {first.archive.name}\n"
    )
    members = _archive_members(first.archive)
    checksum_name = "ai-teams-1.2.3/RELEASE-METADATA/SHA256SUMS"
    checksum_lines = members.pop(checksum_name).decode("utf-8").splitlines()
    expected = {
        path.removeprefix("ai-teams-1.2.3/"): hashlib.sha256(content).hexdigest()
        for path, content in members.items()
    }
    actual = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in checksum_lines}
    assert actual == expected


def test_manifest_and_inventory_are_explicit_about_unresolved_licenses(
    tmp_path: Path,
) -> None:
    root = _fixture_repository(tmp_path)
    result = build_release_artifact(root, tmp_path / "out", "1.2.3")
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    sbom = json.loads(result.sbom.read_text(encoding="utf-8"))
    licenses = json.loads(result.licenses.read_text(encoding="utf-8"))

    assert manifest["source_strategy"] == "git_tracked"
    assert manifest["promotion_allowed"] is False
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.6"
    assert {component["name"] for component in sbom["components"]} == {
        "fastapi",
        "react",
    }
    assert licenses["summary"] == {
        "dependencies": 2,
        "known_license": 1,
        "unknown_license": 1,
        "locked": 2,
        "declared_unlocked": 0,
    }
    assert licenses["promotion_blockers"] == []


def test_clean_exact_tag_without_inventory_blockers_is_promotable(
    tmp_path: Path,
) -> None:
    root = _fixture_repository(tmp_path, with_license=True)
    _git(root, "tag", "v1.2.3")

    result = build_release_artifact(
        root,
        tmp_path / "out",
        "1.2.3",
        require_release_tag=True,
    )

    assert result.promotion_allowed is True


def test_dirty_worktree_fails_closed_and_preview_is_marked(
    tmp_path: Path,
) -> None:
    root = _fixture_repository(tmp_path)
    _write(root / "README.md", "# changed\n")

    with pytest.raises(ReleaseArtifactError, match="worktree está sucio"):
        build_release_artifact(root, tmp_path / "blocked", "1.2.3")

    preview = build_release_artifact(
        root,
        tmp_path / "preview",
        "1.2.3-preview.1",
        allow_dirty=True,
    )
    manifest = json.loads(preview.manifest.read_text(encoding="utf-8"))
    assert manifest["dirty"] is True
    assert manifest["promotion_allowed"] is False


@pytest.mark.parametrize(
    ("path", "content", "message"),
    [
        ("runtime/session.sqlite", "state", "Estado runtime"),
        (".env", "TOKEN=value", "Nombre sensible"),
        (".env.production", "TOKEN=value", "Nombre sensible"),
        (
            "config/secret.txt",
            "-----BEGIN " + "PRIVATE KEY-----\nnot-a-real-key",
            "Posible secreto private_key",
        ),
    ],
)
def test_sensitive_tracked_content_is_rejected(
    tmp_path: Path,
    path: str,
    content: str,
    message: str,
) -> None:
    root = _fixture_repository(tmp_path)
    _write(root / path, content)
    _git(root, "add", "-f", path)
    _git(root, "commit", "-qm", "sensitive fixture")

    with pytest.raises(ReleaseArtifactError, match=message):
        build_release_artifact(root, tmp_path / "out", "1.2.3")


def test_utf16_secret_is_rejected(tmp_path: Path) -> None:
    root = _fixture_repository(tmp_path)
    secret_path = root / "config" / "utf16-secret.txt"
    secret_path.write_bytes(
        ("-----BEGIN " + "PRIVATE KEY-----\nnot-a-real-key").encode("utf-16")
    )
    _git(root, "add", secret_path.relative_to(root).as_posix())
    _git(root, "commit", "-qm", "utf16 secret fixture")

    with pytest.raises(ReleaseArtifactError, match="Posible secreto private_key"):
        build_release_artifact(root, tmp_path / "out", "1.2.3")


def test_release_tag_is_required_when_requested(tmp_path: Path) -> None:
    root = _fixture_repository(tmp_path, with_license=True)

    with pytest.raises(ReleaseArtifactError, match="tag exacto"):
        build_release_artifact(
            root,
            tmp_path / "out",
            "1.2.3",
            require_release_tag=True,
        )


def test_verifier_checks_outer_and_every_inner_checksum(tmp_path: Path) -> None:
    root = _fixture_repository(tmp_path, with_license=True)
    _git(root, "tag", "v1.2.3")
    result = build_release_artifact(
        root, tmp_path / "out", "1.2.3", require_release_tag=True
    )

    verified = verify_release_artifact(
        result.archive,
        checksum_path=result.checksum,
        require_promotable=True,
    )

    assert verified.version == "1.2.3"
    assert verified.promotion_allowed is True
    assert verified.files_verified > 10


def test_verifier_rejects_tampered_outer_archive(tmp_path: Path) -> None:
    root = _fixture_repository(tmp_path)
    result = build_release_artifact(root, tmp_path / "out", "1.2.3")
    result.archive.write_bytes(result.archive.read_bytes() + b"tampered")

    with pytest.raises(ReleaseArtifactError, match="checksum externo"):
        verify_release_artifact(result.archive, checksum_path=result.checksum)


def test_verifier_fails_closed_for_preview(tmp_path: Path) -> None:
    root = _fixture_repository(tmp_path)
    result = build_release_artifact(root, tmp_path / "out", "1.2.3")

    with pytest.raises(ReleaseArtifactError, match="no autoriza"):
        verify_release_artifact(result.archive, require_promotable=True)


def test_verifier_rejects_external_sidecar_divergence(tmp_path: Path) -> None:
    root = _fixture_repository(tmp_path)
    result = build_release_artifact(root, tmp_path / "out", "1.2.3")
    result.manifest.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ReleaseArtifactError, match="sidecar externo"):
        verify_release_artifact(result.archive, checksum_path=result.checksum)


def test_verifier_rejects_windows_case_collisions(tmp_path: Path) -> None:
    archive_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("ai-teams-1.2.3/README.md", b"one")
        archive.writestr("ai-teams-1.2.3/readme.md", b"two")

    with pytest.raises(ReleaseArtifactError, match="rutas duplicadas"):
        verify_release_artifact(archive_path)
