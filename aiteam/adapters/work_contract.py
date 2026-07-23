from __future__ import annotations

import json
import re
from typing import Any


PLAN_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "owner_role": {"type": "string"},
        "reports_to": {"type": "string"},
        "deliverable": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "accepted_by": {"type": "string"},
        "dependencies": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["id", "title", "owner_role", "reports_to", "deliverable", "evidence", "accepted_by", "dependencies"],
    "additionalProperties": False,
}

PLAN_CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "integer", "enum": [1]},
        "objective": {"type": "string"},
        "scope": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "architecture": {"type": "string"},
        "work_items": {"type": "array", "items": PLAN_ITEM_SCHEMA},
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"risk": {"type": "string"}, "mitigation": {"type": "string"}, "rollback": {"type": "string"}},
                "required": ["risk", "mitigation", "rollback"],
                "additionalProperties": False,
            },
        },
        "verification": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"criterion": {"type": "string"}, "evidence": {"type": "string"}, "owner_role": {"type": "string"}},
                "required": ["criterion", "evidence", "owner_role"],
                "additionalProperties": False,
            },
        },
        "escalation_conditions": {"type": "array", "items": {"type": "string"}},
        "next_run_risks": {"type": "array", "items": {"type": "string"}},
        "narrative_markdown": {"type": "string"},
    },
    "required": [
        "schema_version", "objective", "scope", "assumptions", "architecture", "work_items",
        "risks", "verification", "escalation_conditions", "next_run_risks", "narrative_markdown",
    ],
    "additionalProperties": False,
}

CAUSAL_UNIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": [
                "objective", "decision", "constraint", "evidence", "accountability",
                "risk", "escalation", "open_item", "scope", "rejected_option",
            ],
        },
        "statement": {"type": "string"},
        "links": {"type": "array", "items": {"type": "string"}},
        "source_comment_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["id", "kind", "statement", "links", "source_comment_ids"],
    "additionalProperties": False,
}


OP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": [
                "add_comment",
                "update_plan",
                "create_issue",
                "update_child_issue",
                "create_interaction",
                "set_status",
                "notify_supervisor",
                "write_file",
                "append_file",
                "delete_file",
                "accept_quorum_synthesis",
                "append_context_summary",
                "propose_skill",
            ],
        },
        "body": {"type": "string"},
        "plan": PLAN_CONTRACT_SCHEMA,
        "path": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "role": {"type": "string", "enum": ["lead", "engineer", "reviewer", "test_runner", "lead_executor"]},
        "complexity": {"type": "string", "enum": ["low", "medium", "high"]},
        "criticality": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "action_type": {
            "type": "string",
            "enum": ["code", "review", "scout_files", "scout_web", "research", "synthesis", "test_exec"],
        },
        "kind": {"type": "string", "enum": ["suggest_tasks", "request_confirmation"]},
        "summary": {"type": "string"},
        "idempotency_key": {"type": "string"},
        "applies_to_roles": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "causal_units": {"type": "array", "items": CAUSAL_UNIT_SCHEMA},
        "status": {"type": "string", "enum": ["done", "in_progress", "todo", "cancelled", "blocked"]},
        # payload is used exclusively with create_interaction.
        # Must include a 'reason' field so the executor can route the response correctly.
        # Example: {"reason": "lead_wants_file_read", "parent_issue_id": "..."}
        "payload": {"type": "object"},
        "dispositions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "finding_id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["accept", "qualify", "discard"]},
                    "rationale": {"type": "string"},
                },
                "required": ["finding_id", "decision", "rationale"],
                "additionalProperties": False,
            },
        },
        "start_comment_id": {"type": "string"},
        "end_comment_id": {"type": "string"},
        "char_count_original": {"type": "integer", "minimum": 1},
        "start_char_offset": {"type": "integer", "minimum": 0},
        "end_char_offset": {"type": "integer", "minimum": 1},
    },
    "required": ["type"],
    "additionalProperties": False,
}

SUBMIT_WORK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ops": {"type": "array", "items": OP_SCHEMA},
        "status": {"type": "string", "enum": ["completed", "failed", "skipped"]},
        "summary": {"type": "string"},
    },
    "required": ["ops", "status", "summary"],
    "additionalProperties": False,
}


OPENAI_SUBMIT_WORK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": OP_SCHEMA["properties"]["type"],
                    "body": {"type": ["string", "null"]},
                    "plan": {**PLAN_CONTRACT_SCHEMA, "type": ["object", "null"]},
                    "path": {"type": ["string", "null"]},
                    "title": {"type": ["string", "null"]},
                    "description": {"type": ["string", "null"]},
                    "role": {
                        "type": ["string", "null"],
                        "enum": ["lead", "engineer", "reviewer", "test_runner", "lead_executor", None],
                    },
                    "complexity": {"type": ["string", "null"], "enum": ["low", "medium", "high", None]},
                    "criticality": {
                        "type": ["string", "null"],
                        "enum": ["low", "medium", "high", "critical", None],
                    },
                    "action_type": {
                        "type": ["string", "null"],
                        "enum": ["code", "review", "scout_files", "scout_web", "research", "synthesis", "test_exec", None],
                    },
                    "kind": {"type": ["string", "null"], "enum": ["suggest_tasks", "request_confirmation", None]},
                    "summary": {"type": ["string", "null"]},
                    "idempotency_key": {"type": ["string", "null"]},
                    "applies_to_roles": {
                        "type": ["array", "null"], "items": {"type": "string"}
                    },
                    "evidence": {"type": ["array", "null"], "items": {"type": "string"}},
                    "causal_units": {
                        "type": ["array", "null"],
                        "items": CAUSAL_UNIT_SCHEMA,
                    },
                    "status": {
                        "type": ["string", "null"],
                        "enum": ["done", "in_progress", "todo", "cancelled", "blocked", None],
                    },
                    # payload for create_interaction — must include 'reason'.
                    # Strictly defined for OpenAI structured output compatibility.
                    "payload": {
                        "type": ["object", "null"],
                        "properties": {
                            "reason": {"type": ["string", "null"]},
                            "parent_issue_id": {"type": ["string", "null"]},
                            "catalog_id": {"type": ["string", "null"]},
                            "name": {"type": ["string", "null"]},
                            "source": {"type": ["string", "null"]},
                            "version": {"type": ["string", "null"]},
                            "justification": {"type": ["string", "null"]},
                            "args": {"type": ["array", "null"], "items": {"type": "string"}},
                            "env_required": {"type": ["array", "null"], "items": {"type": "string"}},
                            "applies_to_roles": {"type": ["array", "null"], "items": {"type": "string"}},
                        },
                        "required": [
                            "reason", "parent_issue_id", "catalog_id", "name", "source", "version",
                            "justification", "args", "env_required", "applies_to_roles",
                        ],
                        "additionalProperties": False,
                    },
                    "dispositions": {
                        "type": ["array", "null"],
                        "items": OP_SCHEMA["properties"]["dispositions"]["items"],
                    },
                    "start_comment_id": {"type": ["string", "null"]},
                    "end_comment_id": {"type": ["string", "null"]},
                    "char_count_original": {"type": ["integer", "null"], "minimum": 1},
                    "start_char_offset": {"type": ["integer", "null"], "minimum": 0},
                    "end_char_offset": {"type": ["integer", "null"], "minimum": 1},
                },
                "required": [
                    "type",
                    "body",
                    "plan",
                    "path",
                    "title",
                    "description",
                    "role",
                    "complexity",
                    "criticality",
                    "action_type",
                    "kind",
                    "summary",
                    "idempotency_key",
                    "applies_to_roles",
                    "evidence",
                    "causal_units",
                    "status",
                    "payload",
                    "dispositions",
                    "start_comment_id",
                    "end_comment_id",
                    "char_count_original",
                    "start_char_offset",
                    "end_char_offset",
                ],
                "additionalProperties": False,
            },
        },
        "status": SUBMIT_WORK_SCHEMA["properties"]["status"],
        "summary": SUBMIT_WORK_SCHEMA["properties"]["summary"],
    },
    "required": ["ops", "status", "summary"],
    "additionalProperties": False,
}

SUBMIT_WORK_TOOL: dict[str, Any] = {
    "name": "submit_work",
    "description": (
        "Submit the result of your work as a list of structured operations. "
        "Call this exactly once when you are done. Ops are applied in order by the control plane."
    ),
    "input_schema": SUBMIT_WORK_SCHEMA,
}


def critical_fact_retention_instruction() -> str:
    """Coverage pass for Tier 1 reasoning without exposing benchmark anchors."""
    return (
        "\n## Tier 1 causal fact retention\n"
        "- Before submitting Lead, Team Lead, Lead Executor, Architect or Quorum Auditor work, "
        "make one silent coverage pass over the full input.\n"
        "- Preserve every acceptance-critical scope/cohort boundary; tenant or authorization "
        "boundary; metric with value, unit, window and required action; owner and acceptor; "
        "dependency; rollback path; and explicit escalation trigger.\n"
        "- Do not replace an exact value with a vague summary and do not include rejected/noise "
        "options merely to prove that you saw them.\n"
    )


TIER1_FACT_RETENTION_ROLES = frozenset(
    {"lead", "team_lead", "lead_executor", "architect", "quorum_auditor", "quorum_senior"}
)
TIER3_CAUSAL_REPORT_ROLES = frozenset({"worker", "file_scout", "web_scout"})


def tier3_causal_report_instruction(role: str) -> str:
    """Exact close contract for cheap read-only roles."""
    role_key = role.strip().lower()
    if role_key not in TIER3_CAUSAL_REPORT_ROLES:
        return ""
    return (
        "\n## Tier 3 causal report\n"
        "- Before responding, make one coverage pass over the complete request and inputs. "
        "Answer every distinct question, scope boundary, metric/window/action, source and "
        "acceptance condition that materially affects the requested summary.\n"
        "- Stay within read-only authority. Report neutral evidence; do not turn a file-reading "
        "request into code review or implementation advice.\n"
        "- Put the complete artifact in the final add_comment body. That body must end with "
        "exactly one block using these keys and allowed values:\n"
        "---AGENT-REPORT---\n"
        f"role: {role_key}\n"
        "result: done | blocked\n"
        "issue_status: done | blocked\n"
        "next_owner: lead\n"
        "blocker: none | <specific blocker>\n"
        "evidence: <files, sources or input facts actually inspected>\n"
        "- Then emit set_status with the same terminal status. Never place the report only in "
        "summary or use free prose as the result value.\n"
    )


def build_execution_contract(role: str | None = None) -> str:
    contract = (
        "\n\n## Execution contract\n"
        "- Read AITEAM_WAKE_PAYLOAD_JSON for issue context.\n"
        "- Return exactly one JSON object matching the submit_work schema.\n"
        "- Keep ops minimal and targeted; prefer add_comment to report progress.\n"
        "- If you are the Lead and you create or revise a project plan, use update_plan with the structured "
        "plan object; body may contain a Markdown narrative but comments never create or revise the plan.\n"
        "- The plan must declare objective, scope, assumptions, architecture, accountable work_items, risks, "
        "verification evidence, escalation conditions and risks that could break the next run.\n"
        "- Use update_plan only when the plan has materially changed.\n"
        "- If you are the Lead on a normal full-team software build, default to Engineer + Reviewer; "
        "Reviewer absorbs static QA. Use role='test_runner' (Tier 3) only when runtime command execution is needed. "
        "Skip Reviewer only for low-risk work and state why.\n"
        "- Keep accountability explicit: each delegated issue should name who reports to whom and who accepts the result.\n"
        "- After delegating child issues, wait for concrete child reports before waking/polling the Lead again.\n"
        "- Use create_issue to delegate sub-work; use set_status: done when complete.\n"
        "- Use notify_supervisor after setting status to done when reporting up the chain.\n"
        "- Lead only: use propose_skill to record reusable project knowledge ONLY after observing concrete "
        "evidence. Set title=<stable name>, body=<concise markdown>, applies_to_roles=[...], evidence=[...]. "
        "It remains proposed and cannot affect a run until the owner activates it. User directives always win.\n"
        "- Use update_child_issue to unblock or requeue a child issue: "
        "{\"type\": \"update_child_issue\", \"path\": \"<child_issue_id>\", "
        "\"body\": \"<directive for the child agent>\", \"status\": \"todo\"}. "
        "This is the ONLY valid way to unblock a child — writing 'engineer desbloqueado' in a comment "
        "on your own issue does nothing. The 'path' field must contain the child issue ID.\n"
        "\n## create_interaction — mandatory payload.reason\n"
        "- Every create_interaction op MUST include payload: {\"reason\": \"<name>\"} so the executor can route "
        "the user's response correctly. Without 'reason', the interaction will be silently skipped.\n"
        "- Common reasons: 'lead_wants_file_read', 'initial_cycle_ready', 'child_blocked_requires_action', 'reviewer_fix_cycle_limit'.\n"
        "- LIMIT: only ONE create_interaction per run. The executor will silently drop any extras. "
        "If you need multiple user decisions, ask the most important one now and ask the rest in future heartbeats.\n"
        "- 'lead_wants_file_read' is ONLY for blocked Tier 3 scouts (file_scout, web_scout, context_curator, test_runner). "
        "Never use it for blocked engineers — engineers already receive workspace_files automatically.\n"
        "- Example: {\"type\": \"create_interaction\", \"kind\": \"request_confirmation\", "
        "\"title\": \"...\", \"summary\": \"...\", \"payload\": {\"reason\": \"lead_wants_file_read\"}, "
        "\"idempotency_key\": \"lead:file-read-request:<issue_id>\"}\n"
        "\n## extension_install_requested — proposing an MCP server (Lead-tier ONLY)\n"
        "- Activating an MCP server runs third-party code. This is ALWAYS the owner's decision — never "
        "auto-accepted, regardless of project autonomy mode.\n"
        "- Only lead/team_lead/lead_executor may emit this reason. If a Tier 2 worker (engineer/reviewer) "
        "identifies a capability gap (e.g. cannot verify Unity Play Mode from static review), it reports "
        "`needs_capability: <what and why>` in its AGENT-REPORT — it does NOT call create_interaction itself. "
        "The Lead reads that signal and decides whether to formalize a proposal.\n"
        "- Before proposing, check `project_open_issues` and prior comments for an existing proposal/rejection "
        "of the same capability — do not re-propose something the owner already declined, and do not duplicate "
        "research: if a sibling issue already produced a sourced recommendation, reuse it instead of re-delegating "
        "the same investigation (a duplicate MCP-research task once burned 95 failed runs before anyone noticed).\n"
        "- A wake with `wake_reason: mcp_need_suggested` is evidence to investigate, NOT permission to propose. "
        "Read its durable report evidence, first try existing tools/adapters, and only formalize a proposal when "
        "the capability gap remains concrete and the exact pre-installed executable can be named.\n"
        "- Prefer a reviewed catalog descriptor when it fits: set 'catalog_id' to github-readonly, "
        "playwright-browser or filesystem-workspace. The control plane fills and locks its runtime contract; "
        "the owner still receives the normal approval card and the executable must already exist locally.\n"
        "- Otherwise payload MUST include: 'reason': 'extension_install_requested', 'name' (short slug-friendly identifier), "
        "'source' (one exact executable already installed on the machine; shell commands, pipes and "
        "auto-install such as 'npx -y' are forbidden), 'version' (exact serverInfo pin, never 'latest'), "
        "'justification' (concrete evidence: what blocker this solves, with issue/run references). "
        "Optional: 'args' (list), 'env_required' (list of env var names the server needs), "
        "'applies_to_roles' (which roles get it — omit/empty is treated as 'no roles' here, always specify).\n"
        "- A catalog proposal needs catalog_id + justification; an ad-hoc proposal needs name/source/version/justification. "
        "Missing or modified fields are rejected automatically with a system comment; "
        "nothing is installed and nothing is asked of the user for an incomplete proposal.\n"
        "- Example: {\"type\": \"create_interaction\", \"kind\": \"request_confirmation\", "
        "\"title\": \"Proponer MCP: unity\", \"summary\": \"<qué, por qué, riesgos>\", "
        "\"payload\": {\"reason\": \"extension_install_requested\", \"name\": \"unity\", "
        "\"source\": \"C:/Tools/unity-mcp.exe\", \"version\": \"1.2.0\", "
        "\"applies_to_roles\": [\"engineer\", \"reviewer\"], "
        "\"justification\": \"Reviewer cannot verify Play Mode from static YAML — issue X blocked N rounds.\"}, "
        "\"idempotency_key\": \"lead:propose-mcp-unity\"}\n"
        "\n## project_open_issues (Lead only — GLOBAL truth for open work)\n"
        "- Lead wake payloads include 'project_open_issues': every non-terminal issue across the WHOLE "
        "project (all root issues), not just your current issue's children.\n"
        "- Any claim like 'no open issues' or 'no pending work' MUST be based on this list.\n"
        "- If your current issue is terminal/empty but project_open_issues is non-empty, the useful "
        "action this heartbeat is to direct, unblock, or delegate on those issues.\n"
        "\n## user_directives (BINDING project decisions)\n"
        "- The wake payload includes 'user_directives': decisions the project owner already took "
        "(their written answer is in 'user_note', the question in 'title'/'question_summary').\n"
        "- Directives are BINDING and OVERRIDE any earlier standard, plan line, or review criterion that "
        "contradicts them. Newest directive wins.\n"
        "- Lead: encode every applicable directive into the acceptance_criteria of new issues you create.\n"
        "- Reviewer/QA: judge against the directives — do NOT request changes that a directive has ruled out "
        "(e.g. if the user chose a generator-based deliverable, do not demand a hand-materialized scene).\n"
        "- If a directive makes your task unnecessary, close it (set_status done/cancelled) and say why.\n"
        "\n## interaction_resolved wakes\n"
        "- When AITEAM_WAKE_REASON is 'interaction_resolved', the payload JSON includes a 'resolved_interaction' object.\n"
        "- Always read resolved_interaction.user_note first — it is the user's written answer.\n"
        "- AITEAM_INTERACTION_ID, AITEAM_INTERACTION_ACTION, AITEAM_INTERACTION_KIND are also set.\n"
        "- 'reviewer_fix_cycle_limit': the automatic fix cycle limit was reached. "
        "accept → create ONE final engineer issue with full rejection history as description (complexity: high); "
        "reject → cancel remaining children and set parent to cancelled. "
        "Do NOT create more fix cycles after this — if the final attempt also fails, close the project.\n"
        "\n## File I/O ops (ALL adapters, including API-only)\n"
        "- Engineers MUST produce real file changes to be considered done. "
        "Use write_file, append_file, and delete_file ops to create or modify workspace files.\n"
        "- write_file: set 'path' to a relative workspace path and 'body' to the full file content.\n"
        "- append_file: set 'path' and 'body' to the text to append.\n"
        "- delete_file: set 'path' only.\n"
        "- Paths must be relative (e.g. 'src/main.py', 'README.md'). Never use absolute paths.\n"
        "- The control plane materializes these ops on disk BEFORE evaluating workspace evidence.\n"
        "- Engineering runs that produce no workspace changes will be asked to retry.\n"
        "- NEVER block because of binary/media assets (audio, images, video). "
        "Use Web Audio API (JavaScript oscillators) for sound, SVG for images, stubs for anything else.\n"
        "- Declaring blockage via ops: {\"type\":\"set_status\",\"status\":\"blocked\"} + {\"type\":\"notify_supervisor\"}. "
        "Writing 'blocked' only in the summary text has NO effect — the issue remains in-progress "
        "and the system re-wakes you until the ops are present.\n"
        "\n## Workspace files (ALL roles — Engineer, Reviewer, QA, file_scout)\n"
        "- The wake payload ALWAYS includes a 'workspace_files' list for Engineers, Reviewers, QA, and file_scouts.\n"
        "- Each entry has 'path', 'content', and 'size_bytes'.\n"
        "- Engineers: read workspace_files BEFORE writing any file. Do not recreate existing files unless "
        "intentionally modifying them. Do NOT ask the Lead for file contents — they are already in your payload.\n"
        "- Reviewers and QA: base your review/test report ONLY on the actual file contents provided, "
        "not on what the engineer described.\n"
        "- If 'workspace_files' is empty, the workspace is genuinely empty — state this explicitly and "
        "either start creating the required files (Engineer) or report that no files were available (Reviewer/QA).\n"
        "- NEVER fabricate test results, pass/fail verdicts, or code quality assessments without actual file evidence.\n"
        "\n## Tier 3 read-only roles (worker, file_scout, web_scout, context_curator, test_runner)\n"
        "- Your only job is to read inputs (or execute commands) and produce the role artifact.\n"
        "- worker, file_scout, web_scout and test_runner use add_comment, then set_status: done.\n"
        "- Do NOT create sub-issues, interactions, update_plan, or write files.\n"
        "- Append the ---AGENT-REPORT--- block as the last part of any report comment before setting status.\n"
        "- context_curator ONLY: payload.context_curation_target contains the exact parent-thread slice. "
        "Persist its causal summary with append_context_summary using path=target_issue_id, body=<summary>, "
        "start_comment_id, end_comment_id, char_count_original, start_char_offset and end_char_offset copied exactly from that payload. "
        "Also emit causal_units with id, kind, statement, links as relation:value and source_comment_ids. "
        "Accountability requires owner/deliverable/accepted_by links; escalation requires metric/threshold/window/action; "
        "rejected_option requires reason. Markdown stays at or below 30%; compact causal_units have a separate 4096-char cap. "
        "The summary must retain decisions, constraints, risks, evidence, owners and escalations. "
        "Before emitting the artifact, make one coverage pass over the whole slice: every distinct scope boundary, "
        "rollout/cohort rule, retry or recovery rule, and verification result must appear in the summary or a causal unit. "
        "Do not omit a fact merely because another fact of the same class was already retained. "
        "Keep every owner explicitly linked to its deliverable, every reviewer to its acceptance evidence, "
        "and every threshold to metric, value, window and action.\n"
        "\n## Workspace listing (fallback — rarely seen)\n"
        "- 'workspace_listing' is a legacy fallback included only when 'workspace_files' is absent.\n"
        "- Each entry has 'path' and 'size_bytes' only (no content).\n"
        "- In practice, Engineers always receive 'workspace_files' (full content) and will not see 'workspace_listing'.\n"
        "- If you do see 'workspace_listing', use it to see which files exist, then continue work without recreating them.\n"
    )
    if (role or "").strip().lower() in TIER1_FACT_RETENTION_ROLES:
        contract += critical_fact_retention_instruction()
    contract += tier3_causal_report_instruction(role or "")
    return contract


# ── Tier 3 op filter ─────────────────────────────────────────────────────────

# The per-tier op permission matrix lives in aiteam.policies (fase 5) —
# aliases kept here for existing imports/tests.
from aiteam.policies import (  # noqa: E402, F401
    OPS_FORBIDDEN_FOR_TIER2 as _OPS_FORBIDDEN_FOR_TIER2,
    OPS_FORBIDDEN_FOR_TIER3 as _OPS_FORBIDDEN_FOR_TIER3,
    TIER2_ROLES as _TIER2_ROLES_FOR_VALIDATION,
    TIER3_ROLES as _TIER3_ROLES_FOR_VALIDATION,
    forbidden_ops_for_role,
)


def filter_forbidden_ops_for_role(
    ops: list[dict[str, Any]], role: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (allowed_ops, dropped_ops) per the role permission matrix.

    Any op outside the role's vocabulary is silently removed before the
    executor processes the op list, and returned for auditing.
    """
    forbidden = forbidden_ops_for_role(role)
    if not forbidden:
        return ops, []
    allowed: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for op in ops:
        if str(op.get("type", "")) in forbidden:
            dropped.append(op)
        else:
            allowed.append(op)
    return allowed, dropped


def ops_to_actions(ops: list[dict[str, Any]]) -> dict[str, Any]:
    actions: dict[str, Any] = {}
    interactions: list[dict[str, Any]] = []
    create_issues: list[dict[str, Any]] = []
    child_updates: list[dict[str, Any]] = []
    update_plan: dict[str, Any] | None = None
    add_comments: list[str] = []
    file_ops: list[dict[str, Any]] = []
    quorum_synthesis: dict[str, Any] | None = None
    context_summary: dict[str, Any] | None = None
    skill_proposals: list[dict[str, Any]] = []

    for op in ops:
        op_type = str(op.get("type") or "")
        if op_type == "set_status":
            actions["issue_status"] = str(op.get("status") or "")
        elif op_type == "notify_supervisor":
            actions["notify_supervisor"] = True
        elif op_type == "add_comment":
            body = str(op.get("body") or "").strip()
            if body:
                add_comments.append(body)
        elif op_type == "update_plan":
            update_plan = {
                "title": str(op.get("title") or "Plan"),
                "body": str(op.get("body") or ""),
                "plan": op.get("plan") if isinstance(op.get("plan"), dict) else None,
            }
        elif op_type == "create_issue":
            issue_spec: dict[str, Any] = {
                "title": str(op.get("title") or ""),
                "description": str(op.get("description") or ""),
                "role": str(op.get("role") or "engineer"),
                "complexity": str(op.get("complexity") or "medium"),
            }
            # Optional routing fields — passed through when present so the executor
            # can apply route_action() to override the proposed role if needed.
            if op.get("criticality"):
                issue_spec["criticality"] = str(op["criticality"])
            if op.get("action_type"):
                issue_spec["action_type"] = str(op["action_type"])
            # Structured done-bar: list of verifiable acceptance criteria the
            # assignee must meet and the reviewer judges against.
            raw_criteria = op.get("acceptance_criteria")
            if isinstance(raw_criteria, list):
                criteria = [str(c).strip() for c in raw_criteria if str(c).strip()]
                if criteria:
                    issue_spec["acceptance_criteria"] = criteria
            create_issues.append(issue_spec)
        elif op_type == "create_interaction":
            # Merge agent-supplied payload (must include 'reason') with version sentinel.
            # The executor routes interaction responses by payload['reason'], so agents
            # MUST supply it — see lead.md "Interaction payload requirements".
            agent_payload = op.get("payload")
            merged_payload: dict[str, Any] = {"version": 1}
            if isinstance(agent_payload, dict):
                merged_payload.update(agent_payload)
            interactions.append(
                {
                    "kind": str(op.get("kind") or "request_confirmation"),
                    "title": str(op.get("title") or ""),
                    "summary": str(op.get("summary") or ""),
                    "idempotency_key": str(op.get("idempotency_key") or ""),
                    "payload": merged_payload,
                    "continuation_policy": "wake_assignee",
                }
            )
        elif op_type == "update_child_issue":
            # Lead posts a directive to a child issue and optionally requeues it.
            # 'path' holds the child issue ID; 'body' is the directive comment;
            # 'status' is the new status for the child (e.g. 'todo' to requeue).
            child_id = str(op.get("path") or "").strip()
            if child_id:
                update: dict[str, Any] = {"child_issue_id": child_id}
                if op.get("status"):
                    update["status"] = str(op["status"])
                if op.get("body"):
                    update["body"] = str(op["body"])
                child_updates.append(update)
        elif op_type in ("write_file", "append_file", "delete_file"):
            path = str(op.get("path") or "").strip()
            if path:
                file_ops.append(
                    {
                        "op": op_type,
                        "path": path,
                        "body": str(op.get("body") or "") if op_type != "delete_file" else "",
                    }
                )
        elif op_type == "accept_quorum_synthesis":
            quorum_synthesis = {
                "session_id": str(op.get("path") or "").strip(),
                "dispositions": op.get("dispositions") if isinstance(op.get("dispositions"), list) else [],
            }
        elif op_type == "append_context_summary":
            context_summary = {
                "target_issue_id": str(op.get("path") or "").strip(),
                "summary_markdown": str(op.get("body") or "").strip(),
                "start_comment_id": str(op.get("start_comment_id") or "").strip(),
                "end_comment_id": str(op.get("end_comment_id") or "").strip(),
                "char_count_original": int(op.get("char_count_original") or 0),
                "start_char_offset": int(op.get("start_char_offset") or 0),
                "end_char_offset": int(op.get("end_char_offset") or 0),
                "causal_units": op.get("causal_units") if isinstance(op.get("causal_units"), list) else None,
            }
        elif op_type == "propose_skill":
            skill_proposals.append({
                "name": str(op.get("title") or "").strip(),
                "body": str(op.get("body") or "").strip(),
                "applies_to_roles": [
                    str(item).strip() for item in (op.get("applies_to_roles") or [])
                    if str(item).strip()
                ],
                "evidence": [
                    str(item).strip() for item in (op.get("evidence") or [])
                    if str(item).strip()
                ],
            })

    if interactions:
        actions["interactions"] = interactions
    if create_issues:
        actions["create_issues"] = create_issues
    if child_updates:
        actions["update_child_issues"] = child_updates
    if update_plan is not None:
        actions["update_plan"] = update_plan
    if add_comments:
        actions["add_comments"] = add_comments
    if file_ops:
        actions["file_ops"] = file_ops
    if quorum_synthesis is not None:
        actions["accept_quorum_synthesis"] = quorum_synthesis
    if context_summary is not None:
        actions["append_context_summary"] = context_summary
    if skill_proposals:
        actions["skill_proposals"] = skill_proposals
    return actions


def parse_submit_work(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        if _is_work_object(value):
            return value
        nested = value.get("result") or value.get("content") or value.get("message")
        if nested is not None:
            return parse_submit_work(nested)
    if isinstance(value, list):
        for item in value:
            try:
                return parse_submit_work(item)
            except ValueError:
                continue
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty submit_work output")
        try:
            parsed = json.loads(text)
            return parse_submit_work(parsed)
        except Exception:
            pass
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if fenced:
            return parse_submit_work(fenced.group(1))
        start = text.find("{")
        decoder = json.JSONDecoder()
        while start >= 0:
            try:
                parsed, _ = decoder.raw_decode(text[start:])
                return parse_submit_work(parsed)
            except (json.JSONDecodeError, ValueError):
                start = text.find("{", start + 1)
    raise ValueError("submit_work JSON object not found")


def validate_submit_work(value: Any) -> dict[str, Any]:
    """Parse and validate the complete neutral work contract.

    ``parse_submit_work`` intentionally recovers JSON from provider envelopes.
    Recovery is not proof of contract compliance, so API adapters must call
    this stricter boundary before materialising any operation.
    """
    parsed = parse_submit_work(value)
    _validate_schema_value(parsed, SUBMIT_WORK_SCHEMA, path="submit_work")
    return parsed


def _validate_schema_value(value: Any, schema: dict[str, Any], *, path: str) -> None:
    expected = schema.get("type")
    if expected is not None and not _matches_schema_type(value, expected):
        raise ValueError(f"{path}: expected {expected}, got {type(value).__name__}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: value is outside the allowed enum")
    if isinstance(value, int) and not isinstance(value, bool) and "minimum" in schema:
        if value < int(schema["minimum"]):
            raise ValueError(f"{path}: value is below minimum {schema['minimum']}")
    if isinstance(value, dict):
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for name in schema.get("required") or []:
            if name not in value:
                raise ValueError(f"{path}: missing required property {name}")
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise ValueError(f"{path}: unexpected properties {', '.join(extras)}")
        for name, item in value.items():
            child_schema = properties.get(name)
            if isinstance(child_schema, dict):
                _validate_schema_value(item, child_schema, path=f"{path}.{name}")
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate_schema_value(item, schema["items"], path=f"{path}[{index}]")


def _matches_schema_type(value: Any, expected: Any) -> bool:
    names = expected if isinstance(expected, list) else [expected]
    for name in names:
        if name == "null" and value is None:
            return True
        if name == "object" and isinstance(value, dict):
            return True
        if name == "array" and isinstance(value, list):
            return True
        if name == "string" and isinstance(value, str):
            return True
        if name == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if name == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if name == "boolean" and isinstance(value, bool):
            return True
    return False


def _is_work_object(value: dict[str, Any]) -> bool:
    return isinstance(value.get("ops"), list) and "status" in value and "summary" in value
