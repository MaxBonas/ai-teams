from __future__ import annotations

import argparse
import json

from aiteam.dev_lifecycle_contract import ACTION_IDS, lifecycle_manifest
from aiteam.platform_runtime import configure_utf8_stdio


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Proyecta el contrato común de ciclo de vida sin ejecutarlo."
    )
    parser.add_argument(
        "--platform",
        choices=("windows", "linux", "macos"),
    )
    parser.add_argument(
        "--action",
        choices=ACTION_IDS,
        help="Limita la salida a una acción.",
    )
    args = parser.parse_args()

    manifest = lifecycle_manifest(target_platform=args.platform)
    if args.action:
        manifest["commands"] = {args.action: manifest["commands"][args.action]}
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
