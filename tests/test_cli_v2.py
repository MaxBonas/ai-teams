from __future__ import annotations

import json

from aiteam.cli import main


def test_cli_system_check_reports_v2_control_plane(capsys) -> None:
    assert main(["system-check"]) == 0

    payload = json.loads(capsys.readouterr().out)

    assert payload["control_plane"] == "v2"
    assert payload["legacy_round_orchestrator"] == "retired"
    adapters = payload["adapters"]
    assert "lead_builtin" in adapters
    assert "anthropic_api" in adapters
    assert "anthropic_sonnet" in adapters
    assert "openai_api" in adapters
    assert "subscription_cli" in adapters
