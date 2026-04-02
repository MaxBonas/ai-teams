import tempfile
from pathlib import Path

from aiteam.lead_memory import (
    build_memory_prompt_block,
    load_lead_memory,
    update_lead_memory,
)


def test_lead_memory_skipped_if_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime_dir = Path(tmp) / "runtime"
        project_root = Path(tmp) / "workspace"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        project_root.mkdir(parents=True, exist_ok=True)

        block = build_memory_prompt_block(
            runtime_dir=runtime_dir,
            project_root=project_root,
        )

        assert block == ""


def test_lead_memory_includes_project_instructions_if_present() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime_dir = Path(tmp) / "runtime"
        project_root = Path(tmp) / "workspace"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (project_root / ".aiteam").mkdir(parents=True, exist_ok=True)
        (project_root / ".aiteam" / "instructions.md").write_text(
            "# Regla\nNo usar migraciones destructivas.\n",
            encoding="utf-8",
        )

        block = build_memory_prompt_block(
            runtime_dir=runtime_dir,
            project_root=project_root,
        )

        assert "== LEAD MEMORY ==" in block
        assert "## Instrucciones del proyecto (.aiteam/instructions.md)" in block
        assert "No usar migraciones destructivas." in block


def test_lead_memory_truncates_to_recent_runs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime_dir = Path(tmp) / "runtime"
        project_root = Path(tmp) / "workspace"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        project_root.mkdir(parents=True, exist_ok=True)

        for idx in range(7):
            update_lead_memory(
                runtime_dir=runtime_dir,
                project_root=project_root,
                chat_id=f"CHAT-{idx}",
                objective=f"Objetivo {idx}",
                result="exitoso",
                phases_completed=3,
                phases_total=4,
                significant_errors=[],
                lead_decisions=[],
                duration_seconds=idx + 1,
                capabilities={
                    "configured_keys": ["OPENAI_API_KEY"],
                    "missing_keys": [],
                    "healthy_mcps": ["filesystem"],
                    "broken_mcps": [],
                },
            )

        text = load_lead_memory(runtime_dir)

        assert text.count("- Run ") == 5
        assert "Objetivo 6" in text
        assert "Objetivo 5" in text
        assert "Objetivo 4" in text
        assert "Objetivo 1" not in text
        assert "Objetivo 0" not in text
