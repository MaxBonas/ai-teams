from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aiteam.config_portability import (
    PortableConfigurationError,
    export_portable_configuration,
    import_portable_configuration,
    inspect_portable_configuration,
    read_portable_package,
    write_portable_package,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exporta o importa configuración operativa redacted de AI Teams."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    export_parser = commands.add_parser("export")
    export_parser.add_argument("--output", type=Path, required=True)
    export_parser.add_argument("--project", type=Path)

    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("--input", type=Path, required=True)

    import_parser = commands.add_parser("import")
    import_parser.add_argument("--input", type=Path, required=True)
    import_parser.add_argument("--project", type=Path)
    import_parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica el merge. Sin este flag solo ejecuta preflight.",
    )

    args = parser.parse_args()
    try:
        if args.command == "export":
            package = export_portable_configuration(project_dir=args.project)
            write_portable_package(args.output, package)
            result = {
                "success": True,
                "output": str(args.output),
                **inspect_portable_configuration(package),
            }
        elif args.command == "inspect":
            result = inspect_portable_configuration(read_portable_package(args.input))
            result["success"] = True
        else:
            package = read_portable_package(args.input)
            result = import_portable_configuration(
                package,
                project_dir=args.project,
                apply=args.apply,
            )
            result["success"] = not result["blockers"]
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("success") else 2
    except PortableConfigurationError as exc:
        print(
            json.dumps(
                {"success": False, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
