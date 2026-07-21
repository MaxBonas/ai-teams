from __future__ import annotations

import json

from scripts.benchmark_opencode_transport import _server_executable, evaluate_stream, summarize


def _stream(marker: str, session: str, tokens: int = 100) -> str:
    events = [
        {"type": "text", "sessionID": session, "part": {"type": "text", "text": json.dumps({
            "status": "completed", "summary": marker, "ops": [],
        })}},
        {"type": "step_finish", "sessionID": session, "part": {
            "type": "step-finish", "tokens": {
                "total": tokens, "input": tokens - 10, "output": 10,
                "reasoning": 0, "cache": {"read": 0, "write": 0},
            },
        }},
    ]
    return "\n".join(json.dumps(item) for item in events)


def test_evaluate_stream_requires_exact_contract_and_session() -> None:
    marker = "OPENCODE_TRANSPORT_1_DIRECT"
    report = evaluate_stream(
        arm="direct", seed=1, marker=marker,
        raw=_stream(marker, "ses_direct_1"), seconds=1.25,
    )

    assert report["ok"] is True
    assert report["usage"]["total_tokens"] == 100
    assert report["session_id"] == "ses_direct_1"


def test_summary_requires_six_distinct_fresh_sessions_and_keeps_production_off() -> None:
    rows = []
    for seed in (1, 2, 3):
        for arm in ("direct", "attached"):
            marker = f"OPENCODE_TRANSPORT_{seed}_{arm.upper()}"
            rows.append(evaluate_stream(
                arm=arm, seed=seed, marker=marker,
                raw=_stream(marker, f"ses_{arm}_{seed}"), seconds=float(seed),
            ))

    report = summarize(rows, model="opencode/test", cli_version="1.18.4")

    assert report["gates"]["matrix_3x2"] is True
    assert report["gates"]["all_contracts_passed"] is True
    assert report["gates"]["fresh_sessions_isolated"] is True
    assert report["gates"]["cancellation_tested"] is False
    assert report["production_activation_allowed"] is False


def test_windows_cmd_resolves_native_server_binary(tmp_path, monkeypatch) -> None:
    shim = tmp_path / "opencode.cmd"
    native = tmp_path / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"")
    shim.write_text("@echo off", encoding="utf-8")
    monkeypatch.setattr("scripts.benchmark_opencode_transport.os.name", "nt")

    assert _server_executable(str(shim)) == str(native)
