"""Audita el contrato N.1 sin red, login, secretos ni inferencias."""

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

_intelligence = import_module("aiteam.provider_change_intelligence")
build_provider_change_inventory = _intelligence.build_provider_change_inventory
load_provider_change_contract = _intelligence.load_provider_change_contract
validate_provider_change_inventory = (
    _intelligence.validate_provider_change_inventory
)
DEFAULT_ADAPTER_PROFILES = import_module(
    "aiteam.user_config"
).DEFAULT_ADAPTER_PROFILES


def build_report() -> dict[str, Any]:
    contract = load_provider_change_contract()
    inventory = build_provider_change_inventory(contract=contract)
    validate_provider_change_inventory(inventory, contract=contract)
    components = inventory["components"]
    profile_ids = {
        str(row["profile_id"])
        for row in components
        if row["profile_id"] is not None
    }
    surfaces = {str(row["surface"]) for row in components}
    facts = [
        fact
        for row in components
        for fact in row["facts"].values()
    ]
    checks = {
        "all_builtin_profiles_covered": profile_ids
        == {str(row["id"]) for row in DEFAULT_ADAPTER_PROFILES},
        "all_surfaces_covered": surfaces == set(contract["surfaces"]),
        "all_facts_have_provenance": all(
            bool(str(fact["source"]["reference"]).strip()) for fact in facts
        ),
        "unknown_is_explicit": any(
            fact["state"] == "unknown" for fact in facts
        ),
        "unknown_does_not_claim_observation": all(
            fact["source"]["observed_at"] is None
            for fact in facts
            if fact["state"] == "unknown"
        ),
        "discovery_grants_no_routing_authority": (
            inventory["scope"]["routing_authority_granted"] is False
        ),
        "read_only_and_secret_free": (
            inventory["scope"]["read_only"] is True
            and inventory["scope"]["secrets_read"] is False
            and inventory["scope"]["network_attempted"] is False
        ),
    }
    return {
        "schema_version": "provider_change_contract_audit_v1",
        "contract_schema_version": contract["schema_version"],
        "inventory_sha256": inventory["inventory_sha256"],
        "counts": {
            "profiles": len(profile_ids),
            "components": len(components),
            "mcp_servers": sum(
                row["surface"] == "mcp_server" for row in components
            ),
            "surfaces": len(surfaces),
        },
        "checks": checks,
        "summary": {
            "passed": sum(checks.values()),
            "total": len(checks),
            "contract_ready": all(checks.values()),
        },
        "scope": inventory["scope"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audita provider_change_intelligence_v1."
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
    return 0 if report["summary"]["contract_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
