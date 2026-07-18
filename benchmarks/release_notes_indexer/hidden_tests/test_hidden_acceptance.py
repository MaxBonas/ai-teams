from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import release_notes_indexer


def test_indexes_releases_sections_and_bullets_in_order() -> None:
    source = """# Changelog
intro
## [2.0.0] - 2026-07-18
### Added
- alpha
- beta
### Fixed
- gamma
## [1.5.0] - 2026-06-01
### Changed
- delta
"""
    assert release_notes_indexer.index_release_notes(source) == [
        {"version": "2.0.0", "date": "2026-07-18", "sections": {"Added": ["alpha", "beta"], "Fixed": ["gamma"]}},
        {"version": "1.5.0", "date": "2026-06-01", "sections": {"Changed": ["delta"]}},
    ]


def test_ignores_markdown_constructs_inside_fences() -> None:
    source = """```md
## [fake] - 2026-01-01
### Added
- nope
```
## [real] - 2026-02-02
### Added
- yes
```text
### Fixed
- hidden
```
- still-added
"""
    result = release_notes_indexer.index_release_notes(source)
    assert result == [{"version": "real", "date": "2026-02-02", "sections": {"Added": ["yes", "still-added"]}}]


def test_invalid_h2_terminates_active_release() -> None:
    source = """## [1.0] - 2026-01-01
### Added
- kept
## Unreleased
### Added
- must-not-leak
"""
    result = release_notes_indexer.index_release_notes(source)
    assert result[0]["sections"] == {"Added": ["kept"]}


def test_duplicate_version_is_rejected() -> None:
    source = """## [1.0] - 2026-01-01
## [1.0] - 2026-02-01
"""
    with pytest.raises(ValueError):
        release_notes_indexer.index_release_notes(source)


def test_malformed_release_headings_are_ignored() -> None:
    source = """## 1.0 - 2026-01-01
## [2.0] 2026-02-02
## [3.0] - 02-02-2026
## [4.0] - 2026-02-30
## [5.0] - 2026-03-03
### Added
- valid
"""
    # El contrato exige forma YYYY-MM-DD, no validación calendárica. 2026-02-30
    # es sintácticamente válido y no puede convertirse en requisito oculto.
    assert release_notes_indexer.index_release_notes(source) == [
        {"version": "4.0", "date": "2026-02-30", "sections": {}},
        {"version": "5.0", "date": "2026-03-03", "sections": {"Added": ["valid"]}},
    ]


def test_cli_writes_stable_utf8_json_and_newline(tmp_path: Path) -> None:
    source = tmp_path / "CHANGELOG.md"
    target = tmp_path / "index.json"
    source.write_text("## [1.0] - 2026-01-01\n### Añadido\n- opción ágil\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(Path(release_notes_indexer.__file__)), str(source), str(target)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    raw = target.read_bytes()
    assert raw.endswith(b"\n")
    assert json.loads(raw.decode("utf-8"))[0]["sections"]["Añadido"] == ["opción ágil"]


def test_cli_failure_does_not_leave_partial_output(tmp_path: Path) -> None:
    target = tmp_path / "index.json"
    proc = subprocess.run(
        [sys.executable, str(Path(release_notes_indexer.__file__)), str(tmp_path / "missing.md"), str(target)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert not target.exists()
