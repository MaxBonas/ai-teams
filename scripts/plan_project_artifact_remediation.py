from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.project_artifact_audit import AuditOptions
from aiteam.project_artifact_remediation import (
    RemediationPlanError,
    build_remediation_manifest,
    write_remediation_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genera un dry-run inmutable; nunca mueve ni borra proyectos."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--include-all-candidates", action="store_true")
    selection.add_argument("--target-name", action="append", default=[])
    parser.add_argument("--active-workspace", type=Path)
    parser.add_argument("--registry-workspace", type=Path, action="append", default=[])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--git-timeout", type=float, default=4.0)
    parser.add_argument("--max-files", type=int, default=200_000)
    parser.add_argument("--probe-process-handles", action="store_true")
    args = parser.parse_args()

    try:
        manifest = build_remediation_manifest(
            args.root,
            target_names=args.target_name,
            include_all_candidates=args.include_all_candidates,
            active_workspace=args.active_workspace,
            registry_workspaces=args.registry_workspace,
            audit_options=AuditOptions(
                git_timeout_seconds=args.git_timeout,
                workers=args.workers,
                max_files_per_folder=args.max_files,
                probe_process_handles=args.probe_process_handles,
            ),
        )
        write_remediation_manifest(manifest, args.output, audited_root=args.root)
    except (OSError, RemediationPlanError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "execution_authorized": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2

    print(
        json.dumps(
            {
                "ok": True,
                "schema_version": manifest["schema_version"],
                "manifest_sha256": manifest["manifest_sha256"],
                "summary": manifest["summary"],
                "execution_authorized": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if not manifest["denied"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
