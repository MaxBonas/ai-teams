from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from aiteam.adapters.registry import AdapterDescriptor, AdapterRegistry, ExecutionResult
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor, _looks_like_plan
from aiteam.heartbeat.scheduler import HeartbeatScheduler
from aiteam.adapters.work_contract import ops_to_actions


def _init(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal:q', 'Quorum runtime')")
        conn.execute(
            "INSERT INTO agents (id, role, name, supervisor_agent_id) VALUES "
            "('role:lead', 'lead', 'Lead', NULL),"
            "('role:q1', 'reviewer', 'Q1', 'role:lead'),"
            "('role:q2', 'reviewer', 'Q2', 'role:lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id, metadata_json) "
            "VALUES ('issue:root', 'goal:q', 'Plan', 'in_progress', 'lead', 'role:lead', "
            "'{\"profile\":\"lead_quorum\"}')"
        )
        for issue_id, agent in (("issue:q1", "role:q1"), ("issue:q2", "role:q2")):
            conn.execute(
                "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id, metadata_json) "
                "VALUES (?, 'goal:q', 'issue:root', ?, 'in_progress', 'reviewer', ?, '{}')",
                (issue_id, issue_id, agent),
            )
        conn.execute(
            "INSERT INTO issue_documents (id, issue_id, key, title, body, current_revision_id) "
            "VALUES ('doc:plan', 'issue:root', 'plan', 'Plan', 'A', 'rev:a')"
        )
        conn.execute(
            "INSERT INTO issue_document_revisions "
            "(id, document_id, issue_id, key, title, body, revision_number) "
            "VALUES ('rev:a', 'doc:plan', 'issue:root', 'plan', 'Plan', 'A', 1)"
        )
        conn.commit()


def _proposal() -> dict:
    return {
        "profile": "lead_quorum",
        "plan_revision_id": "rev:a",
        "suggested_issues": [
            {"id": "issue:plan", "delegation_type": "planning"},
            {"id": "issue:q1", "delegation_type": "risk_review"},
            {"id": "issue:q2", "delegation_type": "risk_review"},
        ],
    }


def _report() -> dict:
    return {
        "valid": 1,
        "is_assignee": 1,
        "result": "changes_requested",
        "evidence": "Riesgo concreto sobre la revisión A.",
    }


def test_markdown_plan_is_materializable_without_plan_prefix() -> None:
    assert _looks_like_plan(
        "**Objective** migrar\n\n**Sub-issues** fases\n\n**Risk model** bloqueo"
    ) is True


def test_explicit_quorum_auto_starts_from_durable_plan_without_hiring_interaction(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM issues WHERE parent_id='issue:root'")
        conn.commit()
    executor = RunExecutor(db_path, AdapterRegistry([]))

    session = executor._maybe_start_explicit_quorum(issue_id="issue:root", run_id="")

    assert session is not None
    with sqlite3.connect(str(db_path)) as conn:
        children = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE parent_id='issue:root' AND role='reviewer'"
        ).fetchone()[0]
        wakes = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE reason='assignment' AND status='queued' "
            "AND agent_id LIKE 'role:quorum_auditor_%'"
        ).fetchone()[0]
        interactions = conn.execute(
            "SELECT COUNT(*) FROM issue_thread_interactions WHERE kind='suggest_tasks'"
        ).fetchone()[0]
    assert children == 2
    assert wakes == 2
    assert interactions == 0


def test_quorum_auditor_receives_immutable_plan_not_other_contributions(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM issues WHERE parent_id='issue:root'")
        conn.commit()
    setup = RunExecutor(db_path, AdapterRegistry([]))
    assert setup._maybe_start_explicit_quorum(issue_id="issue:root", run_id="") is not None
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET adapter_type='openai_api' WHERE id='role:quorum_auditor_1'")
        conn.commit()

    class _Capture:
        descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

        def __init__(self) -> None:
            self.payload = {}

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            self.payload = json.loads(str(wake_context["wake_payload_json"]))
            return {}

        def execute(self, run, env) -> ExecutionResult:
            return ExecutionResult(status="completed", output="audit")

    runtime = _Capture()
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:quorum_auditor_1")
    assert dispatch is not None
    RunExecutor(db_path, AdapterRegistry([runtime])).execute(dispatch)
    assert runtime.payload["quorum_review"]["base_plan_revision_id"] == "rev:a"
    assert runtime.payload["quorum_review"]["plan"]["body"] == "A"
    assert "contributions" not in runtime.payload["quorum_review"]


def test_quorum_report_in_add_comment_records_contribution(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([]))
    session = executor._initialize_quorum_session(
        parent_issue_id="issue:root", proposal=_proposal(), created_issue_ids=["issue:q1", "issue:q2"]
    )
    assert session is not None
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO runs (id,agent_id,issue_id,status,provider,model,channel) "
            "VALUES ('run:add','role:q1','issue:q1','running','openai','m','api')"
        )
        conn.commit()
    executor._apply_result_actions(
        run={"id": "run:add", "issue_id": "issue:q1", "provider": "openai", "model": "m", "channel": "api"},
        agent_id="role:q1",
        agent_role="reviewer",
        result=ExecutionResult(
            status="completed",
            actions={"add_comments": [
                "---AGENT-REPORT---\nrole: reviewer\nresult: changes_requested\n"
                "issue_status: blocked\nnext_owner: lead\ntech_match: n/a\n"
                "blocker: risk\nevidence: finding"
            ]},
        ),
    )
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT provider,valid FROM quorum_contributions WHERE session_id=?", (session["id"],)
        ).fetchone()
    assert row == ("openai", 1)


def test_missing_quorum_report_retries_once_then_degrades_with_lead_wakeup(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([]))
    session = executor._initialize_quorum_session(
        parent_issue_id="issue:root", proposal=_proposal(), created_issue_ids=["issue:q1", "issue:q2"]
    )
    assert session is not None
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO runs (id,agent_id,issue_id,status) VALUES "
            "('run:missing-1','role:q2','issue:q2','completed'),"
            "('run:missing-2','role:q2','issue:q2','completed')"
        )
        conn.commit()

    executor._ensure_quorum_auditor_continuation(
        issue_id="issue:q2", agent_id="role:q2", run_id="run:missing-1", run_status="completed"
    )
    with sqlite3.connect(str(db_path)) as conn:
        retry = conn.execute(
            "SELECT payload_json FROM wakeup_requests WHERE reason='quorum_report_retry'"
        ).fetchone()
        status = conn.execute("SELECT status FROM issues WHERE id='issue:q2'").fetchone()[0]
    assert retry is not None
    assert json.loads(retry[0])["quorum_session_id"] == session["id"]
    assert status == "todo"

    executor._ensure_quorum_auditor_continuation(
        issue_id="issue:q2", agent_id="role:q2", run_id="run:missing-1", run_status="completed"
    )
    with sqlite3.connect(str(db_path)) as conn:
        unchanged = conn.execute(
            "SELECT status FROM quorum_sessions WHERE id=?", (session["id"],)
        ).fetchone()[0]
        missing_events = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='quorum.auditor_report_missing'"
        ).fetchone()[0]
    assert unchanged == "reviewing"
    assert missing_events == 1

    executor._ensure_quorum_auditor_continuation(
        issue_id="issue:q2", agent_id="role:q2", run_id="run:missing-2", run_status="completed"
    )
    with sqlite3.connect(str(db_path)) as conn:
        degraded = conn.execute(
            "SELECT status,skipped_reason FROM quorum_sessions WHERE id=?", (session["id"],)
        ).fetchone()
        lead_wake = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE agent_id='role:lead' AND reason='quorum_degraded'"
        ).fetchone()[0]
        pending_retry = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE reason='quorum_report_retry' AND status='queued'"
        ).fetchone()[0]
    assert degraded == ("degraded", "auditor_report_format_exhausted")
    assert lead_wake == 1
    assert pending_retry == 0


def test_quorum_report_retry_bypasses_unchanged_review_evidence(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([]))
    monkeypatch.setattr(executor, "_review_evidence_unchanged", lambda **_kwargs: True)

    skip_reason = executor._preflight_skip_reason(
        issue_id="issue:q2",
        agent_role="reviewer",
        ctx={"wake_reason": "quorum_report_retry"},
        run_id="run:retry",
        agent_id="role:q2",
        workspace_root=tmp_path,
    )

    assert skip_reason is None


def test_explicit_quorum_cannot_close_before_accepted_plan(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([]))
    assert executor._quality_close_denied(issue_id="issue:root") == "lead_quorum_plan_and_session_required"
    session = executor._initialize_quorum_session(
        parent_issue_id="issue:root",
        proposal=_proposal(),
        created_issue_ids=["issue:q1", "issue:q2"],
    )
    assert session is not None
    assert executor._quality_close_denied(issue_id="issue:root") == "lead_quorum_accepted_plan_required"


class _CostedAuditorRuntime:
    descriptor = AdapterDescriptor(
        adapter_type="subscription_cli",
        channel="subscription",
        provider="openai-codex",
        model="gpt-auditor",
    )

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict, env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output=(
                "Revisión independiente completada.\n\n"
                "---AGENT-REPORT---\n"
                "role: reviewer\n"
                "result: changes_requested\n"
                "issue_status: done\n"
                "evidence: Riesgo concreto sobre la revisión A.\n"
            ),
            usage={"input_tokens": 1200, "output_tokens": 80},
            actual_cost_cents=0,
            actions={"issue_status": "done", "notify_supervisor": True},
        )


def _finish_auditor(
    executor: RunExecutor,
    db_path: Path,
    *,
    issue_id: str,
    agent_id: str,
    run_id: str,
    provider: str,
) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status, provider, model, channel) "
            "VALUES (?, ?, ?, 'completed', ?, 'stub', 'api')",
            (run_id, agent_id, issue_id, provider),
        )
        conn.execute("UPDATE issues SET status='done' WHERE id=?", (issue_id,))
        conn.commit()
    run = {
        "id": run_id,
        "provider": provider,
        "model": "stub",
        "channel": "api",
    }
    executor._maybe_record_quorum_contribution(
        issue_id=issue_id, agent_id=agent_id, run=run, report=_report()
    )
    executor._enqueue_supervisor_report(
        issue_id=issue_id, reporting_agent_id=agent_id, source_run_id=run_id
    )


def test_runtime_wakes_lead_only_when_quorum_gate_is_ready(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([]))
    session = executor._initialize_quorum_session(
        parent_issue_id="issue:root",
        proposal=_proposal(),
        created_issue_ids=["issue:q1", "issue:q2"],
    )
    assert session is not None

    _finish_auditor(
        executor, db_path, issue_id="issue:q1", agent_id="role:q1",
        run_id="run:q1", provider="openai",
    )
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wakeup_requests").fetchone()[0] == 0

    _finish_auditor(
        executor, db_path, issue_id="issue:q2", agent_id="role:q2",
        run_id="run:q2", provider="google",
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        wakeup = conn.execute("SELECT * FROM wakeup_requests").fetchone()
        assert wakeup["reason"] == "quorum_ready"
        assert json.loads(wakeup["payload_json"])["quorum_gate"]["ready"] is True


def test_quorum_auditor_run_links_contribution_to_zero_cost_usage_event(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE agents SET adapter_type='subscription_cli' WHERE id='role:q1'"
        )
        conn.commit()
    executor = RunExecutor(db_path, AdapterRegistry([_CostedAuditorRuntime()]))
    session = executor._initialize_quorum_session(
        parent_issue_id="issue:root",
        proposal=_proposal(),
        created_issue_ids=["issue:q1", "issue:q2"],
    )
    assert session is not None
    enqueue_wakeup(
        db_path,
        agent_id="role:q1",
        source="quorum",
        reason="new_issue",
        payload={"issue_id": "issue:q1", "quorum_session_id": session["id"]},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:q1")
    assert dispatch is not None

    executor.execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        link = conn.execute(
            """
            SELECT qc.run_id, qc.provider contribution_provider,
                   qc.model contribution_model, qc.channel contribution_channel,
                   ce.agent_id, ce.provider, ce.model, ce.channel,
                   ce.input_tokens, ce.output_tokens, ce.cost_cents
            FROM quorum_contributions qc
            JOIN cost_events ce ON ce.run_id=qc.run_id
            WHERE qc.session_id=? AND qc.agent_id='role:q1'
            """,
            (session["id"],),
        ).fetchone()
    assert link is not None
    assert link["run_id"] == dispatch.run["id"]
    assert link["agent_id"] == "role:q1"
    assert link["contribution_provider"] == link["provider"] == "openai-codex"
    assert link["contribution_model"] == link["model"] == "gpt-auditor"
    assert link["contribution_channel"] == link["channel"] == "subscription"
    assert (link["input_tokens"], link["output_tokens"], link["cost_cents"]) == (
        1200,
        80,
        0,
    )


def test_runtime_degrades_same_provider_quorum_with_continuation(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([]))
    session = executor._initialize_quorum_session(
        parent_issue_id="issue:root",
        proposal=_proposal(),
        created_issue_ids=["issue:q1", "issue:q2"],
    )
    assert session is not None
    _finish_auditor(
        executor, db_path, issue_id="issue:q1", agent_id="role:q1",
        run_id="run:q1", provider="openai",
    )
    _finish_auditor(
        executor, db_path, issue_id="issue:q2", agent_id="role:q2",
        run_id="run:q2", provider="openai",
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        session_row = conn.execute(
            "SELECT status, skipped_reason FROM quorum_sessions WHERE id=?", (session["id"],)
        ).fetchone()
        wakeup = conn.execute("SELECT reason FROM wakeup_requests").fetchone()
    assert dict(session_row) == {
        "status": "degraded",
        "skipped_reason": "provider_diversity_unsatisfied",
    }
    assert wakeup["reason"] == "quorum_degraded"


def test_degraded_quorum_escalates_without_replaying_api_lead(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET adapter_type='openai_api' WHERE id='role:lead'")
        conn.commit()
    setup_executor = RunExecutor(db_path, AdapterRegistry([]))
    session = setup_executor._initialize_quorum_session(
        parent_issue_id="issue:root", proposal=_proposal(),
        created_issue_ids=["issue:q1", "issue:q2"],
    )
    assert session is not None
    _finish_auditor(
        setup_executor, db_path, issue_id="issue:q1", agent_id="role:q1",
        run_id="run:q1", provider="openai",
    )
    _finish_auditor(
        setup_executor, db_path, issue_id="issue:q2", agent_id="role:q2",
        run_id="run:q2", provider="openai",
    )

    class _MustNotRun:
        descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

        def __init__(self) -> None:
            self.calls = 0

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {}

        def execute(self, run, env) -> ExecutionResult:
            self.calls += 1
            raise AssertionError("degraded quorum must escalate deterministically")

    runtime = _MustNotRun()
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    RunExecutor(db_path, AdapterRegistry([runtime])).execute(dispatch)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        interaction = conn.execute(
            "SELECT status, payload_json FROM issue_thread_interactions "
            "WHERE idempotency_key=?",
            (f"quorum:degraded:{session['id']}",),
        ).fetchone()
    assert runtime.calls == 0
    assert interaction["status"] == "pending"
    assert json.loads(interaction["payload_json"])["reason"] == "quorum_degraded"


def test_api_lead_acceptance_is_applied_deterministically_without_llm_replay(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM issues WHERE parent_id='issue:root'")
        conn.execute("UPDATE agents SET adapter_type='openai_api' WHERE id='role:lead'")
        proposal = {
            "profile": "lead_quorum",
            "plan_revision_id": "rev:a",
            "proposed_team": [],
            "suggested_issues": [
                {
                    "id": "issue:q1", "title": "Auditor 1", "role": "reviewer",
                    "assignee_agent_id": "role:q1", "delegation_type": "risk_review",
                },
                {
                    "id": "issue:q2", "title": "Auditor 2", "role": "reviewer",
                    "assignee_agent_id": "role:q2", "delegation_type": "risk_review",
                },
            ],
        }
        conn.execute(
            """
            INSERT INTO issue_thread_interactions (
                id, issue_id, kind, status, payload_json, result_json, idempotency_key
            ) VALUES ('int:q', 'issue:root', 'suggest_tasks', 'accepted', ?, '{}', 'int:q')
            """,
            (json.dumps(proposal),),
        )
        conn.commit()

    class _MustNotRun:
        descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

        def __init__(self) -> None:
            self.calls = 0

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {}

        def execute(self, run, env) -> ExecutionResult:
            self.calls += 1
            raise AssertionError("accepted suggest_tasks must not replay the LLM")

    runtime = _MustNotRun()
    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="interaction",
        reason="interaction_resolved",
        payload={
            "issue_id": "issue:root",
            "wake_reason": "interaction_resolved",
            "kind": "suggest_tasks",
            "action": "accept",
            "interaction_id": "int:q",
        },
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    RunExecutor(db_path, AdapterRegistry([runtime])).execute(dispatch)

    with sqlite3.connect(str(db_path)) as conn:
        session_count = conn.execute("SELECT COUNT(*) FROM quorum_sessions").fetchone()[0]
        child_count = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE parent_id='issue:root'"
        ).fetchone()[0]
    assert runtime.calls == 0
    assert session_count == 1
    assert child_count == 2


def test_lead_synthesis_action_updates_plan_and_finishes_planning(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([]))
    session = executor._initialize_quorum_session(
        parent_issue_id="issue:root",
        proposal=_proposal(),
        created_issue_ids=["issue:q1", "issue:q2"],
    )
    assert session is not None
    _finish_auditor(
        executor, db_path, issue_id="issue:q1", agent_id="role:q1",
        run_id="run:q1", provider="openai",
    )
    _finish_auditor(
        executor, db_path, issue_id="issue:q2", agent_id="role:q2",
        run_id="run:q2", provider="google",
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status) "
            "VALUES ('run:synthesis', 'role:lead', 'issue:root', 'running')"
        )
        conn.commit()

    actions = ops_to_actions(
        [
            {"type": "update_plan", "title": "Plan B", "body": "Plan consolidado B"},
            {
                "type": "accept_quorum_synthesis",
                "path": session["id"],
                "dispositions": [
                    {
                        "finding_id": "issue:q1:report",
                        "decision": "accept",
                        "rationale": "mitiga riesgo uno",
                    },
                    {
                        "finding_id": "issue:q2:report",
                        "decision": "qualify",
                        "rationale": "ajusta riesgo dos",
                    },
                ],
            },
        ]
    )
    executor._apply_result_actions(
        run={"id": "run:synthesis", "issue_id": "issue:root"},
        agent_id="role:lead",
        agent_role="lead",
        result=ExecutionResult(status="completed", actions=actions),
    )

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        session_row = conn.execute(
            "SELECT status, final_plan_revision_id FROM quorum_sessions WHERE id=?",
            (session["id"],),
        ).fetchone()
        issue_metadata = json.loads(conn.execute(
            "SELECT metadata_json FROM issues WHERE id='issue:root'"
        ).fetchone()[0])
        issue_status = conn.execute(
            "SELECT status FROM issues WHERE id='issue:root'"
        ).fetchone()[0]
        plan = conn.execute(
            "SELECT body, revision_number FROM issue_documents WHERE issue_id='issue:root' AND key='plan'"
        ).fetchone()
        accepted_wakes = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE reason='quorum_accepted'"
        ).fetchone()[0]
    assert session_row["status"] == "accepted"
    assert session_row["final_plan_revision_id"] != "rev:a"
    assert issue_status == "done"
    assert issue_metadata["profile"] == "lead_quorum"
    assert issue_metadata["planning_status"] == "accepted_plan"
    assert dict(plan) == {"body": "Plan consolidado B", "revision_number": 2}
    assert accepted_wakes == 0


def test_invalid_synthesis_escalates_after_bounded_retries(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([]))
    session = executor._initialize_quorum_session(
        parent_issue_id="issue:root",
        proposal=_proposal(),
        created_issue_ids=["issue:q1", "issue:q2"],
    )
    assert session is not None
    _finish_auditor(
        executor, db_path, issue_id="issue:q1", agent_id="role:q1",
        run_id="run:q1", provider="openai",
    )
    _finish_auditor(
        executor, db_path, issue_id="issue:q2", agent_id="role:q2",
        run_id="run:q2", provider="google",
    )
    incomplete_actions = ops_to_actions(
        [
            {"type": "update_plan", "title": "Plan incompleto", "body": "B"},
            {
                "type": "accept_quorum_synthesis",
                "path": session["id"],
                "dispositions": [
                    {"finding_id": "issue:q1:report", "decision": "accept", "rationale": "uno"}
                ],
            },
        ]
    )
    for attempt in (1, 2):
        run_id = f"run:synthesis:{attempt}"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO runs (id, agent_id, issue_id, status) "
                "VALUES (?, 'role:lead', 'issue:root', 'running')",
                (run_id,),
            )
            conn.commit()
        executor._apply_result_actions(
            run={"id": run_id, "issue_id": "issue:root"},
            agent_id="role:lead",
            agent_role="lead",
            result=ExecutionResult(status="completed", actions=incomplete_actions),
        )

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        session_row = conn.execute(
            "SELECT status, skipped_reason FROM quorum_sessions WHERE id=?", (session["id"],)
        ).fetchone()
        interaction = conn.execute(
            "SELECT status, payload_json FROM issue_thread_interactions "
            "WHERE idempotency_key=?",
            (f"quorum:synthesis-failed:{session['id']}",),
        ).fetchone()
        live_retries = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE reason='quorum_ready' "
            "AND status='queued'"
        ).fetchone()[0]
    assert dict(session_row) == {
        "status": "degraded",
        "skipped_reason": "synthesis_attempts_exhausted",
    }
    assert interaction["status"] == "pending"
    assert json.loads(interaction["payload_json"])["reason"] == "quorum_synthesis_failed"
    assert live_retries == 0


def test_quorum_ready_payload_drives_real_adapter_synthesis(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE agents SET adapter_type='openai_api' WHERE id='role:lead'")
        conn.commit()
    setup_executor = RunExecutor(db_path, AdapterRegistry([]))
    session = setup_executor._initialize_quorum_session(
        parent_issue_id="issue:root", proposal=_proposal(),
        created_issue_ids=["issue:q1", "issue:q2"],
    )
    assert session is not None
    _finish_auditor(
        setup_executor, db_path, issue_id="issue:q1", agent_id="role:q1",
        run_id="run:q1", provider="openai",
    )
    _finish_auditor(
        setup_executor, db_path, issue_id="issue:q2", agent_id="role:q2",
        run_id="run:q2", provider="google",
    )

    class _SynthesizingRuntime:
        descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

        def __init__(self) -> None:
            self.payload: dict = {}

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"WAKE": str(wake_context.get("wake_payload_json") or "{}")}

        def execute(self, run, env) -> ExecutionResult:
            self.payload = json.loads(env["WAKE"])
            quorum = self.payload["quorum"]
            dispositions = [
                {
                    "finding_id": finding["id"],
                    "decision": "accept",
                    "rationale": "incorporado en plan B",
                }
                for contribution in quorum["contributions"]
                for finding in contribution["findings"]
            ]
            return ExecutionResult(
                status="completed",
                output="Síntesis estructurada.",
                actions={
                    "update_plan": {"title": "Plan B", "body": "Plan B desde quorum"},
                    "accept_quorum_synthesis": {
                        "session_id": quorum["session_id"],
                        "dispositions": dispositions,
                    },
                },
            )

    runtime = _SynthesizingRuntime()
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    RunExecutor(db_path, AdapterRegistry([runtime])).execute(dispatch)
    with sqlite3.connect(str(db_path)) as conn:
        status = conn.execute(
            "SELECT status FROM quorum_sessions WHERE id=?", (session["id"],)
        ).fetchone()[0]
        metadata = json.loads(conn.execute(
            "SELECT metadata_json FROM issues WHERE id='issue:root'"
        ).fetchone()[0])
    assert runtime.payload["quorum"]["base_plan_revision_id"] == "rev:a"
    assert len(runtime.payload["quorum"]["contributions"]) == 2
    assert status == "accepted"
    assert metadata["profile"] == "lead_quorum"
    assert metadata["planning_status"] == "accepted_plan"
