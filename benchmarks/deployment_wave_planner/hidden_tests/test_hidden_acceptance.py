from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

import deployment_planner


def service(name: str, region: str, risk: str = "low", deps: list[str] | None = None) -> dict:
    return {"name": name, "region": region, "risk": risk, "depends_on": deps or []}


def test_plans_dependencies_regions_risk_and_metrics() -> None:
    manifest = {
        "max_parallel": 3,
        "services": [
            service("db", "eu", "high"),
            service("api", "eu", "medium", ["db"]),
            service("worker", "us", "medium", ["db"]),
            service("web", "eu", "low", ["api"]),
            service("metrics", "ap", "low"),
        ],
    }
    assert deployment_planner.plan_deployment(manifest) == {
        "waves": [
            {"ordinal": 1, "services": ["db"]},
            {"ordinal": 2, "services": ["api", "worker", "metrics"]},
            {"ordinal": 3, "services": ["web"]},
        ],
        "rollback_order": ["web", "metrics", "worker", "api", "db"],
        "critical_path_length": 3,
    }


def test_greedy_region_collision_waits_for_next_wave() -> None:
    manifest = {
        "max_parallel": 3,
        "services": [
            service("a", "eu", "medium"),
            service("b", "eu", "medium"),
            service("c", "us", "low"),
        ],
    }
    assert deployment_planner.plan_deployment(manifest)["waves"] == [
        {"ordinal": 1, "services": ["a", "c"]},
        {"ordinal": 2, "services": ["b"]},
    ]


def test_high_risk_candidate_is_always_isolated() -> None:
    manifest = {
        "max_parallel": 4,
        "services": [service("safe", "eu"), service("critical", "us", "high")],
    }
    assert deployment_planner.plan_deployment(manifest)["waves"] == [
        {"ordinal": 1, "services": ["critical"]},
        {"ordinal": 2, "services": ["safe"]},
    ]


@pytest.mark.parametrize(
    "manifest",
    [
        {"max_parallel": 0, "services": [service("a", "eu")]},
        {"max_parallel": True, "services": [service("a", "eu")]},
        {"max_parallel": 1, "services": []},
        {"max_parallel": 1, "services": [service("a", "eu"), service("a", "us")]},
        {"max_parallel": 1, "services": [service("a", "eu", deps=["missing"])]},
        {"max_parallel": 1, "services": [service("a", "eu", deps=["a"])]},
        {"max_parallel": 1, "services": [service("a", "eu", "urgent")]},
        {"max_parallel": 1, "services": [service("a", "eu", deps=["b", "b"]), service("b", "us")]},
    ],
)
def test_rejects_invalid_manifests(manifest: dict) -> None:
    with pytest.raises(ValueError):
        deployment_planner.plan_deployment(manifest)


def test_rejects_indirect_cycle() -> None:
    manifest = {
        "max_parallel": 2,
        "services": [service("a", "eu", deps=["b"]), service("b", "us", deps=["c"]), service("c", "ap", deps=["a"])],
    }
    with pytest.raises(ValueError):
        deployment_planner.plan_deployment(manifest)


def test_does_not_mutate_input_and_is_deterministic() -> None:
    manifest = {"max_parallel": 2, "services": [service("z", "us"), service("a", "eu")]}
    original = copy.deepcopy(manifest)
    first = deployment_planner.plan_deployment(manifest)
    second = deployment_planner.plan_deployment(manifest)
    assert manifest == original
    assert first == second
    assert first["waves"][0]["services"] == ["a", "z"]


def test_critical_path_counts_services() -> None:
    manifest = {
        "max_parallel": 2,
        "services": [
            service("a", "eu"),
            service("b", "us", deps=["a"]),
            service("c", "ap", deps=["b"]),
            service("d", "sa", deps=["a"]),
        ],
    }
    assert deployment_planner.plan_deployment(manifest)["critical_path_length"] == 3


def test_cli_writes_stable_json_and_newline(tmp_path: Path) -> None:
    source = tmp_path / "manifest.json"
    target = tmp_path / "plan.json"
    source.write_text(json.dumps({"max_parallel": 1, "services": [service("api-á", "eu")]}), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(Path(deployment_planner.__file__)), str(source), str(target)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    raw = target.read_bytes()
    assert raw.endswith(b"\n")
    assert json.loads(raw.decode("utf-8"))["waves"][0]["services"] == ["api-á"]


def test_cli_failure_leaves_no_partial_output(tmp_path: Path) -> None:
    source = tmp_path / "bad.json"
    target = tmp_path / "plan.json"
    source.write_text("{not-json", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(Path(deployment_planner.__file__)), str(source), str(target)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert not target.exists()
