from __future__ import annotations

from scripts.e2e_solo_lead_canary import run_canary


def test_solo_lead_canary_is_single_agent_direct_and_terminal(tmp_path) -> None:
    report = run_canary(tmp_path)
    assert report["ok"] is True
    assert all(report["checks"].values())
