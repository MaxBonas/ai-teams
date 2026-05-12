from __future__ import annotations

import json
import re
from typing import Any


OP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": [
                "add_comment",
                "update_plan",
                "create_issue",
                "create_interaction",
                "set_status",
                "notify_supervisor",
                "write_file",
                "append_file",
                "delete_file",
            ],
        },
        "body": {"type": "string"},
        "path": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "role": {"type": "string", "enum": ["lead", "engineer", "reviewer", "qa"]},
        "complexity": {"type": "string", "enum": ["low", "medium", "high"]},
        "kind": {"type": "string", "enum": ["suggest_tasks", "request_confirmation"]},
        "summary": {"type": "string"},
        "idempotency_key": {"type": "string"},
        "status": {"type": "string", "enum": ["done", "in_progress", "todo", "cancelled"]},
        # payload is used exclusively with create_interaction.
        # Must include a 'reason' field so the executor can route the response correctly.
        # Example: {"reason": "lead_wants_file_read", "parent_issue_id": "..."}
        "payload": {"type": "object"},
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
                    "path": {"type": ["string", "null"]},
                    "title": {"type": ["string", "null"]},
                    "description": {"type": ["string", "null"]},
                    "role": {"type": ["string", "null"], "enum": ["lead", "engineer", "reviewer", "qa", None]},
                    "complexity": {"type": ["string", "null"], "enum": ["low", "medium", "high", None]},
                    "kind": {"type": ["string", "null"], "enum": ["suggest_tasks", "request_confirmation", None]},
                    "summary": {"type": ["string", "null"]},
                    "idempotency_key": {"type": ["string", "null"]},
                    "status": {
                        "type": ["string", "null"],
                        "enum": ["done", "in_progress", "todo", "cancelled", None],
                    },
                    # payload for create_interaction — must include 'reason'.
                    # Strictly defined for OpenAI structured output compatibility.
                    "payload": {
                        "type": ["object", "null"],
                        "properties": {
                            "reason": {"type": ["string", "null"]},
                            "parent_issue_id": {"type": ["string", "null"]},
                        },
                        "required": ["reason", "parent_issue_id"],
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "type",
                    "body",
                    "path",
                    "title",
                    "description",
                    "role",
                    "complexity",
                    "kind",
                    "summary",
                    "idempotency_key",
                    "status",
                    "payload",
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


def build_execution_contract() -> str:
    return (
        "\n\n## Execution contract\n"
        "- Read AITEAM_WAKE_PAYLOAD_JSON for issue context.\n"
        "- Return exactly one JSON object matching the submit_work schema.\n"
        "- Keep ops minimal and targeted; prefer add_comment to report progress.\n"
        "- If you are the Lead and you create, revise, or mention a project plan, use update_plan; "
        "the UI Plan tab reads the durable plan document, not plan-shaped comments.\n"
        "- Use update_plan only when the plan has materially changed.\n"
        "- If you are the Lead on a normal full-team software build, default to Engineer + Reviewer; "
        "QA is optional and only needed for runtime verification. Skip Reviewer only for low-risk work and state why.\n"
        "- Keep accountability explicit: each delegated issue should name who reports to whom and who accepts the result.\n"
        "- After delegating child issues, wait for concrete child reports before waking/polling the Lead again.\n"
        "- Use create_issue to delegate sub-work; use set_status: done when complete.\n"
        "- Use notify_supervisor after setting status to done when reporting up the chain.\n"
        "\n## create_interaction — mandatory payload.reason\n"
        "- Every create_interaction op MUST include payload: {\"reason\": \"<name>\"} so the executor can route "
        "the user's response correctly. Without 'reason', the interaction will be silently skipped.\n"
        "- Common reasons: 'lead_wants_file_read', 'initial_cycle_ready', 'child_blocked_requires_action', 'reviewer_fix_cycle_limit'.\n"
        "- LIMIT: only ONE create_interaction per run. The executor will silently drop any extras. "
        "If you need multiple user decisions, ask the most important one now and ask the rest in future heartbeats.\n"
        "- 'lead_wants_file_read' is ONLY for blocked Tier 3 scouts (file_scout, web_scout, context_curator). "
        "Never use it for blocked engineers — engineers already receive workspace_files automatically.\n"
        "- Example: {\"type\": \"create_interaction\", \"kind\": \"request_confirmation\", "
        "\"title\": \"...\", \"summary\": \"...\", \"payload\": {\"reason\": \"lead_wants_file_read\"}, "
        "\"idempotency_key\": \"lead:file-read-request:<issue_id>\"}\n"
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
        "\n## Tier 3 scout roles (file_scout, web_scout, context_curator)\n"
        "- Your only job is to read inputs (workspace_files, web, or issue thread) and write one summary comment.\n"
        "- Use add_comment for your findings, then set_status: done. Close in the same run.\n"
        "- Do NOT create sub-issues, interactions, update_plan, or write files.\n"
        "- Append the ---AGENT-REPORT--- block as the last part of your comment before setting status.\n"
        "\n## Workspace listing (fallback — rarely seen)\n"
        "- 'workspace_listing' is a legacy fallback included only when 'workspace_files' is absent.\n"
        "- Each entry has 'path' and 'size_bytes' only (no content).\n"
        "- In practice, Engineers always receive 'workspace_files' (full content) and will not see 'workspace_listing'.\n"
        "- If you do see 'workspace_listing', use it to see which files exist, then continue work without recreating them.\n"
    )


def ops_to_actions(ops: list[dict[str, Any]]) -> dict[str, Any]:
    actions: dict[str, Any] = {}
    interactions: list[dict[str, Any]] = []
    create_issues: list[dict[str, Any]] = []
    update_plan: dict[str, Any] | None = None
    add_comments: list[str] = []
    file_ops: list[dict[str, Any]] = []

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
            }
        elif op_type == "create_issue":
            create_issues.append(
                {
                    "title": str(op.get("title") or ""),
                    "description": str(op.get("description") or ""),
                    "role": str(op.get("role") or "engineer"),
                    "complexity": str(op.get("complexity") or "medium"),
                }
            )
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

    if interactions:
        actions["interactions"] = interactions
    if create_issues:
        actions["create_issues"] = create_issues
    if update_plan is not None:
        actions["update_plan"] = update_plan
    if add_comments:
        actions["add_comments"] = add_comments
    if file_ops:
        actions["file_ops"] = file_ops
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
        end = text.rfind("}")
        if start >= 0 and end > start:
            return parse_submit_work(text[start : end + 1])
    raise ValueError("submit_work JSON object not found")


def _is_work_object(value: dict[str, Any]) -> bool:
    return isinstance(value.get("ops"), list) and "status" in value and "summary" in value
