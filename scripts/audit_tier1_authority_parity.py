"""Audita paridad Tier 1 entre catálogo, API, selección, snapshots y UI."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.model_catalog_read_model import build_current_model_catalog_read_model
from aiteam.model_tier1_parity import audit_tier1_authority_parity
from aiteam.user_config import load_adapter_profiles, model_options


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    observed_at = datetime.now(timezone.utc)
    read_model = build_current_model_catalog_read_model(
        observed_at=observed_at,
        repo_root=REPO_ROOT,
    )
    report = audit_tier1_authority_parity(
        read_model,
        profiles=load_adapter_profiles(),
        options_by_profile=model_options(),
        repo_root=REPO_ROOT,
    )
    receipt = {
        "schema_version": "tier1_authority_parity_receipt_v1",
        "observed_at": observed_at.isoformat(),
        "report": report,
    }
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    if args.json:
        print(encoded)
    else:
        print(
            json.dumps(
                {
                    "ok": report["ok"],
                    "failures": report["failure_count"],
                    "authority_cells": report["read_model"]["authority_cell_count"],
                    "selection_cells": report["surfaces"][
                        "contextual_selection"
                    ]["checked_active_cells"],
                    "snapshot_decisions": report["surfaces"]["snapshots"][
                        "checked_decisions"
                    ],
                    "output": str(args.output) if args.output else None,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
