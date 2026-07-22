from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.audit_parallel_trigger_inventory import (
    discover_databases,
    evaluate_trigger_source,
    main,
)


def _source(**overrides: object) -> dict[str, object]:
    return {
        "source_kind": "live_canary",
        "evidence_quality": "exact",
        "root_count": 3,
        "capacity_pool_count": 3,
        "parallelizable_wait_runs": 1,
        "parallelizable_wait_seconds": 2.5,
        **overrides,
    }


def test_exact_live_multi_root_pool_wait_satisfies_trigger() -> None:
    assert evaluate_trigger_source(_source()) == []


def test_approximate_signal_never_satisfies_trigger() -> None:
    assert evaluate_trigger_source(_source(evidence_quality="approximate")) == [
        "evidence_not_exact"
    ]


def test_trigger_rejects_missing_shape_and_positive_wait() -> None:
    assert evaluate_trigger_source(_source(
        root_count=1,
        capacity_pool_count=1,
        parallelizable_wait_runs=0,
        parallelizable_wait_seconds=0,
    )) == [
        "fewer_than_two_roots",
        "fewer_than_two_capacity_pools",
        "no_positive_parallelizable_wait",
    ]


def test_hermetic_adapter_never_counts_as_live_trigger() -> None:
    assert evaluate_trigger_source(
        _source(source_kind="benchmark"),
        contains_hermetic_adapters=True,
    ) == ["synthetic_hermetic_source"]


def test_discovery_prunes_ephemeral_test_directories(tmp_path: Path) -> None:
    retained = tmp_path / "project" / ".aiteam" / "aiteam.db"
    retained.parent.mkdir(parents=True)
    retained.touch()
    ephemeral = tmp_path / "tmp-stale" / "hidden.db"
    ephemeral.parent.mkdir()
    ephemeral.touch()

    databases, errors, pruned = discover_databases(tmp_path)

    assert databases == [retained]
    assert errors == []
    assert len(pruned) == 1
    assert pruned[0].endswith("tmp-stale")


def test_require_trigger_cli_fails_closed_without_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(sys, "argv", [
        "audit_parallel_trigger_inventory.py",
        "--root",
        str(tmp_path),
        "--output",
        str(output),
        "--require-trigger",
    ])

    assert main() == 1
    assert json.loads(output.read_text(encoding="utf-8"))["conclusion"]["live_ab_allowed"] is False
