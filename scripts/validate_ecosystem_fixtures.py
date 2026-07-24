from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.ecosystem_validation import (
    DEFAULT_FIXTURES_ROOT,
    required_cases_satisfied,
    validate_ecosystem_fixtures,
    write_validation_receipt,
)
from aiteam.platform_runtime import configure_utf8_stdio


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=(
            "Valida fixtures poliglotas mediante comandos descriptor-bound y "
            "produce un recibo sin promover soporte automáticamente."
        )
    )
    parser.add_argument("--fixtures-root", type=Path, default=DEFAULT_FIXTURES_ROOT)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--require", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    receipt = validate_ecosystem_fixtures(
        fixtures_root=args.fixtures_root,
        selected_case_ids=args.case or args.require,
        execute=not args.dry_run,
    )
    if args.receipt:
        write_validation_receipt(receipt, args.receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    satisfied, failures = required_cases_satisfied(receipt, args.require)
    if not satisfied:
        print(f"Required fixture cases did not pass: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
