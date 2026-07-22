import sqlite3
from pathlib import Path

from aiteam.db.migration import SCHEMA_PATH
from aiteam.model_selection_context import model_selection_runtime_context


def test_daily_api_budget_is_derived_from_cost_events(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_DAILY_COST_CAP_CENTS", "100")
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO cost_events (id, channel, cost_cents) VALUES (?, ?, ?)",
            ("cost:1", "api", 100),
        )
        conn.commit()

    context = model_selection_runtime_context(db, profiles=[])

    assert context["budget"]["status"] == "limit_reached"
    assert context["budget"]["cap_cents"] == 100
    assert context["budget"]["spent_cents"] == 100
    assert context["budget"]["remaining_cents"] == 0


def test_missing_budget_policy_is_unbounded_not_zero_budget(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AITEAM_DAILY_COST_CAP_CENTS", raising=False)

    context = model_selection_runtime_context(tmp_path / "missing.db", profiles=[])

    assert context["budget"]["status"] == "unbounded"
    assert context["budget"]["cap_cents"] is None
