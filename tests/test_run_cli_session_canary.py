from __future__ import annotations

from scripts.run_cli_session_canary import _arm_report, _parse_json_object, _run_codex


def test_canary_parser_accepts_plain_or_fenced_json() -> None:
    assert _parse_json_object('{"initial_fact":"x"}') == {"initial_fact": "x"}
    assert _parse_json_object('```json\n{"initial_fact":"x"}\n```') == {
        "initial_fact": "x"
    }


def test_arm_report_separates_raw_cached_and_quality_gates() -> None:
    report = _arm_report(
        seed=1,
        arm="resumed",
        run={
            "status": "completed",
            "duration_seconds": 3.5,
            "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 12},
            "output": '{"initial_fact":"F","active_color":"new","revoked_color_used":false}',
        },
        contract={"fact": "F", "old_color": "old", "new_color": "new"},
        explicit_session_id=True,
        scope_match=True,
        prompt_chars=100,
        session_id="session:12345678",
    )

    assert report["input_tokens"] == 100
    assert report["cached_input_tokens"] == 80
    assert report["uncached_input_tokens"] == 20
    assert all(report["gates"].values())


def test_resume_command_uses_configured_read_only_sandbox(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        return Proc()

    monkeypatch.setattr("scripts.run_cli_session_canary.subprocess.run", fake_run)
    monkeypatch.setattr("scripts.run_cli_session_canary.REPO_ROOT", tmp_path)
    _run_codex(
        executable="codex",
        model="gpt-5.5",
        prompt="next",
        timeout_sec=30,
        persist=False,
        resume_session_id="019f837c-8013-7a13-894b-fbdf2dc4f3c6",
    )

    command = captured["command"]
    assert "--sandbox" not in command
    assert 'sandbox_mode="read-only"' in command
