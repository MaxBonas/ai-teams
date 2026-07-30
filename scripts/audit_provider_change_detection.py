"""Matriz hermética para probes y diffs semánticos P0.N.2."""

from __future__ import annotations

import argparse
import json
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_detection = import_module("aiteam.provider_change_detection")
_intelligence = import_module("aiteam.provider_change_intelligence")
build_provider_snapshot = _detection.build_provider_snapshot
compare_provider_snapshots = _detection.compare_provider_snapshots
build_provider_change_inventory = (
    _intelligence.build_provider_change_inventory
)

NOW = "2026-07-30T12:00:00+00:00"
LATER = "2026-07-30T13:00:00+00:00"


def build_report() -> dict[str, Any]:
    cases = _cases()
    rows: list[dict[str, Any]] = []
    for case in cases:
        previous = _snapshot(
            case["surface"], case.get("before") or {}, observed_at=NOW
        )
        current = _snapshot(
            case["surface"], case.get("after") or {}, observed_at=LATER
        )
        diff = compare_provider_snapshots(previous, current)
        kinds = {row["kind"] for row in diff["changes"]}
        expected_kinds = set(case.get("expected_kinds") or [])
        rows.append(
            {
                "id": case["id"],
                "surface": case["surface"],
                "expected_decision": case["expected_decision"],
                "observed_decision": diff["summary"]["decision"],
                "expected_kinds_present": expected_kinds <= kinds,
                "routing_change_allowed": diff["summary"][
                    "routing_change_allowed"
                ],
                "automatic_update_allowed": diff["summary"][
                    "automatic_update_allowed"
                ],
                "diff_sha256": diff["diff_sha256"],
                "ok": (
                    diff["summary"]["decision"]
                    == case["expected_decision"]
                    and expected_kinds <= kinds
                    and diff["summary"]["routing_change_allowed"] is False
                    and diff["summary"]["automatic_update_allowed"] is False
                ),
            }
        )
    checks = {
        "fixture_matrix_green": all(row["ok"] for row in rows),
        "all_surfaces_exercised": {row["surface"] for row in rows}
        == {
            "cli_package",
            "mcp_server",
            "sdk_api",
            "internal_adapter",
            "model_catalog",
        },
        "unavailable_is_unknown": all(
            row["observed_decision"] == "unknown"
            for row in rows
            if row["id"] in {"offline", "rate_limited", "auth_required"}
        ),
        "release_is_not_automatic": all(
            row["automatic_update_allowed"] is False for row in rows
        ),
        "discovery_never_changes_routing": all(
            row["routing_change_allowed"] is False for row in rows
        ),
        "model_lifecycle_coverage": all(
            next(row for row in rows if row["id"] == case_id)["ok"]
            for case_id in (
                "model_added",
                "model_removed",
                "model_renamed",
                "model_metadata",
            )
        ),
        "protocol_and_schema_coverage": all(
            next(row for row in rows if row["id"] == case_id)["ok"]
            for case_id in ("mcp_tools", "api_schema", "adapter_contract")
        ),
        "diffs_are_sha_bound": len(
            {row["diff_sha256"] for row in rows}
        )
        == len(rows),
    }
    return {
        "schema_version": "provider_change_detection_audit_v1",
        "contract_schema_version": "provider_change_intelligence_v1",
        "snapshot_schema_version": _detection.SNAPSHOT_SCHEMA,
        "diff_schema_version": _detection.DIFF_SCHEMA,
        "rows": rows,
        "checks": checks,
        "summary": {
            "cases": len(rows),
            "passed": sum(row["ok"] for row in rows),
            "checks_passed": sum(checks.values()),
            "checks_total": len(checks),
            "detectors_ready": all(checks.values()),
        },
        "scope": {
            "fixtures_only": True,
            "read_only": True,
            "network_attempted": False,
            "secrets_read": False,
            "login_attempted": False,
            "inference_attempted": False,
            "updates_attempted": False,
        },
    }


def _snapshot(
    surface: str,
    changes: dict[str, Any],
    *,
    observed_at: str,
) -> dict[str, Any]:
    component = next(
        row
        for row in build_provider_change_inventory()["components"]
        if row["surface"] == surface
    )
    observation = {
        "status": "observed",
        "installed_version": "1.0.0",
        "latest_known_version": "1.0.0",
        "compatibility": {},
        "lifecycle": {},
        "dimensions": {},
        **changes,
    }
    return build_provider_snapshot(
        component, observation, observed_at=observed_at
    )


def _model(model_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "id": model_id,
        "aliases": [],
        "context": 1000,
        "tools": False,
        "structured_output": True,
        "price": "1",
        "quota": "standard",
        "lifecycle": "active",
        **changes,
    }


def _cases() -> list[dict[str, Any]]:
    return [
        {
            "id": "no_change",
            "surface": "cli_package",
            "expected_decision": "none",
        },
        {
            "id": "newer_available",
            "surface": "cli_package",
            "after": {"latest_known_version": "1.1.0"},
            "expected_decision": "newer_available",
            "expected_kinds": ["newer_available"],
        },
        {
            "id": "update_recommended",
            "surface": "cli_package",
            "after": {
                "latest_known_version": "1.1.0",
                "compatibility": {"latest_known": "compatible"},
            },
            "expected_decision": "update_recommended",
        },
        {
            "id": "update_required",
            "surface": "cli_package",
            "after": {
                "latest_known_version": "1.1.0",
                "compatibility": {"latest_known": "compatible"},
                "lifecycle": {"latest_known": "required"},
            },
            "expected_decision": "update_required",
        },
        {
            "id": "installed_incompatible",
            "surface": "cli_package",
            "after": {
                "compatibility": {"installed": "incompatible"}
            },
            "expected_decision": "blocked",
            "expected_kinds": ["installed_incompatible"],
        },
        {
            "id": "installed_deprecated",
            "surface": "cli_package",
            "after": {"lifecycle": {"installed": "deprecated"}},
            "expected_decision": "update_required",
            "expected_kinds": ["installed_deprecated"],
        },
        {
            "id": "installed_retired",
            "surface": "cli_package",
            "after": {"lifecycle": {"installed": "retired"}},
            "expected_decision": "blocked",
            "expected_kinds": ["installed_retired"],
        },
        {
            "id": "prerelease",
            "surface": "cli_package",
            "after": {
                "installed_version": "1.1.0-beta.1",
                "latest_known_version": "1.1.0-beta.1",
            },
            "expected_decision": "newer_available",
            "expected_kinds": ["prerelease_installed"],
        },
        {
            "id": "downgrade",
            "surface": "cli_package",
            "before": {
                "installed_version": "1.1.0",
                "latest_known_version": "1.1.0",
            },
            "after": {
                "installed_version": "1.0.0",
                "latest_known_version": "1.1.0",
            },
            "expected_decision": "newer_available",
            "expected_kinds": ["installed_downgraded"],
        },
        {
            "id": "offline",
            "surface": "cli_package",
            "after": {"status": "offline"},
            "expected_decision": "unknown",
        },
        {
            "id": "rate_limited",
            "surface": "sdk_api",
            "after": {"status": "rate_limited"},
            "expected_decision": "unknown",
        },
        {
            "id": "auth_required",
            "surface": "model_catalog",
            "after": {"status": "auth_required"},
            "expected_decision": "unknown",
        },
        {
            "id": "mcp_tools",
            "surface": "mcp_server",
            "before": {"dimensions": {"tools": ["read"]}},
            "after": {"dimensions": {"tools": ["read", "write"]}},
            "expected_decision": "blocked",
            "expected_kinds": ["mcp_server_tools_changed"],
        },
        {
            "id": "api_schema",
            "surface": "sdk_api",
            "before": {
                "dimensions": {
                    "endpoint": "/v1",
                    "auth_schema": "bearer",
                    "request_schema": "v1",
                }
            },
            "after": {
                "dimensions": {
                    "endpoint": "/v2",
                    "auth_schema": "oauth",
                    "request_schema": "v2",
                }
            },
            "expected_decision": "blocked",
            "expected_kinds": [
                "sdk_api_endpoint_changed",
                "sdk_api_auth_schema_changed",
                "sdk_api_request_schema_changed",
            ],
        },
        {
            "id": "adapter_contract",
            "surface": "internal_adapter",
            "before": {
                "dimensions": {"translation_contract": "v1"}
            },
            "after": {
                "dimensions": {"translation_contract": "v2"}
            },
            "expected_decision": "update_required",
        },
        {
            "id": "model_added",
            "surface": "model_catalog",
            "before": {"dimensions": {"model_id": []}},
            "after": {
                "dimensions": {"model_id": [_model("model-new")]}
            },
            "expected_decision": "newer_available",
            "expected_kinds": ["model_added"],
        },
        {
            "id": "model_removed",
            "surface": "model_catalog",
            "before": {
                "dimensions": {"model_id": [_model("model-old")]}
            },
            "after": {"dimensions": {"model_id": []}},
            "expected_decision": "blocked",
            "expected_kinds": ["model_removed"],
        },
        {
            "id": "model_renamed",
            "surface": "model_catalog",
            "before": {
                "dimensions": {"model_id": [_model("model-old")]}
            },
            "after": {
                "dimensions": {
                    "model_id": [
                        _model("model-new", aliases=["model-old"])
                    ]
                }
            },
            "expected_decision": "newer_available",
            "expected_kinds": ["model_renamed"],
        },
        {
            "id": "model_metadata",
            "surface": "model_catalog",
            "before": {
                "dimensions": {"model_id": [_model("model")]}
            },
            "after": {
                "dimensions": {
                    "model_id": [
                        _model(
                            "model",
                            context=2000,
                            tools=True,
                            structured_output=False,
                            price="2",
                            quota="reduced",
                            lifecycle="deprecated",
                        )
                    ]
                }
            },
            "expected_decision": "blocked",
            "expected_kinds": [
                "model_context_changed",
                "model_tools_changed",
                "model_structured_output_changed",
                "model_price_changed",
                "model_quota_changed",
                "model_lifecycle_changed",
            ],
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audita los detectores semánticos provider-neutral."
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_report()
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    ready = report["summary"]["detectors_ready"]
    return 0 if ready or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
