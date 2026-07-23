from __future__ import annotations

from aiteam.installation_support import (
    audit_installation_support,
    load_installation_support_contract,
    version_meets_minimum,
)


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
    assert acceptance["platform_id"] == "windows_native_x86_64"
    assert acceptance["required_steps"].count("bootstrap_first") == 1
    assert "independent_machine=true" in acceptance["promotion_requires"]
    assert "No ejecuta inferencias" in " ".join(acceptance["limits"])


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
