from __future__ import annotations

from pathlib import Path

import pytest

from aiteam import model_catalog_service as service
from aiteam.model_owner_preferences import ModelOwnerPreferencesError


def test_catalog_cache_tracks_machine_config_and_can_be_invalidated(
    tmp_path: Path, monkeypatch
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    settings = config_dir / "profiles.json"
    settings.write_text("{}", encoding="utf-8")
    calls: list[tuple[Path, ...]] = []

    def fake_build(*, db_paths=()):
        calls.append(tuple(db_paths))
        return {"generation": len(calls)}

    monkeypatch.setattr(service, "user_config_dir", lambda: config_dir)
    monkeypatch.setattr(service, "build_current_model_catalog_read_model", fake_build)
    service.invalidate_model_catalog_cache()

    first = service.get_current_model_catalog(max_age_seconds=60)
    second = service.get_current_model_catalog(max_age_seconds=60)
    assert first == second
    assert first is not second
    assert len(calls) == 1

    first["generation"] = 999
    assert service.get_current_model_catalog(max_age_seconds=60)["generation"] == 1

    settings.write_text('{"profile": "changed"}', encoding="utf-8")
    refreshed = service.get_current_model_catalog(max_age_seconds=60)
    assert refreshed["generation"] == 2

    service.invalidate_model_catalog_cache()
    third = service.get_current_model_catalog(max_age_seconds=60)
    assert third["generation"] == 3
    assert len(calls) == 3


def test_catalog_cache_key_changes_with_owner_preferences(
    tmp_path: Path, monkeypatch
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(service, "user_config_dir", lambda: config_dir)

    before = service._cache_key(())
    preference_path = config_dir / "model_owner_preferences.json"
    preference_path.write_text('{"state":"archived"}', encoding="utf-8")
    after = service._cache_key(())

    assert after != before


def test_corrupt_owner_preferences_never_reuse_a_stale_catalog(
    tmp_path: Path, monkeypatch
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    calls = 0

    def fake_build(*, db_paths=()):
        nonlocal calls
        calls += 1
        preference_path = config_dir / "model_owner_preferences.json"
        if preference_path.exists():
            raise ModelOwnerPreferencesError("corrupt owner preferences")
        return {"generation": calls}

    monkeypatch.setattr(service, "user_config_dir", lambda: config_dir)
    monkeypatch.setattr(service, "build_current_model_catalog_read_model", fake_build)
    service.invalidate_model_catalog_cache()

    assert service.get_current_model_catalog(max_age_seconds=60) == {"generation": 1}
    (config_dir / "model_owner_preferences.json").write_text(
        '{"schema_version":"broken"}', encoding="utf-8"
    )

    with pytest.raises(
        ModelOwnerPreferencesError, match="corrupt owner preferences"
    ):
        service.get_current_model_catalog(max_age_seconds=60)
    assert calls == 2


def test_catalog_rebuild_records_maintenance_for_existing_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    settings = config_dir / "profiles.json"
    settings.write_text("{}", encoding="utf-8")
    db_path = tmp_path / "aiteam.db"
    db_path.touch()
    calls: list[tuple[Path, str]] = []
    projection = {
        "content_hash": "a" * 64,
        "candidates": [],
    }

    monkeypatch.setattr(service, "user_config_dir", lambda: config_dir)
    monkeypatch.setattr(
        service,
        "build_current_model_catalog_read_model",
        lambda **_kwargs: projection,
    )
    monkeypatch.setattr(
        service,
        "reconcile_model_catalog_maintenance",
        lambda path, read_model: calls.append(
            (Path(path), str(read_model["content_hash"]))
        ),
    )
    service.invalidate_model_catalog_cache()

    service.get_current_model_catalog(db_paths=(db_path,), max_age_seconds=60)
    service.get_current_model_catalog(db_paths=(db_path,), max_age_seconds=60)
    assert calls == [(db_path, "a" * 64)]

    settings.write_text('{"changed":true}', encoding="utf-8")
    service.get_current_model_catalog(db_paths=(db_path,), max_age_seconds=60)
    assert calls == [(db_path, "a" * 64), (db_path, "a" * 64)]


def test_maintenance_failure_never_blocks_catalog_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "aiteam.db"
    db_path.touch()
    projection = {
        "content_hash": "a" * 64,
        "candidates": [],
        "schema_version": "model_catalog_read_model_v2",
    }

    monkeypatch.setattr(service, "user_config_dir", lambda: config_dir)
    monkeypatch.setattr(
        service,
        "build_current_model_catalog_read_model",
        lambda **_kwargs: projection,
    )
    monkeypatch.setattr(
        service,
        "reconcile_model_catalog_maintenance",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("fixture telemetry failure")
        ),
    )
    service.invalidate_model_catalog_cache()

    assert service.get_current_model_catalog(
        db_paths=(db_path,),
        max_age_seconds=60,
    ) == projection
