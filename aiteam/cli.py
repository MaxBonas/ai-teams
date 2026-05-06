from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.adapters import build_default_registry
from aiteam.db.finops import check_budget
from aiteam.db.migration import migrate_to_v2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aiteam", description="AI Teams v2 control-plane CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("system-check", help="Print a small v2 runtime smoke check")

    migrate = sub.add_parser("migrate-to-v2", help="Run the SQLite v2 migration")
    migrate.add_argument("--db", default="runtime/aiteam.db")
    migrate.add_argument("--apply", action="store_true")
    migrate.add_argument("--no-backup", action="store_true")
    migrate.add_argument("--json", action="store_true")

    budget = sub.add_parser("budget-status", help="Show monthly budget status for an agent")
    budget.add_argument("--db", default="runtime/aiteam.db")
    budget.add_argument("--agent-id", required=True)
    budget.add_argument("--period", default=None)

    args = parser.parse_args(argv)
    if args.command == "system-check":
        registry = build_default_registry()
        payload = {
            "control_plane": "v2",
            "legacy_round_orchestrator": "retired",
            "adapters": [descriptor.adapter_type for descriptor in registry.descriptors()],
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "migrate-to-v2":
        summary = migrate_to_v2(
            Path(args.db),
            apply=bool(args.apply),
            backup=not bool(args.no_backup),
        )
        data = summary.to_dict()
        if args.json:
            print(json.dumps(data, ensure_ascii=False, sort_keys=True))
        else:
            for key, value in data.items():
                print(f"{key}: {value}")
        return 0

    if args.command == "budget-status":
        status = check_budget(Path(args.db), agent_id=args.agent_id, period=args.period)
        print(json.dumps(status.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
