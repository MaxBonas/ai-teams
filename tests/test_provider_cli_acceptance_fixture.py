from __future__ import annotations

import json
import os
from pathlib import Path

from aiteam.provider_cli_acceptance_fixture import (
    build_provider_cli_acceptance_fixture,
)


def test_posix_fixture_is_local_executable_and_version_bound(
    tmp_path: Path,
) -> None:
    environment = build_provider_cli_acceptance_fixture(tmp_path, os_id="linux")
    bin_dir = Path(environment["CODEX_HOME"]).parent / "bin"

    assert "codex-cli 0.146.0-alpha.6" in (bin_dir / "codex").read_text(
        encoding="utf-8"
    )
    assert "'1.1.8'" in (bin_dir / "agy").read_text(encoding="utf-8")
    if os.name != "nt":
        assert (bin_dir / "codex").stat().st_mode & 0o111
    assert environment["AITEAM_PROVIDER_CLI_FIXTURE"] == "release_acceptance_v1"


def test_windows_fixture_uses_cmd_shims_and_codex_cache(
    tmp_path: Path,
) -> None:
    environment = build_provider_cli_acceptance_fixture(tmp_path, os_id="windows")
    bin_dir = Path(environment["CODEX_HOME"]).parent / "bin"
    cache = json.loads(
        (Path(environment["CODEX_HOME"]) / "models_cache.json").read_text(
            encoding="utf-8"
        )
    )

    assert (bin_dir / "codex.cmd").is_file()
    assert (bin_dir / "agy.cmd").is_file()
    assert (bin_dir / "opencode.cmd").is_file()
    assert cache["client_version"] == "0.146.0"
    assert {item["slug"] for item in cache["models"]} == {
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
    }
