from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.machine_doctor import build_machine_inventory
from aiteam.machine_doctor_receipt import write_explicit_receipt
from aiteam.platform_runtime import configure_utf8_stdio
from aiteam.provider_cli_version_audit import (
    audit_provider_cli_versions,
    build_catalog_guards,
    build_documentation_guard,
    build_runtime_cli_observations,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DRIFT_RECEIPT = (
    ROOT
    / "benchmarks"
    / "results"
    / "model_catalog_drift"
    / "model-catalog-drift-2026-07-29-cli-refresh.json"
)


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=(
            "Audita versiones, identidad redacted y catálogo de los CLIs "
            "documentados sin login, instalación ni inferencia."
        )
    )
    parser.add_argument(
        "--drift-receipt",
        type=Path,
        default=DEFAULT_DRIFT_RECEIPT,
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    drift = json.loads(args.drift_receipt.read_text(encoding="utf-8"))
    doctor = build_machine_inventory()
    runtime = build_runtime_cli_observations()
    guards = build_catalog_guards(runtime, drift_receipt=drift)
    documentation = build_documentation_guard(
        (ROOT / "docs" / "INSTALLATION_AND_INTEGRATION.md").read_text(
            encoding="utf-8"
        )
    )
    report = audit_provider_cli_versions(
        doctor,
        runtime,
        catalog_guards=guards,
        documentation_guard=documentation,
    )
    if args.output:
        write_explicit_receipt(args.output, report, overwrite=args.force)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        summary = report["summary"]
        print(
            "[provider_cli_versions] "
            f"identity={summary['identity_version_ok']} "
            f"catalog={summary['catalog_ready']} "
            f"docs={summary['documentation_ready']} "
            f"promotion={summary['promotion_ready']} "
            f"failures={summary['failure_count']}"
        )
        for row in report["rows"]:
            print(
                f"[provider_cli_versions] {row['cli_id']}: {row['status']} "
                f"version={row['runtime']['version'] or 'absent'}"
            )
    return 2 if args.strict and not report["summary"]["promotion_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
