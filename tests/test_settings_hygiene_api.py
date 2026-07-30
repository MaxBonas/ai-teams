from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app


def test_settings_exposes_read_only_hygiene_without_paths_in_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_root = tmp_path / "config"
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    (projects_root / "Demo 2" / ".aiteam").mkdir(parents=True)
    (projects_root / "Demo 2" / ".aiteam" / "aiteam.db").touch()
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(config_root))
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(projects_root))
    client = TestClient(app)

    saved = client.post(
        "/api/settings",
        json={"projects_root": str(projects_root)},
    )
    loaded = client.get("/api/settings")

    assert saved.status_code == 200
    assert loaded.status_code == 200
    hygiene = loaded.json()["project_hygiene"]
    assert hygiene["status"] == "legacy_artifacts_detected"
    assert hygiene["counts"]["legacy_numbered"] == 1
    assert hygiene["lifecycle"]["doctor_can_mutate"] is False
    assert str(projects_root) not in str(hygiene)


def test_hygiene_preview_never_persists_or_creates_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_root = tmp_path / "config"
    missing = (tmp_path / "future-projects").resolve()
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(config_root))
    client = TestClient(app)

    preview = client.post(
        "/api/settings/project-hygiene/preview",
        json={"projects_root": str(missing)},
    )
    settings = client.get("/api/settings")

    assert preview.status_code == 200
    assert preview.json()["persisted"] is False
    assert preview.json()["project_hygiene"]["status"] == "root_missing"
    assert not missing.exists()
    assert settings.json()["projects_root"] == ""


def test_hygiene_preview_rejects_relative_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "config"))
    client = TestClient(app)

    response = client.post(
        "/api/settings/project-hygiene/preview",
        json={"projects_root": "relative/projects"},
    )

    assert response.status_code == 400


def test_environment_projects_root_counts_as_configured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(projects_root))
    client = TestClient(app)

    response = client.get("/api/settings")

    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["projects_root_source"] == "environment"
    assert response.json()["project_hygiene"]["status"] == "clean"


def test_updating_root_preserves_preferences_and_adapter_storage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_root = tmp_path / "config"
    config_root.mkdir()
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_root.mkdir()
    new_root.mkdir()
    settings_path = config_root / "settings.json"
    adapter_path = config_root / "adapter_profiles.json"
    settings_path.write_text(
        json.dumps({"projects_root": str(old_root), "theme": "dark"}),
        encoding="utf-8",
    )
    adapter_path.write_text(
        json.dumps({"profiles": [{"id": "kept-adapter"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(config_root))
    monkeypatch.delenv("AITEAM_PROJECTS_ROOT", raising=False)
    client = TestClient(app)

    response = client.post("/api/settings", json={"projects_root": str(new_root)})

    assert response.status_code == 200
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {
        "projects_root": str(new_root),
        "theme": "dark",
    }
    assert json.loads(adapter_path.read_text(encoding="utf-8")) == {
        "profiles": [{"id": "kept-adapter"}],
    }
