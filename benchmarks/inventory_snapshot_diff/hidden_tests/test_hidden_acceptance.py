from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

import inventory_diff


def item(sku: str, quantity: int, price: int, tags: list[str] | None = None) -> dict:
    return {"sku": sku, "quantity": quantity, "price_cents": price, "tags": tags or []}


def snapshot(*items: dict) -> dict:
    return {"items": list(items)}


def test_reconciles_all_categories_and_summary() -> None:
    previous = snapshot(item("b", 4, 200, ["sale"]), item("a", 1, 100), item("gone", 5, 50))
    current = snapshot(item("new", 3, 90), item("a", 1, 100), item("b", 7, 250, ["featured"]))
    result = inventory_diff.reconcile_inventory(previous, current)
    assert result["added"] == [item("new", 3, 90)]
    assert result["removed"] == [item("gone", 5, 50)]
    assert result["unchanged"] == ["a"]
    assert result["changed"] == [{
        "sku": "b",
        "changes": {
            "quantity": {"before": 4, "after": 7},
            "price_cents": {"before": 200, "after": 250},
            "tags": {"before": ["sale"], "after": ["featured"]},
        },
    }]
    assert result["summary"] == {"added": 1, "removed": 1, "changed": 1, "unchanged": 1, "quantity_delta": 1}


def test_tags_are_sets_for_comparison_and_sorted_for_output() -> None:
    previous = snapshot(item("sku", 1, 10, ["z", "a"]))
    current = snapshot(item("sku", 1, 10, ["a", "z"]))
    result = inventory_diff.reconcile_inventory(previous, current)
    assert result["changed"] == []
    assert result["unchanged"] == ["sku"]


def test_outputs_are_sorted_and_deterministic() -> None:
    previous = snapshot(item("z", 1, 1), item("b", 1, 1))
    current = snapshot(item("c", 1, 1), item("a", 1, 1))
    first = inventory_diff.reconcile_inventory(previous, current)
    second = inventory_diff.reconcile_inventory(previous, current)
    assert first == second
    assert [row["sku"] for row in first["added"]] == ["a", "c"]
    assert [row["sku"] for row in first["removed"]] == ["b", "z"]


def test_does_not_mutate_inputs() -> None:
    previous = snapshot(item("a", 1, 10, ["z", "a"]))
    current = snapshot(item("a", 2, 10, ["a", "z"]))
    before_previous = copy.deepcopy(previous)
    before_current = copy.deepcopy(current)
    inventory_diff.reconcile_inventory(previous, current)
    assert previous == before_previous
    assert current == before_current


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {},
        {"items": "not-a-list"},
        snapshot({"sku": "a", "quantity": 1, "price_cents": 2}),
        snapshot({"sku": "a", "quantity": 1, "price_cents": 2, "tags": [], "extra": True}),
        snapshot(item("", 1, 2)),
        snapshot(item("a", True, 2)),
        snapshot(item("a", -1, 2)),
        snapshot(item("a", 1, -2)),
        snapshot(item("a", 1, 2, ["x", "x"])),
        snapshot(item("a", 1, 2, [""])),
    ],
)
def test_rejects_invalid_snapshot_shapes(bad: object) -> None:
    with pytest.raises(ValueError):
        inventory_diff.reconcile_inventory(bad, snapshot())


def test_rejects_duplicate_skus() -> None:
    with pytest.raises(ValueError):
        inventory_diff.reconcile_inventory(snapshot(item("a", 1, 2), item("a", 3, 4)), snapshot())


def test_quantity_delta_covers_added_removed_and_changed() -> None:
    previous = snapshot(item("removed", 5, 1), item("changed", 2, 1))
    current = snapshot(item("added", 10, 1), item("changed", 4, 1))
    assert inventory_diff.reconcile_inventory(previous, current)["summary"]["quantity_delta"] == 7


def test_changed_fields_exclude_unchanged_values() -> None:
    result = inventory_diff.reconcile_inventory(
        snapshot(item("a", 1, 10, ["x"])),
        snapshot(item("a", 2, 10, ["x"])),
    )
    assert result["changed"] == [{"sku": "a", "changes": {"quantity": {"before": 1, "after": 2}}}]


def test_cli_writes_stable_utf8_json(tmp_path: Path) -> None:
    previous = tmp_path / "previous.json"
    current = tmp_path / "current.json"
    target = tmp_path / "diff.json"
    previous.write_text(json.dumps(snapshot(item("á", 1, 10)), ensure_ascii=False), encoding="utf-8")
    current.write_text(json.dumps(snapshot(item("á", 2, 10)), ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(Path(inventory_diff.__file__)), str(previous), str(current), str(target)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    raw = target.read_bytes()
    assert raw.endswith(b"\n")
    decoded = json.loads(raw.decode("utf-8"))
    assert decoded["changed"][0]["sku"] == "á"
    assert decoded["summary"]["changed"] == 1


def test_cli_failure_leaves_no_partial_output(tmp_path: Path) -> None:
    previous = tmp_path / "bad.json"
    current = tmp_path / "current.json"
    target = tmp_path / "diff.json"
    previous.write_text("{bad-json", encoding="utf-8")
    current.write_text(json.dumps(snapshot()), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(Path(inventory_diff.__file__)), str(previous), str(current), str(target)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert not target.exists()
