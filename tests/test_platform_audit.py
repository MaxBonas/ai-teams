from __future__ import annotations

from pathlib import Path

from aiteam.platform_audit import (
    audit_platform_portability,
    render_platform_portability_summary,
)


ROOT = Path(__file__).resolve().parents[1]


def test_repository_platform_audit_is_clean(tmp_path: Path) -> None:
    report = audit_platform_portability(
        ROOT,
        probe_dir=tmp_path,
        run_process_probe=False,
    )

    assert report["ok"] is True
    assert report["source"]["personal_absolute_paths"] == []
    assert report["source"]["shell_true"] == []
    assert report["boundary_missing"] == []
    assert report["scope"]["support_promotion"] is False


def test_platform_audit_finds_personal_paths_and_shell_execution(
    tmp_path: Path,
) -> None:
    for relative in (
        "aiteam/adapters/subprocess_adapter.py",
        "aiteam/adapters/subscription_cli_adapter.py",
        "aiteam/cli.py",
        "aiteam/mcp_runtime.py",
        "aiteam/notifications.py",
        "aiteam/user_config.py",
        "api/routers/user_adapters.py",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "from aiteam.platform_runtime import run_command\n",
            encoding="utf-8",
        )
    unsafe = tmp_path / "scripts" / "unsafe.py"
    unsafe.parent.mkdir(parents=True)
    unsafe.write_text(
        "HOME = r'C:\\Users\\Alice\\project'\n"
        "subprocess.Popen('tool', shell=True)\n",
        encoding="utf-8",
    )

    report = audit_platform_portability(
        tmp_path,
        probe_dir=tmp_path,
        run_process_probe=False,
    )

    assert report["ok"] is False
    assert report["source"]["personal_absolute_paths"] == [
        {"file": "scripts/unsafe.py", "line": 1}
    ]
    assert report["source"]["shell_true"] == [
        {"file": "scripts/unsafe.py", "line": 2}
    ]


def test_human_summary_states_no_support_promotion(tmp_path: Path) -> None:
    report = audit_platform_portability(
        ROOT,
        probe_dir=tmp_path,
        run_process_probe=False,
    )

    summary = render_platform_portability_summary(report)

    assert "no promociona soporte" in summary
