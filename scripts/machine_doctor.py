from __future__ import annotations

import argparse
import json

from aiteam.machine_doctor import build_machine_inventory, render_machine_inventory
from aiteam.platform_runtime import configure_utf8_stdio


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=(
            "Inventario read-only de máquina, toolchains y adapters; observa "
            "versiones y health durable sin probar credenciales ni ejecutar inferencias."
        )
    )
    parser.add_argument("--json", action="store_true", help="Emite machine_doctor_v1.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Devuelve código 2 si existe algún bloqueo de preparación.",
    )
    args = parser.parse_args()

    report = build_machine_inventory()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_machine_inventory(report))
    return 2 if args.strict and not report["summary"]["strict_pass"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
