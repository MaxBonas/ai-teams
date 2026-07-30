"""Genera el inventario durable de cobertura útil Tier 1/Tier 2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.model_tier_coverage import audit_model_tier_coverage
from aiteam.user_config import DEFAULT_ADAPTER_PROFILES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.coverage.read_text(encoding="utf-8"))
    report = audit_model_tier_coverage(
        source,
        profiles=DEFAULT_ADAPTER_PROFILES,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "gaps": len(report["gaps"]),
                "lead_ready": report["tier_1"]["lanes"]["lead_ready"]["roles"][0][
                    "eligible_count"
                ],
                "quorum_ready": report["tier_1"]["lanes"]["quorum_ready"]["roles"][
                    0
                ]["eligible_count"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
