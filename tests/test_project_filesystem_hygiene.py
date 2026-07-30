from __future__ import annotations

from pathlib import Path

import pytest

from aiteam.cli import cmd_project_use, main


def test_legacy_cli_create_is_side_effect_free(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(projects_root))

    with pytest.raises(SystemExit) as exc_info:
        main(["project", "create", "Demo"])

    assert exc_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
    assert not projects_root.exists()


def test_active_workspace_source_has_no_suffix_allocator_or_tombstone() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    utils_source = (repo_root / "api" / "utils.py").read_text(encoding="utf-8")
    workspace_source = (
        repo_root / "api" / "routers" / "workspace.py"
    ).read_text(encoding="utf-8")
    app_source = (
        repo_root / "ide-frontend" / "src" / "App.tsx"
    ).read_text(encoding="utf-8")

    assert "_allocate_project_path" not in utils_source
    assert "_allocate_project_path" not in workspace_source
    assert "/api/projects/new" not in workspace_source
    assert ".aiteam-deleted-" not in workspace_source
    assert "LEGACY_PROJECT_SETUP_ENABLED" not in app_source
    assert "legacy-project-setup" not in app_source


def test_cli_use_never_initializes_personal_directory(
    tmp_path: Path,
    capsys,
) -> None:
    personal = tmp_path / "personal"
    personal.mkdir()
    marker = personal / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    exit_code = cmd_project_use(type("Args", (), {"path": str(personal)})())

    assert exit_code == 2
    assert "guided setup" in capsys.readouterr().out
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (personal / ".aiteam").exists()
