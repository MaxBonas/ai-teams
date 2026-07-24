from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_release_workflow_separates_read_build_from_write_publish() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "release-artifact.yml"
    ).read_text(encoding="utf-8")

    assert "\npermissions:\n  contents: read\n" in workflow
    publish = workflow.split("\n  publish:\n", 1)[1]
    assert "if: github.ref_type == 'tag'" in publish
    assert "needs: [artifact, release-acceptance]" in publish
    assert "environment: github-release" in publish
    assert "permissions:\n      contents: write" in publish
    assert "actions/download-artifact@v8" in publish
    assert "--require-promotable" in publish
    assert "--verify-tag" in publish
    assert "--draft" in publish
    assert 'test "${asset_count}" = "5"' in publish
    assert 'gh release edit "${GITHUB_REF_NAME}" --draft=false' in publish
    acceptance = workflow.split("\n  release-acceptance:\n", 1)[1].split(
        "\n  publish:\n", 1
    )[0]
    assert "os: [windows-latest, ubuntu-latest, macos-latest]" in acceptance
    assert "runs-on: ${{ matrix.os }}" in acceptance
    assert "scripts/accept_release_archive.py" in acceptance
    assert "--allow-preview" in acceptance
    assert "release-acceptance-receipt.json" in acceptance
    assert "${{ matrix.os }}" in acceptance


def test_release_workflow_gates_tag_before_upload_and_publish() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "release-artifact.yml"
    ).read_text(encoding="utf-8")
    artifact = workflow.split("\n  artifact:\n", 1)[1].split("\n  publish:\n", 1)[0]

    descriptor_gate = artifact.index("Validate tag, notes and rollback contract")
    build = artifact.index("Build deterministic artifact and sidecars")
    verification = artifact.index("Require promotable metadata for tags")
    smoke = artifact.index("Smoke extracted package")
    upload = artifact.index("Upload artifact and audit sidecars")
    assert descriptor_gate < build < verification < smoke < upload
    assert artifact.count("uv export --locked --no-header") == 2
