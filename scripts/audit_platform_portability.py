from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.platform_audit import (
    audit_platform_portability,
    render_platform_portability_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audita filesystem, procesos y fronteras OS sin modificar configuración "
            "ni promocionar soporte."
        )
    )
    parser.add_argument("--json", action="store_true", help="Emite JSON estable.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Devuelve código 2 si falla una comprobación.",
    )
    parser.add_argument(
        "--probe-dir",
        type=Path,
        help="Directorio donde crear el fixture temporal de filesystem.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    report = audit_platform_portability(root, probe_dir=args.probe_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_platform_portability_summary(report))
    return 2 if args.strict and not report["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
