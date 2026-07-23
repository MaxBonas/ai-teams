from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_documented_windows_entrypoints_and_templates_exist() -> None:
    for relative_path in (
        "scripts/prepare_dev_env.bat",
        "scripts/prepare_dev_env.sh",
        "scripts/dev_lifecycle.py",
        "scripts/update_windows.bat",
        "scripts/update_windows.ps1",
        "scripts/config_portability.py",
        "scripts/machine_doctor.py",
        "scripts/machine_doctor_receipt.py",
        "scripts/machine_doctor_remediate.py",
        "start_ide.bat",
        "start_ide.sh",
        "stop_ide.bat",
        "stop_ide.sh",
        "scripts/python_local.bat",
        "scripts/python_local.sh",
        "scripts/pytest_local.sh",
        "scripts/migrate_to_v2.py",
        "scripts/audit_installation_support.py",
        "scripts/accept_windows_clean_room.py",
        ".github/workflows/windows-clean-room.yml",
        "config/installation_support.v1.json",
        "config/configuration_layers.v1.json",
        "config/machine_doctor.v1.schema.json",
        "config/machine_doctor_receipt.v1.schema.json",
        "config/machine_doctor_remediation.v1.schema.json",
        "config/dev_lifecycle.v1.json",
        "config/control_plane.example.json",
        "config/agents.example.json",
    ):
        assert (ROOT / relative_path).is_file(), relative_path


def test_installation_guide_is_linked_and_does_not_overclaim_platforms() -> None:
    readme = _read("README.md")
    guide = _read("docs/INSTALLATION_AND_INTEGRATION.md")
    index = _read("docs/INDEX.md")

    assert "docs/INSTALLATION_AND_INTEGRATION.md" in readme
    assert "INSTALLATION_AND_INTEGRATION.md" in index
    assert "Verificado para control plane" in readme
    assert "no prueba conectividad, autenticación ni health" in readme
    assert "Windows x86_64 está `verified`" in guide
    assert "Linux y macOS son `planned`" in guide
    assert "no prueba conectividad, autenticación ni health" in guide
    assert "installation_support.v1.json" in readme
    assert "installation_support.v1.json" in guide
    assert "Ollama y LM Studio son opcionales" in readme
    assert "API key personal" in guide
    assert "windows-clean-room.yml" in guide
    assert "windows-clean-room-f2a20ed.json" in guide
    assert "aiteam_portable_config_v1" in guide
    assert "config_portability.py import" in guide
    assert "--apply" in guide
    assert "machine_doctor_receipt.py" in guide
    assert "machine_doctor_remediate.py" in guide
    assert "`applied=false`" in guide
    assert "config/dev_lifecycle.v1.json" in readme
    assert "sh scripts/prepare_dev_env.sh" in guide
    assert "estado preview" in guide


def test_documented_migration_validation_remains_a_dry_run() -> None:
    guide = _read("docs/INSTALLATION_AND_INTEGRATION.md")
    validation = guide.split("## Validación mínima", 1)[1].split("## Traslado", 1)[0]

    assert "scripts\\migrate_to_v2.py --json" in validation
    assert "--apply" not in validation


def test_venv_install_is_anchored_to_its_own_checkout() -> None:
    script = _read("scripts/ensure_local_venv.ps1")

    assert "Push-Location $WorkingDirectory" in script
    assert script.count("-WorkingDirectory $rootDir") == 2
