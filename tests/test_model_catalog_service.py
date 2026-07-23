from __future__ import annotations

from pathlib import Path

from aiteam import model_catalog_service as service


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
