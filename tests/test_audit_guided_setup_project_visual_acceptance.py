from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_guided_setup_project_visual_acceptance import (
    EXPECTED_STATES,
    MANIFEST_VERSION,
    _hash,
    build_audit,
    validate_audit,
)


def _visual_manifest(tmp_path: Path) -> Path:
    screenshots = []
    for index, (state, (file_name, width, height)) in enumerate(
        EXPECTED_STATES.items(),
        start=1,
    ):
        content = f"fixture-{state}".encode()
        (tmp_path / file_name).write_bytes(content)
        durable = state.startswith("durable-")
        authority = {
            "proposal_hash": f"{index:x}" * 64,
            "preflight_hash": f"{index + 1:x}" * 64,
            "execution_plan_hash": f"{index + 2:x}" * 64,
            "execution_receipt_hash": f"{index + 3:x}" * 64 if durable else None,
            "durable_receipt_hash": f"{index + 4:x}" * 64 if durable else None,
        }
        screenshots.append({
            "file": file_name,
            "state": state,
            "viewport": {"width": width, "height": height},
            "sha256": hashlib.sha256(content).hexdigest(),
            "authority_hashes": authority,
        })
    sealed = {
        "schema_version": MANIFEST_VERSION,
        "screenshots": screenshots,
    }
    path = tmp_path / "visual-evidence.json"
    path.write_text(
        json.dumps({
            **sealed,
            "evidence_sha256": _hash(sealed),
        }),
        encoding="utf-8",
    )
    return path


def test_visual_acceptance_audit_is_green(tmp_path: Path) -> None:
    report = build_audit(
        Path(__file__).resolve().parents[1],
        _visual_manifest(tmp_path),
    )

    assert report["summary"] == {
        "ready": True,
        "check_count": 10,
        "passed_count": 10,
        "visual_state_count": 6,
    }
    assert report["scope"]["remote_calls"] is False
    assert report["scope"]["quota_consumed"] is False


def test_visual_acceptance_rejects_screenshot_tampering(tmp_path: Path) -> None:
    manifest = _visual_manifest(tmp_path)
    (tmp_path / "preflight-go.png").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="screenshot hash drift"):
        build_audit(Path(__file__).resolve().parents[1], manifest)


def test_visual_acceptance_rejects_authority_tampering(tmp_path: Path) -> None:
    manifest_path = _visual_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["screenshots"][-1]["authority_hashes"]["execution_plan_hash"] = "short"
    sealed = {
        "schema_version": manifest["schema_version"],
        "screenshots": manifest["screenshots"],
    }
    manifest["evidence_sha256"] = _hash(sealed)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="authority hash drift"):
        build_audit(Path(__file__).resolve().parents[1], manifest_path)


def test_visual_acceptance_rejects_report_tampering(tmp_path: Path) -> None:
    report = build_audit(
        Path(__file__).resolve().parents[1],
        _visual_manifest(tmp_path),
    )
    tampered = deepcopy(report)
    tampered["checks"]["durable_go_requires_complete_hash_chain"] = False
    tampered["summary"]["ready"] = False
    tampered["summary"]["passed_count"] = 9

    with pytest.raises(ValueError, match="report hash drift"):
        validate_audit(tampered)
