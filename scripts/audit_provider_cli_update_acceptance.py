from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.provider_cli_update_acceptance import (
    build_provider_cli_update_acceptance,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Acepta por fixtures la equivalencia del gate CLI entre clone "
            "limpio y checkout actualizado, sin tocar instalaciones globales."
        )
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    report = build_provider_cli_update_acceptance()
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    ready = report["summary"]["promotion_ready"]
    return 0 if ready or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
