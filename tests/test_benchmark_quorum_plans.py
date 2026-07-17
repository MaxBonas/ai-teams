from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aiteam.db.migration import SCHEMA_PATH
from scripts.benchmark_quorum_plans import evaluate_pair, load_quorum_pair, score_plan


RUBRIC = {
    "id": "test",
    "criteria": [
        {"id": "rollback", "weight": 2, "required": True, "patterns": ["rollback|reversión"]},
        {"id": "locking", "weight": 2, "patterns": ["WAL|bloqueo"]},
    ],
    "penalties": [{"id": "unsafe", "weight": 2, "patterns": ["sin backup"]}],
}


def test_final_plan_can_improve_base_against_same_hidden_rubric() -> None:
    base = "Objetivo: migrar. Pasos: cambiar la tabla."
    final = (
        "Objetivo y criterio de cierre: migrar sin pérdida. Fases y responsable: Engineer ejecuta; "
        "Reviewer acepta con evidencia de tests. Riesgos: bloqueo; activar WAL. Rollback y escalado "
        "al usuario si falla. La siguiente run reanuda desde el recibo durable."
    )
    report = evaluate_pair(base, final, RUBRIC)
    assert report["delta_score_pct"] > 0
    assert report["final"]["passes_hard_gate"] is True
    assert report["hard_gate_improved"] is True


def test_required_criterion_and_penalty_are_visible() -> None:
    score = score_plan("Objetivo: cambio destructivo sin backup.", RUBRIC)
    assert "rollback" in score["hard_failures"]
    assert score["passes_hard_gate"] is False
    assert score["penalties"][0]["applied"] == 2


def _quorum_db(tmp_path: Path, *, accepted: bool = True) -> Path:
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id,title) VALUES ('g','goal')")
        conn.execute("INSERT INTO agents (id,role,name,adapter_type) VALUES ('lead','lead','Lead','manual'),('q','reviewer','Q','manual')")
        conn.execute("INSERT INTO issues (id,goal_id,title,status) VALUES ('i','g','issue','in_progress')")
        conn.execute("INSERT INTO runs (id,agent_id,issue_id,status) VALUES ('r','q','i','completed')")
        conn.execute("INSERT INTO issue_documents (id,issue_id,key,title,body,current_revision_id,revision_number) VALUES ('d','i','plan','Plan','B','b',2)")
        conn.execute("INSERT INTO issue_document_revisions (id,document_id,issue_id,key,title,body,revision_number) VALUES ('a','d','i','plan','Plan','A',1),('b','d','i','plan','Plan','B',2)")
        conn.execute(
            "INSERT INTO quorum_sessions (id,issue_id,base_plan_revision_id,status,final_plan_revision_id) VALUES ('s','i','a',?,?)",
            ("accepted" if accepted else "reviewing", "b" if accepted else None),
        )
        conn.execute("INSERT INTO quorum_contributions (id,session_id,agent_id,run_id,ordinal,provider,model,channel,result,valid) VALUES ('c','s','q','r',1,'openai','m','api','ok',1)")
        conn.execute("INSERT INTO cost_events (id,run_id,agent_id,issue_id,input_tokens,output_tokens,cost_cents) VALUES ('ce','r','q','i',10,4,3)")
        conn.commit()
    return db


def test_load_quorum_pair_links_revisions_provenance_and_cost(tmp_path: Path) -> None:
    pair = load_quorum_pair(_quorum_db(tmp_path), issue_id="i")
    assert pair["base_plan"] == "A" and pair["final_plan"] == "B"
    assert pair["session"]["status"] == "accepted"
    assert pair["contributions"] == [{
        "ordinal": 1, "provider": "openai", "model": "m", "channel": "api",
        "valid": 1, "run_id": "r", "input_tokens": 10, "output_tokens": 4, "cost_cents": 3,
    }]


def test_load_quorum_pair_rejects_session_without_final_plan(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no accepted final plan"):
        load_quorum_pair(_quorum_db(tmp_path, accepted=False))


def test_all_versioned_plan_rubrics_are_valid() -> None:
    root = Path(__file__).resolve().parents[1] / "benchmarks" / "plan_quality"
    rubrics = [json.loads(path.read_text(encoding="utf-8")) for path in root.glob("*.json")]
    assert len(rubrics) >= 3
    for rubric in rubrics:
        assert rubric["id"].endswith("_v1")
        assert len(rubric["criteria"]) >= 8
        assert any(item.get("required") for item in rubric["criteria"])
