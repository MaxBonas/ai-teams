from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from aiteam.installation_support import load_installation_support_contract
from aiteam.provider_cli_version_audit import (
    audit_provider_cli_versions,
    build_catalog_guards,
    build_documentation_guard,
    parse_cli_version,
    validate_provider_cli_version_audit,
)


def _observations() -> tuple[dict, list[dict]]:
    rows = []
    doctor_adapters = []
    versions = {
        "codex_subscription": ("codex", "codex-cli 0.146.0-alpha.6", "codex.cmd"),
        "antigravity_subscription": ("agy", "1.1.8", "agy.exe"),
        "opencode_zen_free": ("opencode", "1.18.4", "opencode.cmd"),
    }
    for index, (adapter_id, (cli_id, version, executable)) in enumerate(
        versions.items(),
        start=1,
    ):
        fingerprint = f"{index:064x}"
        cli = {
            "id": cli_id,
            "installed": True,
            "version": version,
            "executable": executable,
            "fingerprint": fingerprint,
            "source": "path_lookup_version_only",
        }
        doctor_adapters.append({"id": adapter_id, "cli": cli})
        rows.append(
            {
                "adapter_id": adapter_id,
                "cli_id": cli_id,
                "installed": True,
                "version": version,
                "executable": executable,
                "fingerprint": fingerprint,
            }
        )
    return {"adapters": doctor_adapters}, rows


def _documentation() -> dict:
    return {
        "ok": True,
        "status": "current",
        "checks": {"fixture": True},
        "source_sha256": "d" * 64,
    }


def test_version_parser_preserves_prerelease_channel() -> None:
    parsed = parse_cli_version("codex-cli 0.146.0-alpha.6")

    assert parsed == {
        "normalized": "0.146.0-alpha.6",
        "core": (0, 146, 0),
        "prerelease": "alpha.6",
        "channel": "prerelease",
    }
    assert parse_cli_version("unknown") is None


def test_identity_version_pass_does_not_claim_catalog_readiness() -> None:
    doctor, runtime = _observations()

    report = audit_provider_cli_versions(
        doctor,
        runtime,
        documentation_guard=_documentation(),
    )

    assert report["summary"] == {
        "identity_version_ok": True,
        "primary_option_ready": True,
        "catalog_ready": False,
        "documentation_ready": True,
        "promotion_ready": False,
        "failure_count": 0,
    }
    assert {row["status"] for row in report["rows"]} == {"catalog_pending"}


def test_fingerprint_mismatch_fails_closed_without_paths() -> None:
    doctor, runtime = _observations()
    runtime[0]["fingerprint"] = "f" * 64

    report = audit_provider_cli_versions(
        doctor,
        runtime,
        documentation_guard=_documentation(),
    )
    codex = report["rows"][0]

    assert codex["identity_version_ok"] is False
    assert "fingerprint_matches" in codex["failure_codes"]
    assert report["summary"]["identity_version_ok"] is False
    assert "\\" not in str(report)


def test_unknown_version_and_implicit_prerelease_fail_closed() -> None:
    doctor, runtime = _observations()
    doctor["adapters"][0]["cli"]["version"] = "unknown"
    runtime[1]["version"] = "1.1.9-beta.1"
    doctor["adapters"][1]["cli"]["version"] = "1.1.9-beta.1"

    report = audit_provider_cli_versions(
        doctor,
        runtime,
        documentation_guard=_documentation(),
    )

    assert "version_observable" in report["rows"][0]["failure_codes"]
    assert "channel_allowed" in report["rows"][1]["failure_codes"]
    assert "prerelease_explicit" in report["rows"][1]["failure_codes"]


def test_optional_absence_passes_but_primary_group_requires_one() -> None:
    doctor, runtime = _observations()
    doctor["adapters"][2]["cli"].update(
        installed=False,
        version=None,
        executable=None,
        fingerprint=None,
    )
    runtime[2].update(
        installed=False,
        version=None,
        executable=None,
        fingerprint=None,
    )

    report = audit_provider_cli_versions(
        doctor,
        runtime,
        documentation_guard=_documentation(),
    )

    assert report["rows"][2]["status"] == "optional_absent"
    assert report["summary"]["identity_version_ok"] is True
    doctor["adapters"][0]["cli"].update(
        installed=False,
        version=None,
        executable=None,
        fingerprint=None,
    )
    runtime[0].update(
        installed=False,
        version=None,
        executable=None,
        fingerprint=None,
    )
    report = audit_provider_cli_versions(
        doctor,
        runtime,
        documentation_guard=_documentation(),
    )
    assert report["rows"][0]["status"] == "primary_alternative_absent"
    assert report["summary"]["identity_version_ok"] is True
    for index in (0, 1):
        doctor["adapters"][index]["cli"].update(
            installed=False,
            version=None,
            executable=None,
            fingerprint=None,
        )
        runtime[index].update(
            installed=False,
            version=None,
            executable=None,
            fingerprint=None,
        )
    report = audit_provider_cli_versions(
        doctor,
        runtime,
        documentation_guard=_documentation(),
    )
    assert report["summary"]["primary_option_ready"] is False
    assert report["summary"]["identity_version_ok"] is False


def test_report_validator_rejects_paths_and_summary_tampering() -> None:
    doctor, runtime = _observations()
    report = audit_provider_cli_versions(
        doctor,
        runtime,
        documentation_guard=_documentation(),
    )
    report["rows"][0]["runtime"]["executable"] = r"C:\private\codex.cmd"

    try:
        validate_provider_cli_version_audit(report)
    except ValueError as exc:
        assert "must not emit executable paths" in str(exc)
    else:
        raise AssertionError("personal executable path must fail closed")


def test_older_version_and_requirement_drift_are_rejected() -> None:
    doctor, runtime = _observations()
    doctor["adapters"][1]["cli"]["version"] = "1.1.7"
    runtime[1]["version"] = "1.1.7"

    report = audit_provider_cli_versions(
        doctor,
        runtime,
        documentation_guard=_documentation(),
    )

    assert "minimum_version" in report["rows"][1]["failure_codes"]

    support = deepcopy(load_installation_support_contract())
    support["cli_version_contract"]["entries"][0]["minimum_version"] = "0.146.0-alpha.7"
    report = audit_provider_cli_versions(
        doctor,
        runtime,
        support=support,
        documentation_guard=_documentation(),
    )
    assert "minimum_version" in report["rows"][0]["failure_codes"]


def test_catalog_guards_bind_fresh_evidence_to_exact_cli_versions() -> None:
    doctor, runtime = _observations()
    drift = {
        "observed_at": "2026-07-29T10:00:00+00:00",
        "catalogs": [
            {
                "profile_id": "antigravity_subscription",
                "cli_version": "1.1.8",
                "status": "current",
                "coverage_ok": True,
            },
            {
                "profile_id": "opencode_zen_free",
                "cli_version": "1.18.4",
                "status": "current",
                "coverage_ok": True,
            },
        ],
    }
    codex = {
        "status": "current",
        "installed_version": "0.146.0",
        "catalog_client_version": "0.146.0",
        "models": ["gpt-5.6-sol"],
    }

    guards = build_catalog_guards(
        runtime,
        drift_receipt=drift,
        codex_catalog=codex,
        observed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )
    report = audit_provider_cli_versions(
        doctor,
        runtime,
        catalog_guards=guards,
        documentation_guard=_documentation(),
    )

    assert all(guard["ok"] for guard in guards.values())
    assert report["summary"]["catalog_ready"] is True
    assert report["summary"]["promotion_ready"] is True


def test_documentation_guard_fails_when_contract_or_cli_is_omitted() -> None:
    current = (
        "provider_cli_version_contract_v1 "
        "config/installation_support.v1.json suelos validados "
        "Codex Antigravity OpenCode"
    )

    assert build_documentation_guard(current)["ok"] is True
    stale = build_documentation_guard(current.replace("OpenCode", ""))
    assert stale["ok"] is False
    assert stale["checks"]["cli_documented:opencode"] is False


def test_stale_or_version_mismatched_catalog_evidence_fails_closed() -> None:
    _doctor, runtime = _observations()
    drift = {
        "observed_at": "2026-05-01T00:00:00+00:00",
        "catalogs": [
            {
                "profile_id": "antigravity_subscription",
                "cli_version": "1.1.7",
                "status": "current",
                "coverage_ok": True,
            },
            {
                "profile_id": "opencode_zen_free",
                "cli_version": "1.18.4",
                "status": "current",
                "coverage_ok": True,
            },
        ],
    }
    guards = build_catalog_guards(
        runtime,
        drift_receipt=drift,
        codex_catalog={
            "status": "current",
            "installed_version": "0.146.0",
            "catalog_client_version": "0.147.0",
            "models": ["gpt-5.6-sol"],
        },
        observed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )

    assert guards["antigravity_subscription"]["ok"] is False
    assert (
        guards["antigravity_subscription"]["checks"]["cli_version_matches"]
        is False
    )
    assert guards["opencode_zen_free"]["checks"]["evidence_fresh"] is False
    assert guards["codex_subscription"]["ok"] is False
    assert (
        guards["codex_subscription"]["checks"]["catalog_not_newer_than_runtime"]
        is False
    )
