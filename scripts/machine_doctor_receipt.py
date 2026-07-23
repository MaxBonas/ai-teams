from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.machine_doctor_receipt import (
    build_machine_doctor_receipt,
    write_explicit_receipt,
)
from aiteam.platform_runtime import configure_utf8_stdio


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=(
            "Genera un recibo explícito de discovery y verifica que las "
            "superficies gobernadas no cambien."
        )
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Permite reemplazar únicamente el recibo indicado.",
    )
    args = parser.parse_args()

    receipt = build_machine_doctor_receipt()
    write_explicit_receipt(args.output, receipt, overwrite=args.force)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if receipt["mutation_guard"]["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
