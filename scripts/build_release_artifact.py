from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.release_artifact import ReleaseArtifactError, build_release_artifact


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Construye el artefacto reproducible y redacted de AI Teams."
    )
    parser.add_argument("--version", required=True, help="Versión SemVer del artefacto.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist/release"),
        help="Directorio de salida (por defecto: dist/release).",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Permite un preview local, siempre no promocionable.",
    )
    parser.add_argument(
        "--require-release-tag",
        action="store_true",
        help="Exige que HEAD tenga el tag exacto VERSION o vVERSION.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        result = build_release_artifact(
            root,
            args.output_dir,
            args.version,
            allow_dirty=args.allow_dirty,
            require_release_tag=args.require_release_tag,
        )
    except ReleaseArtifactError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {"ok": True, **result.as_dict()},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
