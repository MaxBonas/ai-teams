"""Anthropic API adapter for AI Teams.

Calls the Anthropic Messages API with the agent's skill as system prompt and
the wake payload as user message.  Returns a structured ExecutionResult whose
``actions`` dict is consumed by RunExecutor._apply_result_actions.

The model is asked to call the ``submit_work`` tool — a structured contract
that maps directly to the actions the executor understands:

  ops:
    - {type: "add_comment",    body: "..."}
    - {type: "update_plan",    title: "...", body: "..."}
    - {type: "create_issue",   title: "...", description: "...",
                               role: "engineer|reviewer|qa|lead",
                               complexity: "low|medium|high"}
    - {type: "create_interaction",
                               kind: "suggest_tasks|request_confirmation",
                               title: "...", summary: "...",
                               idempotency_key: "..." (optional)}
    - {type: "set_status",     status: "done|in_progress|todo"}
    - {type: "notify_supervisor"}

Usage:
  Register as adapter_type="anthropic_api" in build_default_registry().
  The agent record must set adapter_type="anthropic_api".
  ANTHROPIC_API_KEY must be set in the environment.
"""

from __future__ import annotations

import json
import os
from typing import Any

from aiteam.adapters.registry import AdapterDescriptor, ExecutionResult, StaticAdapterRuntime
from aiteam.pricing import estimate_cost_from_usage
from aiteam.adapters.work_contract import (
    SUBMIT_WORK_TOOL,
    build_execution_contract,
    ops_to_actions,
)
from aiteam.quorum_quality import quorum_audit_contract_instruction


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------

class AnthropicApiRuntime:
    """Calls the Anthropic Messages API and returns structured actions."""

    def __init__(
        self,
        descriptor: AdapterDescriptor,
        *,
        model: str = "claude-opus-4-5",
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> None:
        self.descriptor = descriptor
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        """Same as StaticAdapterRuntime — env vars for subprocess compatibility."""
        import os as _os
        issue_id = str(wake_context.get("issue_id", "") or "")
        reason = str(wake_context.get("reason", "") or "")
        comment_id = str(wake_context.get("comment_id", "") or "")
        agent_role = str(wake_context.get("agent_role", "") or "")
        agent_skill = str(wake_context.get("agent_skill", "") or "")
        wake_payload_json = str(wake_context.get("wake_payload_json", "") or "")
        api_url = _os.environ.get("AITEAM_API_URL", "http://localhost:8000")
        interaction_id = str(wake_context.get("interaction_id", "") or "")
        interaction_action = str(wake_context.get("interaction_action", "") or "")
        interaction_kind = str(wake_context.get("interaction_kind", "") or "")
        return {
            "AITEAM_RUN_ID": run_id,
            "AITEAM_TASK_ID": issue_id,
            "AITEAM_WAKE_REASON": reason,
            "AITEAM_WAKE_COMMENT_ID": comment_id,
            "AITEAM_AGENT_ROLE": agent_role,
            "AITEAM_AGENT_SKILL": agent_skill,
            "AITEAM_WAKE_PAYLOAD_JSON": wake_payload_json,
            "AITEAM_API_URL": api_url,
            "AITEAM_INTERACTION_ID": interaction_id,
            "AITEAM_INTERACTION_ACTION": interaction_action,
            "AITEAM_INTERACTION_KIND": interaction_kind,
        }

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        api_key = env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return ExecutionResult(
                status="failed",
                error="ANTHROPIC_API_KEY not set",
                error_code="missing_api_key",
            )

        skill = env.get("AITEAM_AGENT_SKILL", "").strip()
        wake_payload_raw = env.get("AITEAM_WAKE_PAYLOAD_JSON", "").strip()
        agent_role = env.get("AITEAM_AGENT_ROLE", "").strip()

        system_prompt = _build_system(skill, agent_role)
        user_message = _build_user(wake_payload_raw, run)

        try:
            import anthropic  # local import so the package is optional at module load time
            client = anthropic.Anthropic(api_key=api_key, timeout=self._timeout)

            response = client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
                tools=[SUBMIT_WORK_TOOL],
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            return ExecutionResult(status="failed", error=str(exc), error_code="api_error")

        # Extract tool call
        tool_use_block = next(
            (blk for blk in response.content if blk.type == "tool_use" and blk.name == "submit_work"),
            None,
        )
        if tool_use_block is None:
            raw_text = " ".join(
                blk.text for blk in response.content if hasattr(blk, "text")
            ).strip()
            return ExecutionResult(
                status="failed",
                output=raw_text[:2048] or None,
                error="model did not call submit_work",
                error_code="no_tool_call",
            )

        try:
            work: dict[str, Any] = (
                tool_use_block.input
                if isinstance(tool_use_block.input, dict)
                else json.loads(str(tool_use_block.input))
            )
        except Exception as exc:
            return ExecutionResult(status="failed", error=f"bad tool input: {exc}", error_code="tool_parse_error")

        ops: list[dict[str, Any]] = work.get("ops") or []
        exec_status = str(work.get("status") or "completed")
        summary = str(work.get("summary") or "")

        # Build actions dict for _apply_result_actions
        actions = ops_to_actions(ops)

        # Usage / cost
        usage_obj = response.usage
        usage = {
            "input_tokens": usage_obj.input_tokens,
            "output_tokens": usage_obj.output_tokens,
        } if usage_obj else None
        cost_cents = estimate_cost_from_usage("anthropic", self._model, usage_obj)

        return ExecutionResult(
            status=exec_status if exec_status in {"completed", "failed", "skipped"} else "completed",
            output=summary or None,
            usage=usage,
            actual_cost_cents=cost_cents,
            actions=actions,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_system(skill: str, role: str) -> str:
    parts: list[str] = []
    if skill:
        parts.append(skill)
    else:
        parts.append(
            f"You are an AI Teams agent with role '{role or 'assistant'}'. "
            "Your job is to complete the delegated work described in the user message, "
            "then call submit_work with the operations needed."
        )
    parts.append(build_execution_contract())
    if role.strip().lower() == "quorum_auditor":
        parts.append(
            "QUORUM AUDITOR — CONTRATO ESTRICTO:\n"
            "Solo puedes emitir ops add_comment y set_status done. No implementes, no edites, "
            "no delegues y no sintetices el plan.\n"
            + quorum_audit_contract_instruction()
        )
    return "\n".join(parts)


def _build_user(wake_payload_raw: str, run: dict[str, Any]) -> str:
    parts: list[str] = []
    if wake_payload_raw:
        try:
            payload = json.loads(wake_payload_raw)
            quorum_review = payload.get("quorum_review")
            if isinstance(quorum_review, dict):
                return (
                    "## Auditoría quorum obligatoria\n"
                    "Audita el objetivo congelado y Plan A siguientes. Cumple literalmente el contrato "
                    "QUORUM AUDITOR del system prompt y llama submit_work al terminar.\n\n"
                    + json.dumps({"quorum_review": quorum_review}, ensure_ascii=False)
                )
            issue = payload.get("issue") or {}
            title = issue.get("title") or run.get("issue_id") or "Unknown task"
            description = issue.get("description") or ""
            comments = payload.get("comments") or []
            pending = payload.get("pending_interactions") or []
            plan = payload.get("plan_document") or {}

            parts.append(f"## Task: {title}")
            if description:
                parts.append(f"\n{description}")

            if plan.get("body"):
                parts.append(f"\n### Current plan\n{plan['body'][:1500]}")

            if comments:
                parts.append("\n### Thread (recent first)")
                for c in comments[-8:]:
                    author = c.get("author_agent_id") or c.get("author_user_id") or "system"
                    body = (c.get("body") or "")[:300]
                    parts.append(f"**{author}**: {body}")

            if pending:
                parts.append("\n### Pending interactions")
                for p in pending:
                    parts.append(f"- [{p.get('kind')}] {p.get('title') or p.get('summary') or ''}")

            parts.append(f"\n### Context snapshot\n```json\n{wake_payload_raw[:800]}\n```")
        except Exception:
            parts.append(f"Context (raw):\n{wake_payload_raw[:2000]}")
    else:
        issue_id = str(run.get("issue_id") or "")
        parts.append(f"Complete the assigned work for issue: {issue_id or 'unknown'}")

    parts.append("\nCall submit_work when done.")
    return "\n".join(parts)

