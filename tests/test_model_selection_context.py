import sqlite3
from pathlib import Path

from aiteam.db.migration import SCHEMA_PATH
from aiteam.compatibility_service import issue_compatibility_context
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


def test_unreadable_budget_evidence_stays_unknown_and_does_not_create_db(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AITEAM_DAILY_COST_CAP_CENTS", "100")
    missing = tmp_path / "missing.db"

    context = model_selection_runtime_context(missing, profiles=[])

    assert context["budget"] == {
        "status": "unknown",
        "source": "cost_events_unavailable+daily_cost_cap_policy",
        "day": context["budget"]["day"],
        "cap_cents": 100,
        "spent_cents": None,
        "remaining_cents": None,
        "observed_at": context["budget"]["observed_at"],
    }
    assert missing.exists() is False


def test_issue_context_unions_required_capabilities_from_all_ancestors(
    tmp_path: Path,
) -> None:
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO issues (id, title, status, role, criticality, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
            ("root", "Root", "todo", "lead", "high", '{"required_capabilities":["external_mcp"]}'),
        )
        conn.execute(
            "INSERT INTO issues (id, parent_id, title, status, role, criticality, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("child", "root", "Child", "todo", "reviewer", "medium", '{"required_capabilities":["repo_read","external_mcp"]}'),
        )
        conn.commit()

    context = issue_compatibility_context(db, "child")

    assert context["required_capabilities"] == ["external_mcp", "repo_read"]
    assert context["criticality"] == "medium"
