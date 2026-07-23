from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.dev_process_registry import (
    ProcessRegistryError,
    assert_clear,
    register_process,
    register_processes,
    stop_registered,
)


ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gobierna procesos locales de AI Teams.")
    parser.add_argument("--registry", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("assert-clear")

    register = subparsers.add_parser("register")
    register.add_argument("--backend-pid", type=int, required=True)
    register.add_argument("--frontend-pid", type=int, required=True)
    register.add_argument("--backend-port", type=int, required=True)
    register.add_argument("--frontend-port", type=int, required=True)

    register_one = subparsers.add_parser("register-one")
    register_one.add_argument("--role", choices=("backend", "frontend"), required=True)
    register_one.add_argument("--pid", type=int, required=True)
    register_one.add_argument("--port", type=int, required=True)

    stop = subparsers.add_parser("stop")
    stop.add_argument("--timeout", type=float, default=5.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    registry = args.registry.resolve() if args.registry else None
    try:
        if args.command == "assert-clear":
            result = assert_clear(root=ROOT, registry_path=registry)
        elif args.command == "register":
            result = register_processes(
                root=ROOT,
                registry_path=registry,
                process_specs=[
                    ("backend", args.backend_pid, "uvicorn api.main:app"),
                    ("frontend", args.frontend_pid, "npm"),
                ],
                ports={
                    "backend": args.backend_port,
                    "frontend": args.frontend_port,
                },
            )
        elif args.command == "register-one":
            marker = "uvicorn api.main:app" if args.role == "backend" else "npm"
            result = register_process(
                root=ROOT,
                registry_path=registry,
                role=args.role,
                pid=args.pid,
                marker=marker,
                port_key=args.role,
                port=args.port,
            )
        else:
            result = stop_registered(
                root=ROOT,
                registry_path=registry,
                timeout=args.timeout,
            )
    except ProcessRegistryError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
