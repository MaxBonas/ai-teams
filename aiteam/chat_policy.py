from __future__ import annotations

from dataclasses import dataclass, field

CHAT_VALIDATION_OWNER = "chat_policy"
CHAT_VALIDATION_CONTRACT_VERSION = "chat_policy_v1"


@dataclass(slots=True)
class ChatPolicyEvent:
    event_type: str
    payload: dict[str, object]


@dataclass(slots=True)
class RunTypePolicy:
    productivity_threshold: int
    passes_by_reasoning: bool
    is_context_query: bool


@dataclass(slots=True)
class ChatValidationContract:
    owner: str = CHAT_VALIDATION_OWNER
    contract_version: str = CHAT_VALIDATION_CONTRACT_VERSION
    final_validation_layer: str = CHAT_VALIDATION_OWNER
    phase_quality_gate_mode: str = "delegated_to_chat_policy"
    phase_evidence_gate_mode: str = "delegated_to_chat_policy"
    policy_review_mode: str = "soft_signals"
    interactive_chat: bool = True
    skip_quality_gates: bool = True
    skip_evidence_gate: bool = True
    require_execution_plan: bool = False

    def as_metadata(self) -> dict[str, object]:
        return {
            "interactive_chat": self.interactive_chat,
            "skip_quality_gates": self.skip_quality_gates,
            "skip_evidence_gate": self.skip_evidence_gate,
            "validation_owner": self.owner,
            "validation_contract_version": self.contract_version,
            "final_validation_layer": self.final_validation_layer,
            "phase_quality_gate_mode": self.phase_quality_gate_mode,
            "phase_evidence_gate_mode": self.phase_evidence_gate_mode,
            "policy_review_mode": self.policy_review_mode,
            "require_execution_plan": self.require_execution_plan,
        }


@dataclass(slots=True)
class ChatPolicyInput:
    task_id: str
    run_type: str
    final_state: str
    productivity_status: str
    next_action_hint: str
    strict_mode: bool
    continuation_requested: bool
    allow_low_productivity_override: bool
    lead_advisory_mode: bool
    live_mode_required: bool
    execution_mode: str
    execution_steps: int
    artifact_created: int
    artifact_modified: int
    productivity_score: int
    reasoning_score: int
    evidence_gate_failures: list[str] = field(default_factory=list)
    semantic_gate_failures: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ChatPolicyOutcome:
    final_state: str
    productivity_status: str
    next_action_hint: str
    live_mode_rejected: bool
    semantic_gate_applied: bool
    evidence_gate_applied: bool
    strict_mode_applied: bool
    low_productivity_rejected: bool
    low_productivity_override: bool
    productivity_threshold: int
    policy_review_required: bool = False
    policy_signals: list[str] = field(default_factory=list)
    events: list[ChatPolicyEvent] = field(default_factory=list)


def build_chat_validation_contract(
    *,
    require_execution_plan: bool = False,
) -> ChatValidationContract:
    return ChatValidationContract(
        require_execution_plan=bool(require_execution_plan),
    )


def build_chat_task_policy_metadata(
    *,
    require_execution_plan: bool = False,
) -> dict[str, object]:
    return build_chat_validation_contract(
        require_execution_plan=require_execution_plan,
    ).as_metadata()


def uses_chat_policy(metadata: dict[str, object] | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    owner = str(metadata.get("validation_owner", "") or "").strip().lower()
    final_layer = str(metadata.get("final_validation_layer", "") or "").strip().lower()
    return owner == CHAT_VALIDATION_OWNER or final_layer == CHAT_VALIDATION_OWNER


def resolve_run_type_policy(run_type: str, reasoning_score: int) -> RunTypePolicy:
    normalized = str(run_type or "").strip().lower()
    if normalized == "context_recovery":
        return RunTypePolicy(
            productivity_threshold=0,
            passes_by_reasoning=reasoning_score >= 40,
            is_context_query=True,
        )
    if normalized == "planning":
        return RunTypePolicy(
            productivity_threshold=0,
            passes_by_reasoning=reasoning_score >= 50,
            is_context_query=True,
        )
    return RunTypePolicy(
        productivity_threshold=35,
        passes_by_reasoning=False,
        is_context_query=False,
    )


def evaluate_chat_policy(
    policy: ChatPolicyInput,
    run_type_policy: RunTypePolicy,
) -> ChatPolicyOutcome:
    final_state = str(policy.final_state or "")
    productivity_status = str(policy.productivity_status or "weak")
    next_action_hint = str(policy.next_action_hint or "")
    policy_signals: list[str] = []
    events: list[ChatPolicyEvent] = []

    live_mode_rejected = False
    semantic_gate_applied = False
    evidence_gate_applied = False
    strict_mode_applied = False
    low_productivity_rejected = False
    policy_review_required = False

    if policy.semantic_gate_failures:
        semantic_gate_applied = True
        if policy.lead_advisory_mode:
            policy_signals.append("semantic_gate_failed")
            productivity_status = "weak"
            next_action_hint = (
                "Advisory mode activo: review/qa detectaron una contradiccion "
                "semantica, pero el Lead decidio cerrar sin bloquear."
            )
            events.append(
                ChatPolicyEvent(
                    event_type="chat_policy_signal",
                    payload={
                        "task_id": policy.task_id,
                        "signal": "semantic_gate_failed",
                        "failures": list(policy.semantic_gate_failures),
                        "advisory_mode": True,
                    },
                )
            )
        else:
            final_state = "rejected"
            policy_signals.append("semantic_gate_failed")
            policy_review_required = True
            productivity_status = "weak"
            next_action_hint = (
                "Señal de policy: review/qa detectaron un bloqueo semantico "
                "real. La corrida no puede cerrarse como exitosa hasta resolverlo "
                "o degradarla explicitamente en advisory."
            )
            events.append(
                ChatPolicyEvent(
                    event_type="chat_policy_signal",
                    payload={
                        "task_id": policy.task_id,
                        "signal": "semantic_gate_failed",
                        "failures": list(policy.semantic_gate_failures),
                        "advisory_mode": False,
                        "review_required": True,
                        "final_state": final_state,
                    },
                )
            )

    if policy.live_mode_required and policy.execution_mode != "live":
        if policy.lead_advisory_mode:
            policy_signals.append("live_mode_required_non_live")
            next_action_hint = (
                "Advisory mode activo: el entorno requiere live mode, "
                "pero el Lead decidió cerrar sin bloquear la corrida."
            )
            events.append(
                ChatPolicyEvent(
                    event_type="chat_policy_signal",
                    payload={
                        "task_id": policy.task_id,
                        "signal": "live_mode_required_non_live",
                        "execution_mode": policy.execution_mode,
                        "advisory_mode": True,
                    },
                )
            )
        else:
            policy_signals.append("live_mode_required_non_live")
            policy_review_required = True
            productivity_status = "weak"
            next_action_hint = (
                "Señal de policy: el entorno requiere live mode, pero la corrida "
                "no fue live. El Lead puede continuar, delegar más verificación "
                "o cerrar en advisory."
            )
            events.append(
                ChatPolicyEvent(
                    event_type="chat_policy_signal",
                    payload={
                        "task_id": policy.task_id,
                        "signal": "live_mode_required_non_live",
                        "execution_mode": policy.execution_mode,
                        "required": True,
                        "advisory_mode": False,
                        "review_required": True,
                    },
                )
            )

    if policy.evidence_gate_failures:
        evidence_gate_applied = True
        if policy.lead_advisory_mode:
            policy_signals.append("evidence_gate_failed")
            next_action_hint = (
                "Advisory mode activo: hay fallos del evidence gate, pero el "
                "Lead decidió cerrar como advisory."
            )
            events.append(
                ChatPolicyEvent(
                    event_type="chat_policy_signal",
                    payload={
                        "task_id": policy.task_id,
                        "signal": "evidence_gate_failed",
                        "failures": list(policy.evidence_gate_failures),
                        "advisory_mode": True,
                    },
                )
            )
        else:
            policy_signals.append("evidence_gate_failed")
            policy_review_required = True
            productivity_status = "weak"
            next_action_hint = (
                "Señal de policy: el evidence gate detectó evidencia débil o "
                "incompleta. El Lead puede continuar, replanificar o cerrar en advisory."
            )
            events.append(
                ChatPolicyEvent(
                    event_type="chat_policy_signal",
                    payload={
                        "task_id": policy.task_id,
                        "signal": "evidence_gate_failed",
                        "failures": list(policy.evidence_gate_failures),
                        "execution_mode": policy.execution_mode,
                        "execution_steps": policy.execution_steps,
                        "artifact_created": policy.artifact_created,
                        "artifact_modified": policy.artifact_modified,
                        "advisory_mode": False,
                        "review_required": True,
                    },
                )
            )

    if policy.strict_mode and not policy.continuation_requested:
        has_minimum_evidence = (
            policy.artifact_created + policy.artifact_modified
        ) > 0 or policy.execution_steps > 0
        mode_is_reliable = policy.execution_mode in {"live", "hybrid"}
        if final_state == "completed" and (
            not has_minimum_evidence or not mode_is_reliable
        ):
            if policy.lead_advisory_mode:
                policy_signals.append("strict_mode_requires_more_evidence")
                if not mode_is_reliable:
                    next_action_hint = (
                        "Advisory mode activo: strict mode detectó modo "
                        "simulado, pero el Lead decidió cerrar sin bloquear."
                    )
                else:
                    next_action_hint = (
                        "Advisory mode activo: strict mode detectó evidencia "
                        "insuficiente, pero el Lead decidió cerrar sin bloquear."
                    )
                events.append(
                    ChatPolicyEvent(
                        event_type="chat_policy_signal",
                        payload={
                            "task_id": policy.task_id,
                            "signal": "strict_mode_requires_more_evidence",
                            "execution_mode": policy.execution_mode,
                            "advisory_mode": True,
                        },
                    )
                )
            else:
                policy_signals.append("strict_mode_requires_more_evidence")
                policy_review_required = True
                productivity_status = "weak"
                if not mode_is_reliable:
                    next_action_hint = (
                        "Señal de policy: strict mode detectó modo simulado. "
                        "El Lead puede pedir más evidencia o cerrar en advisory."
                    )
                else:
                    next_action_hint = (
                        "Señal de policy: strict mode detectó evidencia insuficiente. "
                        "El Lead puede continuar, delegar verificación o cerrar en advisory."
                    )
                events.append(
                    ChatPolicyEvent(
                        event_type="chat_policy_signal",
                        payload={
                            "task_id": policy.task_id,
                            "signal": "strict_mode_requires_more_evidence",
                            "reason": "simulated_mode_or_missing_evidence",
                            "execution_steps": policy.execution_steps,
                            "artifact_created": policy.artifact_created,
                            "artifact_modified": policy.artifact_modified,
                            "execution_mode": policy.execution_mode,
                            "advisory_mode": False,
                            "review_required": True,
                        },
                    )
                )

    # continuation_requested permite override de productividad baja, SALVO cuando
    # la run no produjo absolutamente ningún artefacto ni execution_step: en ese
    # caso el override silenciaría un fallo total y confundiría al usuario.
    _zero_output_run = (
        policy.execution_steps == 0
        and policy.artifact_created == 0
        and policy.artifact_modified == 0
    )
    low_productivity_override = (
        policy.allow_low_productivity_override
        or (policy.continuation_requested and not _zero_output_run)
        or (
            run_type_policy.is_context_query
            and run_type_policy.passes_by_reasoning
        )
    )
    if (
        policy.productivity_score < run_type_policy.productivity_threshold
        and not low_productivity_override
        and final_state not in {"failed", "rejected"}
    ):
        if policy.lead_advisory_mode:
            policy_signals.append("low_productivity_below_threshold")
            productivity_status = "weak"
            next_action_hint = (
                f"Advisory mode activo: productividad<"
                f"{run_type_policy.productivity_threshold}, "
                "pero el Lead decidió cerrar sin bloquear."
            )
            events.append(
                ChatPolicyEvent(
                    event_type="chat_policy_signal",
                    payload={
                        "task_id": policy.task_id,
                        "signal": "low_productivity_below_threshold",
                        "productivity_score": policy.productivity_score,
                        "threshold": run_type_policy.productivity_threshold,
                        "advisory_mode": True,
                    },
                )
            )
        else:
            policy_signals.append("low_productivity_below_threshold")
            policy_review_required = True
            productivity_status = "weak"
            next_action_hint = (
                f"Señal de policy: productividad<"
                f"{run_type_policy.productivity_threshold}. "
                "El Lead puede continuar, recortar alcance o cerrar en advisory."
            )
            events.append(
                ChatPolicyEvent(
                    event_type="chat_policy_signal",
                    payload={
                        "task_id": policy.task_id,
                        "signal": "low_productivity_below_threshold",
                        "productivity_score": policy.productivity_score,
                        "threshold": run_type_policy.productivity_threshold,
                        "override": False,
                        "advisory_mode": False,
                        "review_required": True,
                    },
                )
            )
    elif (
        policy.productivity_score < run_type_policy.productivity_threshold
        and low_productivity_override
    ):
        events.append(
            ChatPolicyEvent(
                event_type="chat_low_productivity_override",
                payload={
                    "task_id": policy.task_id,
                    "productivity_score": policy.productivity_score,
                    "threshold": run_type_policy.productivity_threshold,
                    "override": True,
                },
            )
        )

    return ChatPolicyOutcome(
        final_state=final_state,
        productivity_status=productivity_status,
        next_action_hint=next_action_hint,
        live_mode_rejected=live_mode_rejected,
        semantic_gate_applied=semantic_gate_applied,
        evidence_gate_applied=evidence_gate_applied,
        strict_mode_applied=strict_mode_applied,
        low_productivity_rejected=low_productivity_rejected,
        low_productivity_override=low_productivity_override,
        productivity_threshold=run_type_policy.productivity_threshold,
        policy_review_required=policy_review_required,
        policy_signals=policy_signals,
        events=events,
    )
