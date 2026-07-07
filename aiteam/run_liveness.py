"""Run liveness classification — Paperclip-inspired, no regex.

This module provides a pure liveness classifier and DB-backed evidence
collector for AI Teams runs.  Classification decisions are based entirely
on structured DB artifacts produced during the run (comments, document
revisions, activity events, tool grants, workspace file changes).  Free-text
analysis via regex is intentionally absent from completion decisions.

Liveness states
---------------
advanced        — run produced concrete evidence of real work (workspace
                  changes for engineering runs, or DB artifacts for others)
plan_only       — engineering run produced output but no workspace changes;
                  a continuation will be enqueued (max 2 attempts)
empty_response  — run produced no output and no evidence; continuation
                  will be enqueued (max 2 attempts)
blocked         — run cannot proceed: continuation budget exhausted
completed       — builtin adapter ran successfully with no notable output
failed          — adapter raised an exception or returned status="failed"

All adapters — including API-only ones (openai_api, anthropic_api, gemini_api)
— can now produce workspace changes via write_file / append_file / delete_file
ops in their structured output.  The executor materializes these before
diff_snapshots, so they count as real workspace evidence.  Engineering runs
that produce no workspace changes enter the plan_only / empty_response
continuation loop instead of being immediately blocked.
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

# API-only adapters call remote LLMs in-process; they cannot run shell
# commands or access files directly.  However, they CAN produce workspace
# changes via write_file / append_file / delete_file ops in their structured
# output — the executor materializes these before diff_snapshots.
_API_ONLY_ADAPTERS: frozenset[str] = frozenset(
    {"openai_api", "gemini_api", "anthropic_api", "anthropic_sonnet"}
)

_BUILTIN_ADAPTERS: frozenset[str] = frozenset(
    {"role_builtin", "lead_builtin", "manual"}
)

_ENGINEERING_ROLES: frozenset[str] = frozenset({"engineer", "software_engineer"})

# One-shot Tier 3 scouts: inspect and report in a single run. Once they produce
# output their issue is done — without this the issue lingers 'in_progress' and
# the reconciler re-wakes the scout forever (observed: 24 runs on one issue).
_ONE_SHOT_SCOUT_ROLES: frozenset[str] = frozenset({"file_scout", "web_scout", "test_runner"})

MAX_CONTINUATION_ATTEMPTS: int = 2


# ---------------------------------------------------------------------------
# Evidence dataclass
# ---------------------------------------------------------------------------


@dataclass
class RunEvidence:
    """Structured evidence collected from DB artifacts produced during a run.

    Counts are read from the DB after the run completes — no text analysis.
    ``workspace_files_changed`` is tracked separately and intentionally
    excluded from ``has_concrete_action_evidence`` (workspace operations
    alone do not count as concrete action evidence, per Paperclip semantics).
    """

    issue_comments_created: int = 0      # issue_comments WHERE source_run_id = ?
    document_revisions_created: int = 0  # issue_document_revisions WHERE created_by_run_id = ?
    activity_events_created: int = 0     # activity_log WHERE run_id = ? (excluding comment.created)
    tool_events_created: int = 0         # tool_access WHERE run_id = ? AND NOT startup adapter
    workspace_files_changed: int = 0     # len(delta.created) + len(delta.modified)

    @property
    def has_concrete_action_evidence(self) -> bool:
        """True when the run produced at least one verifiable DB artifact.

        Workspace file changes are excluded — they are tracked separately.
        API-only adapters cannot change files; CLI adapters that only staged
        unrelated changes should not be credited via workspace ops alone.
        """
        return (
            self.issue_comments_created
            + self.document_revisions_created
            + self.activity_events_created
            + self.tool_events_created
        ) > 0


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------


@dataclass
class LivenessResult:
    """Classification output from :func:`classify_run_liveness`."""

    state: str
    """One of: advanced | plan_only | empty_response | blocked | completed | failed"""

    reason: str
    """Short machine-readable reason code."""

    needs_continuation: bool
    """True → caller should enqueue a liveness continuation wakeup."""

    continuation_attempt: int = 0
    """Current attempt number (used when building the idempotency key)."""

    actions_override: dict = field(default_factory=dict)
    """Extra actions to merge into the run's action set post-classification.
    Keys follow the same shape as ``ExecutionResult.actions``:
    ``issue_status``, ``notify_supervisor``, ``add_comments``.
    """


# ---------------------------------------------------------------------------
# Pure classifier
# ---------------------------------------------------------------------------


def classify_run_liveness(
    *,
    run_status: str,
    evidence: RunEvidence,
    adapter_type: str,
    agent_role: str,
    useful_output: bool,
    has_explicit_issue_status: bool = False,
    explicit_blocking_declared: bool = False,
    continuation_attempt: int = 0,
    max_continuation_attempts: int = MAX_CONTINUATION_ATTEMPTS,
) -> LivenessResult:
    """Pure liveness classifier — no regex, no free-text analysis.

    Classification hierarchy (Paperclip-inspired, adapted for AI Teams):

    1. ``failed`` / ``skipped`` → terminal, no continuation
    2. Builtin adapter (role_builtin / lead_builtin / manual) → advanced/completed
    3. Non-engineering role: useful output OR concrete DB evidence → advanced
    4. Engineering role with workspace changes → advanced (auto-closes if no explicit status)
    5. Engineering role that explicitly declared ``blocked`` or ``cancelled`` via ops →
       advanced/completed, NO continuation.  Prevents re-waking an engineer that
       deliberately blocked the issue with a ``set_status`` op.
       NOTE: ``done`` without workspace evidence is NOT covered here — those runs
       fall through to plan_only so the continuation loop can nudge the engineer
       to produce real file changes before the issue is accepted.
    6. Engineering role, no workspace changes, useful output → plan_only (continuable)
    7. Engineering role, no workspace changes, no output → empty_response (continuable)

    All adapters — including API-only (openai_api, anthropic_api, gemini_api) — are
    treated the same.  The executor materializes write_file/append_file/delete_file
    ops before diff_snapshots, so API-only agents that use file ops will land in
    rule 4 (advanced) rather than the continuation loop.

    Parameters
    ----------
    run_status:
        The adapter's execution status (``completed`` / ``failed`` / ``skipped``).
    evidence:
        Structured counts collected from DB artifacts.
    adapter_type:
        The adapter type used for this run (e.g. ``openai_api``, ``subscription_cli``).
    agent_role:
        The agent's role string (e.g. ``engineer``, ``lead``).
    useful_output:
        True when ``result.output`` is non-empty and non-trivial.
    has_explicit_issue_status:
        True when the adapter already set *any* ``issue_status`` in its actions.
        Used in rule 4 to suppress auto-closing (auto-setting ``done``) when the
        adapter already declared its own status.
    explicit_blocking_declared:
        True when the adapter set ``issue_status`` to ``"blocked"`` or ``"cancelled"``
        via a ``set_status`` op.  Triggers rule 5: skips the continuation loop so the
        deliberate block is not overridden by a liveness wakeup.
        Distinct from ``has_explicit_issue_status`` because ``done`` without workspace
        evidence should still go through plan_only, not bypass the continuation loop.
    continuation_attempt:
        Current continuation attempt number (0 = first/original run).
    max_continuation_attempts:
        Maximum allowed continuations before escalating to blocked.
    """
    role = str(agent_role or "").strip().lower()
    is_engineering = role in _ENGINEERING_ROLES
    is_builtin = adapter_type in _BUILTIN_ADAPTERS

    # 1. Terminal execution status
    if run_status == "failed":
        return LivenessResult(state="failed", reason="run_failed", needs_continuation=False)
    if run_status == "skipped":
        return LivenessResult(state="completed", reason="run_skipped", needs_continuation=False)

    # 2. Builtin adapters: deterministic output; if they completed, they're done
    if is_builtin:
        state = "advanced" if (useful_output or evidence.has_concrete_action_evidence) else "completed"
        return LivenessResult(
            state=state,
            reason="builtin_adapter_completed",
            needs_continuation=False,
        )

    # 3. (Removed) API-only adapters are no longer immediately blocked.
    #    They can now produce workspace changes via write_file / append_file /
    #    delete_file ops.  Engineering runs without workspace changes fall
    #    through to the plan_only / empty_response continuation loop below.

    # 4. Non-engineering role: comments and plan text are sufficient evidence.
    #    (Text output from a lead/reviewer/qa is the expected delivery format.)
    if not is_engineering:
        if useful_output or evidence.has_concrete_action_evidence:
            # One-shot scouts finish in a single run — close the issue so the
            # reconciler stops re-waking them. Only when the agent didn't
            # already declare its own status via a set_status op.
            override: dict = {}
            if role in _ONE_SHOT_SCOUT_ROLES and not has_explicit_issue_status:
                override = {
                    "issue_status": "done",
                    "notify_supervisor": True,
                    "_liveness_state": "advanced",
                    "_liveness_reason": "scout_one_shot_report_complete",
                }
            return LivenessResult(
                state="advanced",
                reason="non_engineering_role_with_output_or_evidence",
                needs_continuation=False,
                actions_override=override,
            )
        return _continuable_or_exhausted(
            state_name="empty_response",
            reason="no_output_no_evidence",
            continuation_attempt=continuation_attempt,
            max_continuation_attempts=max_continuation_attempts,
            exhausted_comment="Bloqueado: sin respuesta ni evidencia tras múltiples intentos.",
        )

    # 5. Engineering role: workspace changes are the gold standard for delivery.
    if evidence.workspace_files_changed > 0:
        override: dict = {
            "notify_supervisor": True,
            "_liveness_state": "advanced",
            "_liveness_reason": "workspace_changes_detected",
        }
        if not has_explicit_issue_status:
            # Auto-close the issue when workspace evidence exists and the
            # adapter didn't explicitly declare a status.
            override["issue_status"] = "done"
        return LivenessResult(
            state="advanced",
            reason="workspace_changes_detected",
            needs_continuation=False,
            actions_override=override,
        )

    # 6. If the engineer explicitly declared a blocking terminal status
    #    (``blocked`` or ``cancelled``) via a set_status op, honour that and do NOT
    #    re-enqueue a liveness continuation.  A continuation would silently reset the
    #    issue back to todo and re-wake the engineer — defeating the deliberate block.
    #
    #    ``done`` without workspace evidence is intentionally excluded: an engineer
    #    that claims done but produced no files should still enter the plan_only loop
    #    so it is nudged to provide real workspace output.
    if explicit_blocking_declared:
        state = "advanced" if (useful_output or evidence.has_concrete_action_evidence) else "completed"
        return LivenessResult(
            state=state,
            reason="explicit_blocking_declared",
            needs_continuation=False,
        )

    # 7. Engineering, no workspace changes, but useful output → plan_only.
    #    Give the agent a bounded number of continuation attempts.
    if useful_output:
        return _continuable_or_exhausted(
            state_name="plan_only",
            reason="output_without_workspace_changes",
            continuation_attempt=continuation_attempt,
            max_continuation_attempts=max_continuation_attempts,
            exhausted_comment=(
                f"Bloqueado: {continuation_attempt} continuaciones con solo texto/plan "
                "sin cambios verificables en el workspace. "
                "Se requiere acción concreta (crear/modificar archivos) o intervención manual."
            ),
        )

    # 8. Engineering, no workspace changes, no output → empty_response.
    return _continuable_or_exhausted(
        state_name="empty_response",
        reason="no_output_no_workspace_changes",
        continuation_attempt=continuation_attempt,
        max_continuation_attempts=max_continuation_attempts,
        exhausted_comment=(
            f"Bloqueado: sin evidencia concreta tras {continuation_attempt} intentos. "
            "La issue requiere intervención manual o reasignación."
        ),
    )


def _continuable_or_exhausted(
    *,
    state_name: str,
    reason: str,
    continuation_attempt: int,
    max_continuation_attempts: int,
    exhausted_comment: str,
) -> LivenessResult:
    """Return a continuable result, or a blocked result if attempts are exhausted."""
    if continuation_attempt >= max_continuation_attempts:
        exhausted_reason = f"{state_name}_exhausted_at_attempt_{continuation_attempt}"
        return LivenessResult(
            state="blocked",
            reason=exhausted_reason,
            needs_continuation=False,
            actions_override={
                "issue_status": "blocked",
                "notify_supervisor": True,
                "_liveness_state": "blocked",
                "_liveness_reason": exhausted_reason,
                "add_comments": [exhausted_comment],
            },
        )
    return LivenessResult(
        state=state_name,
        reason=reason,
        needs_continuation=True,
        continuation_attempt=continuation_attempt,
    )


# ---------------------------------------------------------------------------
# DB evidence collector
# ---------------------------------------------------------------------------


def collect_run_evidence(
    db_path: Path,
    *,
    run_id: str,
    workspace_files_changed: int = 0,
) -> RunEvidence:
    """Query the DB for evidence artifacts produced during *run_id*.

    Side-effect-free: read-only.  Safe to call any time after the run has
    written its output comment and applied its result actions.
    """
    comments = 0
    revisions = 0
    activity = 0
    tools = 0

    with contextlib.closing(_db_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM issue_comments WHERE source_run_id = ?",
            (run_id,),
        ).fetchone()
        comments = int(row["n"]) if row else 0

        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM issue_document_revisions WHERE created_by_run_id = ?",
                (run_id,),
            ).fetchone()
            revisions = int(row["n"]) if row else 0
        except Exception:
            revisions = 0

        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM activity_log
                WHERE run_id = ?
                  AND action NOT IN ('comment.created', 'adapter.startup')
                """,
                (run_id,),
            ).fetchone()
            activity = int(row["n"]) if row else 0
        except Exception:
            activity = 0

        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM tool_access
                WHERE run_id = ?
                  AND decision = 'allowed'
                  AND tool_name NOT LIKE 'adapter:%'
                """,
                (run_id,),
            ).fetchone()
            tools = int(row["n"]) if row else 0
        except Exception:
            tools = 0

    return RunEvidence(
        issue_comments_created=comments,
        document_revisions_created=revisions,
        activity_events_created=activity,
        tool_events_created=tools,
        workspace_files_changed=workspace_files_changed,
    )


def _db_connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
