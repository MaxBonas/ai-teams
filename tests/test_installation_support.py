from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aiteam.installation_support import (
    audit_installation_support,
    load_installation_support_contract,
    version_meets_minimum,
)

ROOT = Path(__file__).resolve().parents[1]


def test_support_contract_separates_required_primary_and_optional_local() -> None:
    contract = load_installation_support_contract()
    adapters = {item["id"]: item for item in contract["adapters"]}
    distributions = {item["id"]: item for item in contract["distributions"]}
    acceptance = contract["acceptance_contract"]

    assert {item["status"] for item in contract["platforms"]} <= {
        "verified",
        "preview",
        "planned",
        "unsupported",
    }
    assert adapters["codex_subscription"]["setup_class"] == "primary_option"
    assert adapters["antigravity_subscription"]["setup_class"] == "primary_option"
    assert adapters["opencode_zen_free"]["setup_class"] == "optional_economy"
    assert adapters["opencode_zen_free"]["authentication"]["human_required"] is True
    assert "API key personal" in adapters["opencode_zen_free"]["authentication"]["mode"]
    assert adapters["ollama"]["setup_class"] == "optional_local"
    assert adapters["lmstudio"]["setup_class"] == "optional_local"
    assert all(item["automatic_install"] is False for item in adapters.values())
    assert "checksums SHA-256" in distributions["versioned_release_artifact"]["required_contents"]
    assert distributions["git_checkout"]["integrity"] == "tag o commit SHA explícito"
    assert distributions["git_checkout"]["status"] == "verified"
    windows = next(
        item for item in contract["platforms"]
        if item["id"] == "windows_native_x86_64"
    )
    assert windows["status"] == "verified"
    assert windows["evidence"].endswith("windows-clean-room-f2a20ed.json")
    assert "adapters vivos" in windows["verified_scope"]
    assert acceptance["platform_id"] == "windows_native_x86_64"
    assert acceptance["required_steps"].count("bootstrap_first") == 1
    assert "independent_machine=true" in acceptance["promotion_requires"]
    assert "No ejecuta inferencias" in " ".join(acceptance["limits"])
    posix = [
        item
        for item in contract["platforms"]
        if item["os"] in {"linux", "macos"}
    ]
    assert all(item["status"] == "planned" for item in posix)
    assert all(item["bootstrap"] == "sh scripts/prepare_dev_env.sh" for item in posix)

    receipt_path = ROOT / windows["evidence"]
    receipt_bytes = receipt_path.read_bytes()
    assert hashlib.sha256(receipt_bytes).hexdigest() == windows["evidence_sha256"]
    assert len(windows["artifact_sha256"]) == 64
    receipt = json.loads(receipt_bytes)
    assert receipt["schema_version"] == "windows_clean_room_acceptance_v1"
    assert receipt["ok"] is True
    assert receipt["independent_machine"] is True
    assert receipt["promotion_allowed"] is True
    assert receipt["ci_provenance"]["run_id"] == "30023876549"
    assert receipt["source"]["revision"] == receipt["ci_provenance"]["source_sha"]
    assert len(receipt["steps"]) == 10
    assert all(step["ok"] for step in receipt["steps"])
    assert all(item["ready"] for item in receipt["installation_audit"]["runtimes"])


def test_machine_audit_does_not_treat_optional_tools_as_blockers() -> None:
    report = audit_installation_support(
        observed_versions={
            "python": "Python 3.12.10",
            "node": "v24.14.0",
            "npm": "11.9.0",
            "git": "git version 2.49.0",
            "powershell": "7.6.3",
            "codex_subscription": "codex-cli 0.145.0",
            "antigravity_subscription": None,
            "opencode_zen_free": None,
            "ollama": None,
            "lmstudio": None,
        },
        host=("windows", "x86_64"),
    )

    assert report["control_plane_ready"] is True
    assert report["live_runs"]["status"] == "adapter_installed_auth_health_required"
    assert report["live_runs"]["ready"] is False
    assert report["acceptance_contract"]["workflow"] == ".github/workflows/windows-clean-room.yml"
    assert {item["id"] for item in report["runtimes"]} == {
        "python",
        "node",
        "npm",
        "git",
        "powershell",
    }
    assert all(item["ready"] for item in report["runtimes"])
    assert next(item for item in report["adapters"] if item["id"] == "ollama")["installed"] is False


def test_machine_audit_requires_a_primary_adapter_for_live_runs() -> None:
    report = audit_installation_support(
        observed_versions={
            "python": "3.12.10",
            "node": "24.14.0",
            "npm": "11.9.0",
            "git": "2.49.0",
            "powershell": "7.6.3",
        },
        host=("windows", "x86_64"),
    )

    assert report["control_plane_ready"] is True
    assert report["live_runs"]["status"] == "primary_adapter_required"
    assert any("Codex o Antigravity" in item for item in report["next_actions"])


def test_version_comparison_is_numeric() -> None:
    assert version_meets_minimum("v24.14.0", "22")
    assert version_meets_minimum("Python 3.10.1", "3.10")
    assert not version_meets_minimum("v20.19.0", "22")
