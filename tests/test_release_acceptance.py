from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.accept_posix_clean_room import (
    _github_provenance as _posix_github_provenance,
)
from scripts.accept_posix_clean_room import _redact as _posix_redact
from scripts.accept_release_archive import (
    _acceptance_script_name,
    _redact,
    _redact_value,
    _remove_tree,
    _safe_child,
    _validate_required_steps,
)
from scripts.accept_windows_clean_room import _is_lower_hex


def test_release_workspace_only_allows_direct_children(tmp_path: Path) -> None:
    child = _safe_child(tmp_path, "candidate")
    assert child.parent == tmp_path.resolve()

    with pytest.raises(RuntimeError, match="hija directa"):
        _safe_child(tmp_path, "../outside")


def test_release_selects_platform_specific_inner_harness() -> None:
    assert _acceptance_script_name("nt") == "accept_windows_clean_room.py"
    assert _acceptance_script_name("posix") == "accept_posix_clean_room.py"
    with pytest.raises(RuntimeError, match="no soportada"):
        _acceptance_script_name("java")


def test_release_cleanup_rejects_outside_and_removes_exact_child(
    tmp_path: Path,
) -> None:
    child = tmp_path / "candidate"
    child.mkdir()
    (child / "state.txt").write_text("temporary", encoding="utf-8")

    assert _remove_tree(child, parent=tmp_path) is True
    assert not child.exists()
    with pytest.raises(RuntimeError, match="fuera"):
        _remove_tree(tmp_path.parent, parent=tmp_path)


def test_release_receipt_redacts_local_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "profile"
    monkeypatch.setenv("USERPROFILE", str(profile))

    result = _redact(f"workspace={tmp_path} profile={profile}", tmp_path)

    assert str(tmp_path) not in result
    assert "<local_path>" in result
    nested = _redact_value({"failure": [f"path={tmp_path}"]}, tmp_path)
    assert str(tmp_path) not in nested["failure"][0]


def test_release_digest_validation_is_strict_lowercase_hex() -> None:
    assert _is_lower_hex("a" * 40, {40, 64})
    assert _is_lower_hex("0" * 64, {64})
    assert not _is_lower_hex("A" * 64, {64})
    assert not _is_lower_hex("g" * 64, {64})
    assert not _is_lower_hex("a" * 39, {40, 64})


def test_release_receipt_requires_every_canonical_step() -> None:
    contract = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "config"
            / "installation_support.v1.json"
        ).read_text(encoding="utf-8")
    )
    required = contract["release_acceptance_contract"]["required_steps"]
    receipt = {"steps": [{"name": name, "ok": True} for name in required]}

    assert _validate_required_steps(receipt)["ok"] is True
    receipt["steps"].pop()
    result = _validate_required_steps(receipt)
    assert result["ok"] is False
    assert result["missing_steps"] == [required[-1]]


@pytest.mark.parametrize(
    ("system", "runner_os", "runner_arch"),
    [("linux", "Linux", "X64"), ("darwin", "macOS", "ARM64")],
)
def test_posix_ci_provenance_requires_matching_runner_and_revision(
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    runner_os: str,
    runner_arch: str,
) -> None:
    revision = "a" * 40
    values = {
        "GITHUB_ACTIONS": "true",
        "CI": "true",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_JOB": "release-acceptance",
        "AITEAM_EXPECTED_SOURCE_SHA": revision,
        "GITHUB_SHA": revision,
        "RUNNER_OS": runner_os,
        "RUNNER_ARCH": runner_arch,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    independent, provenance = _posix_github_provenance(revision, system=system)

    assert independent is True
    assert provenance["runner_os"] == runner_os
    monkeypatch.setenv("AITEAM_EXPECTED_SOURCE_SHA", "b" * 40)
    assert _posix_github_provenance(revision, system=system)[0] is False


def test_posix_receipt_redacts_home_and_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    fixture = tmp_path / "fixture"
    monkeypatch.setenv("HOME", str(home))

    result = _posix_redact(f"home={home} fixture={fixture}", fixture_root=fixture)

    assert str(home) not in result
    assert str(fixture) not in result
    assert "<home>" in result
    assert "<fixture_root>" in result
