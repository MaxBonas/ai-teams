from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiteam.machine_doctor import build_machine_inventory
from aiteam.machine_doctor_receipt import (
    build_machine_doctor_receipt,
    build_remediation_plan,
    canonical_sha256,
    validate_machine_doctor_receipt,
    validate_remediation_plan,
    write_explicit_receipt,
)


ROOT = Path(__file__).resolve().parents[1]


def _report(root: Path) -> dict:
    return build_machine_inventory(
        root=root,
        command_probe=lambda command: (True, "99.0.0"),
        port_probe=lambda port: "not_listening",
        host=("linux", "x86_64", "test-release"),
        adapter_profiles=[],
    )


def test_receipt_is_reproducible_and_discovery_keeps_surfaces_unchanged(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    config = tmp_path / "config"
    checkout.mkdir()
    config.mkdir()
    (checkout / "README.md").write_text("fixture\n", encoding="utf-8")
    (config / "adapter_health.json").write_text("{}\n", encoding="utf-8")

    kwargs = {
        "root": checkout,
        "config_root": config,
        "inventory_builder": lambda: _report(checkout),
        "cli_snapshot": lambda: {"codex_subscription": True},
    }
    first = build_machine_doctor_receipt(**kwargs)
    second = build_machine_doctor_receipt(**kwargs)

    assert first == second
    assert first["mutation_guard"]["verified"] is True
    assert first["report_sha256"] == canonical_sha256(first["report"])
    assert first["receipt_id"] == second["receipt_id"]
    assert first["contract"]["secret_contents_read_by_guard"] is False


def test_receipt_guard_detects_a_discovery_write(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    config = tmp_path / "config"
    checkout.mkdir()
    config.mkdir()

    def mutating_inventory() -> dict:
        (checkout / "unexpected.txt").write_text("mutation", encoding="utf-8")
        return _report(checkout)

    receipt = build_machine_doctor_receipt(
        root=checkout,
        config_root=config,
        inventory_builder=mutating_inventory,
        cli_snapshot=lambda: {},
    )

    assert receipt["mutation_guard"]["verified"] is False
    assert receipt["mutation_guard"]["surfaces"]["checkout"]["unchanged"] is False


def test_metadata_guard_does_not_read_secret_file_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    config = tmp_path / "config"
    checkout.mkdir()
    config.mkdir()
    secret = config / "secrets.json"
    secret.write_text('{"token":"do-not-read"}', encoding="utf-8")
    original = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):
        if path == secret:
            raise AssertionError("secret contents were read")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    receipt = build_machine_doctor_receipt(
        root=checkout,
        config_root=config,
        inventory_builder=lambda: _report(checkout),
        cli_snapshot=lambda: {},
    )

    assert receipt["mutation_guard"]["verified"] is True


def test_receipt_validation_rejects_tampering(tmp_path: Path) -> None:
    receipt = build_machine_doctor_receipt(
        root=tmp_path,
        config_root=tmp_path,
        inventory_builder=lambda: _report(tmp_path),
        cli_snapshot=lambda: {},
    )
    receipt["report"]["summary"]["status"] = "ready"

    with pytest.raises(ValueError):
        validate_machine_doctor_receipt(receipt)


def test_explicit_receipt_write_requires_parent_and_overwrite_consent(
    tmp_path: Path,
) -> None:
    payload = {"schema_version": "fixture"}
    missing_parent = tmp_path / "missing" / "receipt.json"
    target = tmp_path / "receipt.json"

    with pytest.raises(ValueError, match="parent directory"):
        write_explicit_receipt(missing_parent, payload)
    write_explicit_receipt(target, payload)
    with pytest.raises(FileExistsError, match="--force"):
        write_explicit_receipt(target, payload)
    write_explicit_receipt(target, {"schema_version": "replacement"}, overwrite=True)

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "schema_version": "replacement"
    }
    assert not (tmp_path / ".receipt.json.tmp").exists()


def test_remediation_is_hash_bound_reproducible_and_never_applied(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)

    first = build_remediation_plan(
        report,
        action_code="verify_primary_adapter",
    )
    second = build_remediation_plan(
        report,
        action_code="verify_primary_adapter",
    )

    assert first == second
    assert first["report_sha256"] == canonical_sha256(report)
    assert first["mode"] == "guided_manual"
    assert first["applied"] is False
    assert first["execution"]["status"] == "not_executed"
    assert first["action"]["targets"] == [
        {
            "subject_kind": "system",
            "subject_id": "primary_adapter",
            "diagnostic_code": "primary_adapter_not_ready",
        }
    ]
    validate_remediation_plan(first)


def test_remediation_rejects_actions_not_present_in_report(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not present"):
        build_remediation_plan(
            _report(tmp_path),
            action_code="install_everything",
        )


def test_remediation_boundary_has_no_process_execution_entrypoint() -> None:
    module = (ROOT / "aiteam" / "machine_doctor_receipt.py").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "scripts" / "machine_doctor_remediate.py").read_text(
        encoding="utf-8"
    )

    assert "subprocess" not in module
    assert "run_command" not in module
    assert "subprocess" not in script
    assert "--apply" not in script
