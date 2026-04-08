import json
import tempfile
from pathlib import Path

from aiteam.lead_memory import (
    build_memory_prompt_block,
    load_lead_memory,
    observe_capabilities_snapshot,
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


# ── observe_capabilities_snapshot ────────────────────────────────────────────

class TestObserveCapabilitiesSnapshot:
    def test_no_provider_doctor_empty_keys(self):
        """When provider_doctor.json does not exist, configured_keys is empty
        (unless subscription_providers are passed)."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            snap = observe_capabilities_snapshot(runtime_dir=runtime_dir)
        assert snap["configured_keys"] == []
        assert snap["missing_keys"] == []

    def test_subscription_providers_added_when_no_doctor(self):
        """Subscription providers are listed even when provider_doctor.json is absent."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            snap = observe_capabilities_snapshot(
                runtime_dir=runtime_dir,
                subscription_providers=["anthropic", "google"],
            )
        assert "anthropic (subscription)" in snap["configured_keys"]
        assert "google (subscription)" in snap["configured_keys"]

    def test_subscription_providers_deduplicated_with_api_keys(self):
        """If a provider appears in both api_keys and subscription_providers,
        it must not be listed twice."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            doctor = {
                "api_keys": {"ANTHROPIC_API_KEY": "set", "OPENAI_API_KEY": "missing"}
            }
            (runtime_dir / "provider_doctor.json").write_text(
                json.dumps(doctor), encoding="utf-8"
            )
            snap = observe_capabilities_snapshot(
                runtime_dir=runtime_dir,
                subscription_providers=["anthropic"],
            )
        # ANTHROPIC_API_KEY is already present via api_keys; subscription label is distinct
        assert "ANTHROPIC_API_KEY" in snap["configured_keys"]
        assert "anthropic (subscription)" in snap["configured_keys"]
        # Ensure no duplicate entries
        assert len(snap["configured_keys"]) == len(set(snap["configured_keys"]))

    def test_none_subscription_providers_noop(self):
        """Passing subscription_providers=None is safe and adds nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            snap = observe_capabilities_snapshot(
                runtime_dir=runtime_dir,
                subscription_providers=None,
            )
        assert snap["configured_keys"] == []

    def test_empty_subscription_providers_list_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            snap = observe_capabilities_snapshot(
                runtime_dir=runtime_dir,
                subscription_providers=[],
            )
        assert snap["configured_keys"] == []

    def test_api_keys_from_provider_doctor(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            doctor = {
                "api_keys": {
                    "OPENAI_API_KEY": "set",
                    "GROQ_API_KEY": "configured",
                    "COHERE_API_KEY": "missing",
                }
            }
            (runtime_dir / "provider_doctor.json").write_text(
                json.dumps(doctor), encoding="utf-8"
            )
            snap = observe_capabilities_snapshot(runtime_dir=runtime_dir)
        assert "OPENAI_API_KEY" in snap["configured_keys"]
        assert "GROQ_API_KEY" in snap["configured_keys"]
        assert "COHERE_API_KEY" not in snap["configured_keys"]
        assert "COHERE_API_KEY" in snap["missing_keys"]
