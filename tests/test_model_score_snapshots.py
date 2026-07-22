import sqlite3
from pathlib import Path

import pytest

from aiteam.db.model_score_snapshots import (
    list_model_role_score_snapshots,
    persist_model_role_score_snapshot,
)


def _candidate(candidate_id: str = "candidate:a", *, eligible: bool = True) -> dict:
    return {
        "candidate_id": candidate_id,
        "score_version": "model_role_score_v1",
        "score": 88,
        "auto_eligible": eligible,
        "breakdown": {"quality": {"value": 90}},
    }


def test_snapshot_is_hashed_complete_and_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "scores.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(Path("aiteam/db/schema.sql").read_text(encoding="utf-8"))
    first = persist_model_role_score_snapshot(
        db_path,
        selection_scope="hiring:issue-1:engineer",
        canonical_role="engineer",
        score_version="model_role_score_v1",
        read_model_version="model_catalog_read_model_v1",
        candidates=[_candidate("candidate:b", eligible=False), _candidate()],
        winner_candidate_id="candidate:a",
        winner_reason="highest eligible score",
        auto_applied=True,
        snapshot_id="snapshot-1",
    )
    second = persist_model_role_score_snapshot(
        db_path,
        selection_scope="hiring:issue-1:engineer",
        canonical_role="engineer",
        score_version="model_role_score_v1",
        read_model_version="model_catalog_read_model_v1",
        candidates=[_candidate(), _candidate("candidate:b", eligible=False)],
        winner_candidate_id="candidate:a",
        winner_reason="highest eligible score",
        auto_applied=True,
        snapshot_id="different-id",
    )

    assert first["id"] == second["id"] == "snapshot-1"
    assert first["input_hash"] == second["input_hash"]
    assert len(first["input_hash"]) == 64
    assert first["auto_applied"] is True
    assert first["hash_valid"] is True
    assert len(first["candidates"]) == 2
    assert list_model_role_score_snapshots(db_path, canonical_role="engineer") == [
        first
    ]
    with sqlite3.connect(db_path) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM model_role_score_snapshots").fetchone()[
                0
            ]
            == 1
        )
        assert {
            row[2]
            for row in conn.execute(
                "PRAGMA foreign_key_list(model_role_score_snapshots)"
            )
        } == {"agents", "issues"}

        conn.execute(
            "UPDATE model_role_score_snapshots SET candidates_json = '[]' WHERE id = ?",
            (first["id"],),
        )
    assert list_model_role_score_snapshots(db_path)[0]["hash_valid"] is False


def test_snapshot_rejects_winner_outside_set_or_ineligible_auto_winner(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "scores.sqlite"
    kwargs = {
        "db_path": db_path,
        "selection_scope": "scope",
        "canonical_role": "engineer",
        "score_version": "model_role_score_v1",
        "read_model_version": "model_catalog_read_model_v1",
    }
    with pytest.raises(ValueError, match="belong"):
        persist_model_role_score_snapshot(
            **kwargs,
            candidates=[_candidate()],
            winner_candidate_id="candidate:missing",
        )
    with pytest.raises(ValueError, match="auto-eligible"):
        persist_model_role_score_snapshot(
            **kwargs,
            candidates=[_candidate(eligible=False)],
            winner_candidate_id="candidate:a",
            auto_applied=True,
        )
