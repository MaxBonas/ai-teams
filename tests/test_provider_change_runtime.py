from __future__ import annotations

from pathlib import Path

import aiteam.provider_change_runtime as runtime
from aiteam.db.provider_changes import (
    provider_change_schedule_summary,
    register_provider_change_schedules,
    run_scheduled_provider_checks,
)


def test_safe_runtime_registers_full_inventory_but_only_local_readers() -> None:
    components, readers = runtime.build_safe_provider_change_runtime(
        command_probe=lambda _command: (False, None),
        codex_catalog_reader=lambda: {"status": "not_authenticated"},
    )

    assert len(components) == 42
    assert len(readers) == 23
    assert {
        components[key]["surface"] for key in readers
    } == {"cli_package", "internal_adapter", "model_catalog"}
    assert sum(
        components[key]["surface"] == "model_catalog" for key in readers
    ) == 1
    assert all(
        components[key]["surface"] not in {"sdk_api", "mcp_server"}
        for key in readers
    )


def test_cli_reader_observes_local_version_without_latest_probe(
    monkeypatch,
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(
        runtime,
        "resolve_provider_cli",
        lambda _cli_id, _commands: "C:/tools/codex.cmd",
    )
    components, readers = runtime.build_safe_provider_change_runtime(
        command_probe=lambda command: (
            seen.append(command) is None,
            "codex-cli 1.2.3",
        ),
        codex_catalog_reader=lambda: {"status": "not_authenticated"},
    )
    key = next(
        key
        for key, component in components.items()
        if component["component_id"] == "cli:codex"
    )

    observation = readers[key]()

    assert seen == [["C:/tools/codex.cmd", "--version"]]
    assert observation["installed_version"] == "codex-cli 1.2.3"
    assert observation["latest_known_version"] is None
    assert observation["compatibility"]["installed"] == "unknown"


def test_codex_catalog_reader_maps_current_models_without_inference() -> None:
    components, readers = runtime.build_safe_provider_change_runtime(
        command_probe=lambda _command: (False, None),
        codex_catalog_reader=lambda: {
            "status": "current",
            "catalog_client_version": "0.140.0",
            "models": [{"id": "gpt-example"}, {"value": "gpt-second"}],
        },
    )
    key = next(
        key
        for key, component in components.items()
        if component["component_id"] == "catalog:codex_subscription"
    )

    observation = readers[key]()

    assert observation["installed_version"] == "0.140.0"
    assert observation["dimensions"]["model_id"] == [
        {"id": "gpt-example"},
        {"id": "gpt-second"},
    ]


def test_safe_scheduler_tick_persists_state_in_machine_database(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "guided_setup.db"
    components, readers = runtime.build_safe_provider_change_runtime(
        command_probe=lambda _command: (False, None),
        codex_catalog_reader=lambda: {"status": "not_authenticated"},
    )
    registration = register_provider_change_schedules(
        db_path,
        list(components.values()),
    )

    result = run_scheduled_provider_checks(
        db_path,
        components,
        readers,
        max_checks=3,
    )
    summary = provider_change_schedule_summary(db_path)

    assert registration["registered"] == 42
    assert len(result) == 3
    assert all(row["probe_status"] != "failed" for row in result)
    assert summary["initialized"] is True
    assert summary["counts"]["total"] == 42
    assert summary["read_only"] is True
