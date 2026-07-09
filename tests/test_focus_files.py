"""Focus files: the files an issue explicitly mentions must get content
within the workspace_files budget before any generic-priority file.

Root cause of the capa-2 lead↔reviewer ping-pong: with 400+ files, the
32 KB budget was consumed by READMEs/docs before the .cs files under
review (priority last) ever got content — the reviewer received them as
"[content omitted]" and correctly refused to approve, forever.
"""
from __future__ import annotations

from pathlib import Path

from aiteam.heartbeat.executor import _extract_focus_paths, _read_workspace_files


def test_extract_focus_paths_finds_paths_and_filenames() -> None:
    tokens = _extract_focus_paths([
        "Revisar corrección de TestSceneManager.cs y su .meta",
        "El engineer tocó `Assets/Editor/CreateTestSceneEditor.cs` y Assets\\Scenes\\PrototypeTest.unity",
        "Criterio: TestSceneManager.cs.meta presente",
    ])
    assert "testscenemanager.cs" in tokens
    assert "assets/editor/createtestsceneeditor.cs" in tokens
    assert "assets/scenes/prototypetest.unity" in tokens
    assert "testscenemanager.cs.meta" in tokens


def test_extract_focus_paths_ignores_versions_and_prose() -> None:
    tokens = _extract_focus_paths([
        "usa GPT-5.5 y la v1.2 del plan. Nada más.",
    ])
    assert not any(t in {"5.5", "1.2", "v1.2", "gpt-5.5"} for t in tokens)


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "Assets" / "Scripts").mkdir(parents=True)
    (ws / "docs").mkdir()
    # Big docs that would exhaust the budget under generic priority
    (ws / "README.md").write_text("R" * 4000, encoding="utf-8")
    for i in range(5):
        (ws / "docs" / f"doc{i}.md").write_text("D" * 4000, encoding="utf-8")
    # The files the issue is about — sources, generic priority LAST
    (ws / "Assets" / "Scripts" / "TestSceneManager.cs").write_text("class TSM {}", encoding="utf-8")
    (ws / "Assets" / "Scripts" / "Other.cs").write_text("class Other {}", encoding="utf-8")
    return ws


def test_focused_files_get_content_before_docs(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    focus = _extract_focus_paths(["Revisar Assets/Scripts/TestSceneManager.cs"])
    files = _read_workspace_files(ws, max_per_file_bytes=8192, max_total_bytes=6000, focus_paths=focus)
    by_path = {f["path"]: f for f in files}

    target = by_path["Assets/Scripts/TestSceneManager.cs"]
    assert target.get("focus") is True
    assert target["content"] == "class TSM {}"
    # A non-focused source stays behind docs and falls out of the tiny budget
    other = by_path["Assets/Scripts/Other.cs"]
    assert "content omitted" in other["content"]
    # Everything still appears (existence is always answerable)
    assert len(files) == 8


def test_without_focus_sources_starve_as_before(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    files = _read_workspace_files(ws, max_per_file_bytes=8192, max_total_bytes=6000)
    by_path = {f["path"]: f for f in files}
    assert "content omitted" in by_path["Assets/Scripts/TestSceneManager.cs"]["content"]
    assert by_path["README.md"]["content"].startswith("R")


def test_focus_by_basename_matches(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    focus = _extract_focus_paths(["arregla TestSceneManager.cs por favor"])
    files = _read_workspace_files(ws, max_per_file_bytes=8192, max_total_bytes=6000, focus_paths=focus)
    by_path = {f["path"]: f for f in files}
    assert by_path["Assets/Scripts/TestSceneManager.cs"]["content"] == "class TSM {}"
