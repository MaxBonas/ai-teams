from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.session_continuity import audit_session_experiment  # noqa: E402


def probe_capabilities() -> dict[str, Any]:
    """Inspección local y sin consumo LLM de primitivas de reanudación."""

    return {
        "contract_version": 1,
        "providers": [
            _probe(
                name="codex",
                version_args=["--version"],
                help_args=["exec", "resume", "--help"],
                explicit_markers=["SESSION_ID", "Resume a previous session by id"],
                unsafe_markers=["--last"],
            ),
            _probe(
                name="claude",
                version_args=["--version"],
                help_args=["--help"],
                explicit_markers=["--resume"],
                unsafe_markers=["--continue"],
            ),
            _probe(
                name="agy",
                version_args=["--version"],
                help_args=["--help"],
                explicit_markers=["--conversation"],
                unsafe_markers=["--continue"],
            ),
        ],
        "policy": {
            "selector": "explicit_id_only",
            "forbidden": ["codex --last", "claude --continue", "agy --continue"],
            "production_enabled": False,
        },
    }


def _probe(
    *,
    name: str,
    version_args: list[str],
    help_args: list[str],
    explicit_markers: list[str],
    unsafe_markers: list[str],
) -> dict[str, Any]:
    executable = shutil.which(f"{name}.cmd") or shutil.which(name)
    if not executable:
        return {"cli": name, "installed": False, "explicit_resume": False}
    version = _run([executable, *version_args])
    help_text = _run([executable, *help_args])
    return {
        "cli": name,
        "installed": True,
        "executable": executable,
        "version": version.strip().splitlines()[0] if version.strip() else None,
        "explicit_resume": any(marker in help_text for marker in explicit_markers),
        "unsafe_implicit_selector_present": any(marker in help_text for marker in unsafe_markers),
    }


def _run(command: list[str]) -> str:
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"probe_error:{exc}"
    return (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Instrumento offline para sesiones CLI persistentes. `probe` no llama a modelos; "
            "`audit` evalúa resultados A/B ya obtenidos y nunca reanuda una conversación."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe_parser = subparsers.add_parser("probe")
    probe_parser.add_argument("--output", type=Path)
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("inputs", nargs="+", type=Path)
    audit_parser.add_argument("--min-seeds", type=int, default=2)
    audit_parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.command == "probe":
        report = probe_capabilities()
        exit_code = 0
    else:
        reports: list[dict[str, Any]] = []
        for path in args.inputs:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                reports.extend(item for item in payload if isinstance(item, dict))
            elif isinstance(payload, dict) and isinstance(payload.get("reports"), list):
                reports.extend(item for item in payload["reports"] if isinstance(item, dict))
            elif isinstance(payload, dict):
                reports.append(payload)
        report = audit_session_experiment(reports, min_seeds=args.min_seeds)
        exit_code = 0 if report["production_activation_allowed"] else 2

    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
