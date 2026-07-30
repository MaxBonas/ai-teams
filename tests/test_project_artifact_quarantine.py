from __future__ import annotations

import contextlib
import json
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from aiteam.project_artifact_audit import AuditOptions
from aiteam.project_artifact_quarantine import (
    QuarantineError,
    apply_quarantine,
    restore_quarantine,
    verify_quarantine_journal,
)
from aiteam.project_artifact_remediation import (
    build_remediation_manifest,
    write_remediation_manifest,
)


@pytest.fixture(autouse=True)
def _fast_observed_handle_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    def probe(_root: Path, children: list[Path], _enabled: bool):
        return (
            {child.name: 0 for child in children},
            {"state": "observed", "processes_denied": 0},
        )

    monkeypatch.setattr("aiteam.project_artifact_audit._probe_handles", probe)


def _project(path: Path, marker: bytes) -> None:
    dotdir = path / ".aiteam"
    dotdir.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(dotdir / "aiteam.db")) as conn:
        conn.executescript(
            """
            CREATE TABLE goals (id TEXT PRIMARY KEY, source TEXT, created_at TEXT);
            CREATE TABLE agents (id TEXT PRIMARY KEY);
            CREATE TABLE issues (id TEXT PRIMARY KEY);
            CREATE TABLE runs (id TEXT PRIMARY KEY);
            INSERT INTO goals VALUES ('goal', 'fixture', '2026-07-30T00:00:00Z');
            """
        )
    nested = path / "nested"
    nested.mkdir()
    (nested / "payload.bin").write_bytes(marker)


def _manifest(
    tmp_path: Path,
    *,
    names: tuple[str, ...] = ("Demo 2",),
) -> tuple[Path, Path, Path, dict]:
    base = tmp_path.parent / f"quarantine-{uuid.uuid4().hex[:8]}"
    base.mkdir()
    root = (base / "legacy").resolve()
    root.mkdir()
    for index, name in enumerate(names):
        _project(root / name, f"payload-{index}".encode())
    quarantine_root = (base / "quarantine").resolve()
    quarantine_root.mkdir()
    manifest = build_remediation_manifest(
        root,
        target_names=names,
        audit_options=AuditOptions(
            workers=1,
            git_timeout_seconds=1,
            probe_process_handles=True,
        ),
    )
    manifest_path = base / "manifest.json"
    write_remediation_manifest(manifest, manifest_path, audited_root=root)
    return root, quarantine_root, manifest_path, manifest


def _apply(
    manifest_path: Path,
    quarantine_root: Path,
    manifest: dict,
) -> dict:
    return apply_quarantine(
        manifest_path,
        quarantine_root,
        approved_manifest_sha256=manifest["manifest_sha256"],
        approved_batch_sha256=manifest["approval"]["target_batch_sha256"],
        workers=1,
        git_timeout_seconds=1,
    )


def test_atomic_directory_move_primitive(tmp_path: Path) -> None:
    from aiteam.project_artifact_quarantine import _atomic_move

    source = tmp_path / "atomic-source"
    destination_parent = tmp_path / "atomic-destination"
    source.mkdir()
    destination_parent.mkdir()
    (source / "payload.txt").write_text("probe", encoding="utf-8")

    _atomic_move(source, destination_parent / "moved")

    assert not source.exists()
    assert (destination_parent / "moved" / "payload.txt").is_file()


def test_quarantine_and_restore_are_byte_exact_and_keep_journal(tmp_path: Path) -> None:
    root, quarantine_root, manifest_path, manifest = _manifest(tmp_path)
    source = root / "Demo 2"
    original = (source / "nested" / "payload.bin").read_bytes()

    quarantined = _apply(manifest_path, quarantine_root, manifest)
    batch_dir = quarantine_root / quarantined["batch_id"]

    assert quarantined["state"] == "quarantined"
    assert not source.exists()
    assert (batch_dir / "items" / source.name / "nested" / "payload.bin").read_bytes() == original
    assert quarantined["purge_authorized"] is False
    assert quarantined["purge_supported"] is False
    assert verify_quarantine_journal(batch_dir)

    restored = restore_quarantine(
        batch_dir,
        approved_batch_sha256=manifest["approval"]["target_batch_sha256"],
    )

    assert restored["state"] == "restored"
    assert (source / "nested" / "payload.bin").read_bytes() == original
    assert not (batch_dir / "items" / source.name).exists()
    assert (batch_dir / "journal.json").is_file()
    assert (batch_dir / "approved-remediation-manifest.json").is_file()
    assert verify_quarantine_journal(batch_dir)


@pytest.mark.parametrize("wrong_field", ["manifest", "batch"])
def test_wrong_approval_hash_causes_zero_mutation(
    tmp_path: Path, wrong_field: str
) -> None:
    root, quarantine_root, manifest_path, manifest = _manifest(tmp_path)
    manifest_hash = manifest["manifest_sha256"]
    batch_hash = manifest["approval"]["target_batch_sha256"]
    if wrong_field == "manifest":
        manifest_hash = "0" * 64
    else:
        batch_hash = "0" * 64

    with pytest.raises(QuarantineError, match="hash aprobado"):
        apply_quarantine(
            manifest_path,
            quarantine_root,
            approved_manifest_sha256=manifest_hash,
            approved_batch_sha256=batch_hash,
            workers=1,
        )

    assert (root / "Demo 2").is_dir()
    assert list(quarantine_root.iterdir()) == []


def test_batch_destination_collision_fails_before_any_move(tmp_path: Path) -> None:
    root, quarantine_root, manifest_path, manifest = _manifest(tmp_path)
    batch_hash = manifest["approval"]["target_batch_sha256"]
    (quarantine_root / f"batch-{batch_hash[:16]}").mkdir()

    with pytest.raises(QuarantineError, match="ya existe"):
        _apply(manifest_path, quarantine_root, manifest)

    assert (root / "Demo 2").is_dir()


def test_live_drift_after_manifest_fails_before_batch_creation(tmp_path: Path) -> None:
    root, quarantine_root, manifest_path, manifest = _manifest(tmp_path)
    (root / "Demo 2" / "new-user-file.txt").write_text("changed", encoding="utf-8")

    with pytest.raises(QuarantineError, match="evidencia viva cambió"):
        _apply(manifest_path, quarantine_root, manifest)

    assert (root / "Demo 2").is_dir()
    assert list(quarantine_root.iterdir()) == []


def test_cross_filesystem_is_rejected_before_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, quarantine_root, manifest_path, manifest = _manifest(tmp_path)
    monkeypatch.setattr(
        "aiteam.project_artifact_quarantine._same_filesystem",
        lambda _left, _right: False,
    )

    with pytest.raises(QuarantineError, match="filesystem"):
        _apply(manifest_path, quarantine_root, manifest)

    assert (root / "Demo 2").is_dir()
    assert list(quarantine_root.iterdir()) == []


def test_interrupted_batch_rolls_back_every_applied_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = ("Demo 2", "Solo 3")
    root, quarantine_root, manifest_path, manifest = _manifest(tmp_path, names=names)
    from aiteam import project_artifact_quarantine as quarantine_module

    original_move = quarantine_module._atomic_move
    calls = 0

    def fail_second_move(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected interruption")
        original_move(source, destination)

    monkeypatch.setattr(quarantine_module, "_atomic_move", fail_second_move)

    with pytest.raises(QuarantineError, match="se revirtieron"):
        _apply(manifest_path, quarantine_root, manifest)

    assert all((root / name).is_dir() for name in names)
    batch_dir = quarantine_root / f"batch-{manifest['approval']['target_batch_sha256'][:16]}"
    journal = json.loads((batch_dir / "journal.json").read_text(encoding="utf-8"))
    assert journal["state"] == "rolled_back"
    assert verify_quarantine_journal(batch_dir)


def test_restore_collision_is_detected_before_any_restore(tmp_path: Path) -> None:
    names = ("Demo 2", "Solo 3")
    root, quarantine_root, manifest_path, manifest = _manifest(tmp_path, names=names)
    journal = _apply(manifest_path, quarantine_root, manifest)
    batch_dir = quarantine_root / journal["batch_id"]
    collision = root / names[1]
    collision.mkdir()
    (collision / "unrelated.txt").write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(QuarantineError, match="Colisión"):
        restore_quarantine(
            batch_dir,
            approved_batch_sha256=manifest["approval"]["target_batch_sha256"],
        )

    assert not (root / names[0]).exists()
    assert (collision / "unrelated.txt").read_text(encoding="utf-8") == "do not overwrite"
    assert all((batch_dir / "items" / name).is_dir() for name in names)


def test_tampered_journal_blocks_restore(tmp_path: Path) -> None:
    _, quarantine_root, manifest_path, manifest = _manifest(tmp_path)
    journal = _apply(manifest_path, quarantine_root, manifest)
    batch_dir = quarantine_root / journal["batch_id"]
    journal_path = batch_dir / "journal.json"
    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    payload["state"] = "restored"
    journal_path.write_text(json.dumps(payload), encoding="utf-8")

    assert verify_quarantine_journal(batch_dir) is False
    with pytest.raises(QuarantineError, match="Integridad"):
        restore_quarantine(
            batch_dir,
            approved_batch_sha256=manifest["approval"]["target_batch_sha256"],
        )


def test_tampered_stored_manifest_blocks_restore(tmp_path: Path) -> None:
    _, quarantine_root, manifest_path, manifest = _manifest(tmp_path)
    journal = _apply(manifest_path, quarantine_root, manifest)
    batch_dir = quarantine_root / journal["batch_id"]
    stored_manifest = batch_dir / "approved-remediation-manifest.json"
    stored_manifest.write_text("{}\n", encoding="utf-8")

    assert verify_quarantine_journal(batch_dir) is False
    with pytest.raises(QuarantineError, match="copia aprobada"):
        restore_quarantine(
            batch_dir,
            approved_batch_sha256=manifest["approval"]["target_batch_sha256"],
        )


def test_cli_rejects_wrong_approval_and_has_no_purge_mode(tmp_path: Path) -> None:
    root, quarantine_root, manifest_path, manifest = _manifest(tmp_path)
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "quarantine_project_artifacts.py"
    batch_hash = manifest["approval"]["target_batch_sha256"]

    rejected = subprocess.run(
        [
            sys.executable,
            str(script),
            "apply",
            "--manifest",
            str(manifest_path),
            "--quarantine-root",
            str(quarantine_root),
            "--approve-manifest-sha256",
            "0" * 64,
            "--approve-batch-sha256",
            batch_hash,
            "--workers",
            "1",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode == 2
    rejected_payload = json.loads(rejected.stdout)
    assert rejected_payload["purge_authorized"] is False
    assert (root / "Demo 2").is_dir()
    assert list(quarantine_root.iterdir()) == []

    source = script.read_text(encoding="utf-8")
    assert "add_parser(\"purge\")" not in source
    assert "delete" not in source.lower()


def test_cli_apply_and_restore_fixture(tmp_path: Path) -> None:
    root, quarantine_root, manifest_path, manifest = _manifest(tmp_path)
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "quarantine_project_artifacts.py"
    batch_hash = manifest["approval"]["target_batch_sha256"]

    applied = subprocess.run(
        [
            sys.executable,
            str(script),
            "apply",
            "--manifest",
            str(manifest_path),
            "--quarantine-root",
            str(quarantine_root),
            "--approve-manifest-sha256",
            manifest["manifest_sha256"],
            "--approve-batch-sha256",
            batch_hash,
            "--workers",
            "1",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
    applied_payload = json.loads(applied.stdout)
    assert applied_payload["state"] == "quarantined"
    assert not (root / "Demo 2").exists()

    restored = subprocess.run(
        [
            sys.executable,
            str(script),
            "restore",
            "--batch-dir",
            str(quarantine_root / applied_payload["batch_id"]),
            "--approve-batch-sha256",
            batch_hash,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert json.loads(restored.stdout)["state"] == "restored"
    assert (root / "Demo 2").is_dir()
