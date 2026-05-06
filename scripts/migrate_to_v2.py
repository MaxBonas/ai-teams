from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.db.migration import migrate_to_v2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview or apply the AI Teams v2 control-plane schema migration."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("runtime") / "aiteam.db",
        help="Path to aiteam.db. Defaults to runtime/aiteam.db.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply schema and normalized rows. Omit for dry-run preview.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a .bak copy before applying.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON summary.",
    )
    args = parser.parse_args()

    summary = migrate_to_v2(
        args.db,
        apply=args.apply,
        backup=not args.no_backup,
    )
    payload = summary.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        mode = "APPLIED" if summary.applied else "DRY-RUN"
        print(f"{mode} v2 migration for {summary.db_path}")
        if summary.backup_path:
            print(f"backup: {summary.backup_path}")
        print(f"legacy_tasks: {summary.legacy_tasks}")
        print(f"goals: {summary.goals}")
        print(f"agents: {summary.agents}")
        print(f"team_blueprints: {summary.team_blueprints}")
        print(f"issues: {summary.issues}")
        print(f"issue_dependencies: {summary.issue_dependencies}")
        print(f"agent_assignments: {summary.agent_assignments}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
