from __future__ import annotations

import json
import os
from pathlib import Path


def build_provider_cli_acceptance_fixture(
    root: Path,
    *,
    os_id: str,
) -> dict[str, str]:
    """Create non-global CLI shims used only to verify the release gate."""
    fixture_root = Path(root).resolve()
    bin_dir = fixture_root / "provider-cli-gate" / "bin"
    codex_home = fixture_root / "provider-cli-gate" / "codex-home"
    bin_dir.mkdir(parents=True, exist_ok=True)
    codex_home.mkdir(parents=True, exist_ok=True)
    versions = {
        "codex": "codex-cli 0.146.0-alpha.6",
        "agy": "1.1.8",
        "opencode": "1.18.4",
    }
    windows = os_id == "windows"
    for name, version in versions.items():
        if windows:
            target = bin_dir / f"{name}.cmd"
            target.write_text(
                f"@echo off\r\necho {version}\r\n",
                encoding="utf-8",
                newline="",
            )
        else:
            target = bin_dir / name
            target.write_text(
                f"#!/bin/sh\nprintf '%s\\n' '{version}'\n",
                encoding="utf-8",
            )
            target.chmod(0o755)
    (codex_home / "models_cache.json").write_text(
        json.dumps(
            {
                "client_version": "0.146.0",
                "models": [
                    {"slug": "gpt-5.6-luna"},
                    {"slug": "gpt-5.6-sol"},
                    {"slug": "gpt-5.6-terra"},
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    separator = ";" if windows else ":"
    environment = {
        "PATH": f"{bin_dir}{separator}{os.environ.get('PATH', '')}",
        "CODEX_HOME": str(codex_home),
        "AITEAM_PROVIDER_CLI_FIXTURE": "release_acceptance_v1",
    }
    if windows:
        environment["LOCALAPPDATA"] = str(
            fixture_root / "provider-cli-gate" / "local-app-data"
        )
    return environment
