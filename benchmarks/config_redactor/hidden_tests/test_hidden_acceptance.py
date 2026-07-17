from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import config_redactor


def test_nested_redaction_is_case_insensitive_and_non_mutating() -> None:
    source = {
        "Api_Key": "a",
        "nested": [{"PASSWORD": "b", "monkey": "kept"}, {"signing_key": "c"}],
        "enabled": True,
    }
    original = copy.deepcopy(source)
    result = config_redactor.redact_config(source)
    assert source == original
    assert result == {
        "Api_Key": "***",
        "nested": [{"PASSWORD": "***", "monkey": "kept"}, {"signing_key": "***"}],
        "enabled": True,
    }


def test_cli_writes_utf8_json(tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    target = tmp_path / "out.json"
    source.write_text(json.dumps({"name": "ámbito", "token": "x"}), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(Path(config_redactor.__file__)), str(source), str(target)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(target.read_text(encoding="utf-8")) == {"name": "ámbito", "token": "***"}


def test_cli_rejects_invalid_json(tmp_path: Path) -> None:
    source = tmp_path / "bad.json"
    source.write_text("{", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(Path(config_redactor.__file__)), str(source), str(tmp_path / "out.json")],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
