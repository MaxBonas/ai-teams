from __future__ import annotations

from dataclasses import dataclass

from aiteam.types import TaskState, WorkTask


@dataclass
class PilotThresholds:
    min_task_success_rate: float = 85.0
    min_gate_pass_rate: float = 85.0
    min_pro_share_percent: float = 60.0
    max_compliance_violations: int = 0


@dataclass
class PilotCheckResult:
    ok: bool
    messages: list[str]


def compute_pilot_metrics(tasks: list[WorkTask], event_summary: dict) -> dict[str, float | int]:
    parent_tasks = [task for task in tasks if not bool(task.metadata.get("is_gate", False))]
    gate_tasks = [task for task in tasks if bool(task.metadata.get("is_gate", False))]

    parent_total = len(parent_tasks)
    parent_completed = sum(1 for task in parent_tasks if task.state == TaskState.COMPLETED)
    gate_total = len(gate_tasks)
    gate_completed = sum(1 for task in gate_tasks if task.state == TaskState.COMPLETED)

    task_success_rate = _safe_percent(parent_completed, parent_total)
    gate_pass_rate = _safe_percent(gate_completed, gate_total)

    task_execution_total = int(event_summary.get("task_execution_total", 0))
    channels = event_summary.get("channels", {})
    subscription_count = int(channels.get("subscription", 0)) if isinstance(channels, dict) else 0
    api_count = int(channels.get("api", 0)) if isinstance(channels, dict) else 0

    pro_share_percent = _safe_percent(subscription_count, task_execution_total)
    api_fallback_rate_percent = _safe_percent(api_count, task_execution_total)

    return {
        "parent_total": parent_total,
        "parent_completed": parent_completed,
        "task_success_rate": task_success_rate,
        "gate_total": gate_total,
        "gate_completed": gate_completed,
        "gate_pass_rate": gate_pass_rate,
        "task_execution_total": task_execution_total,
        "pro_share_percent": pro_share_percent,
        "api_fallback_rate_percent": api_fallback_rate_percent,
        "compliance_violations": int(event_summary.get("compliance_violations", 0)),
    }


def evaluate_pilot(metrics: dict[str, float | int], thresholds: PilotThresholds) -> PilotCheckResult:
    messages: list[str] = []

    task_success = float(metrics.get("task_success_rate", 0.0))
    gate_pass = float(metrics.get("gate_pass_rate", 0.0))
    pro_share = float(metrics.get("pro_share_percent", 0.0))
    compliance_violations = int(metrics.get("compliance_violations", 0))

    if task_success < thresholds.min_task_success_rate:
        messages.append(
            "task_success_rate "
            f"{task_success:.2f}% < required {thresholds.min_task_success_rate:.2f}%"
        )
    if gate_pass < thresholds.min_gate_pass_rate:
        messages.append(
            f"gate_pass_rate {gate_pass:.2f}% < required {thresholds.min_gate_pass_rate:.2f}%"
        )
    if pro_share < thresholds.min_pro_share_percent:
        messages.append(
            f"pro_share_percent {pro_share:.2f}% < required {thresholds.min_pro_share_percent:.2f}%"
        )
    if compliance_violations > thresholds.max_compliance_violations:
        messages.append(
            f"compliance_violations {compliance_violations} > allowed "
            f"{thresholds.max_compliance_violations}"
        )

    if not messages:
        messages.append("pilot thresholds satisfied")
        return PilotCheckResult(ok=True, messages=messages)
    return PilotCheckResult(ok=False, messages=messages)


def _safe_percent(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round((part / whole) * 100.0, 2)
