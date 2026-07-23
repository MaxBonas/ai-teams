from __future__ import annotations

import argparse
import json

from aiteam.installation_support import (
    audit_installation_support,
    load_installation_support_contract,
    render_installation_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audita el contrato I.1 sin instalar software ni probar credenciales."
    )
    parser.add_argument("--json", action="store_true", help="Emite el informe estable en JSON.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Devuelve código 2 cuando faltan runtimes requeridos.",
    )
    args = parser.parse_args()

    report = audit_installation_support(load_installation_support_contract())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_installation_summary(report))
    return 2 if args.strict and not report["control_plane_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
