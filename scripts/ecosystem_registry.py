from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.ecosystem_registry import (
    detect_project_ecosystems,
    load_ecosystem_registry,
    project_toolchain_projection,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspecciona el registro de ecosistemas sin ejecutar comandos."
    )
    parser.add_argument("command", choices=("catalog", "detect", "project"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--role", default="lead")
    args = parser.parse_args()
    if args.command == "catalog":
        result = load_ecosystem_registry()
    elif args.command == "detect":
        result = detect_project_ecosystems(args.root)
    else:
        result = project_toolchain_projection(args.root, role=args.role)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
