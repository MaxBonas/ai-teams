from __future__ import annotations

import html
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from aiteam.types import WorkTask


def build_dashboard_payload(
    *,
    runtime_dir: Path,
    tasks: list[WorkTask],
    summary: dict[str, Any],
    pilot_metrics: dict[str, float | int],
    budget_snapshot: dict[str, Any] | None,
    memory_counts: dict[str, int],
    environment: str = "dev",
) -> dict[str, Any]:
    state_counts = Counter(task.state.value for task in tasks)
    role_counts = Counter(task.role.value for task in tasks)
    recent_events = _recent_events(runtime_dir / "events.jsonl", limit=120)
    latency_series = _agent_latency_series(recent_events)
    latency_histogram = _agent_latency_histogram(latency_series)
    latency_avg = _agent_latency_average_ms(latency_series)
    latency_percentiles = _agent_latency_percentiles(latency_series)
    tuning_recommendations = _parallel_tuning_recommendations(
        environment=environment,
        summary=summary,
        recent_events=recent_events,
        latency_percentiles=latency_percentiles,
    )
    latency_trends = _agent_round_latency_percentiles(recent_events, max_rounds=8)
    flow_timeline = _flow_timeline(recent_events, limit=24)
    flow_summary = _flow_summary(tasks=tasks, recent_events=recent_events)
    provider_ops = _provider_ops(runtime_dir)

    def classify(task: WorkTask) -> str:
        diff = str(task.metadata.get("git_diff_evidence", ""))
        if diff:
            added = diff.count("\n+")
            deleted = diff.count("\n-")
            if deleted > 0 and deleted >= (added * 0.2):
                return "Refactor"
            return "Bootstrap"
        t = task.title.lower()
        if "refactor" in t or "fix" in t or "update" in t or "bug" in t:
            return "Refactor"
        if "create" in t or "init" in t or "nuevo" in t or "new" in t:
            return "Bootstrap"
        return "Other"

    mode_counts = Counter(
        classify(task) for task in tasks if task.role.value == "engineer"
    )

    ab_versions = Counter(
        str(task.metadata.get("prompt_ab_version", "A")) for task in tasks
    )

    return {
        "runtime_dir": str(runtime_dir.resolve()),
        "task_total": len(tasks),
        "task_state_counts": dict(state_counts),
        "task_role_counts": dict(role_counts),
        "task_mode_counts": dict(mode_counts),
        "task_ab_versions": dict(ab_versions),
        "summary": summary,
        "pilot_metrics": pilot_metrics,
        "budget": budget_snapshot or {},
        "memory_counts": memory_counts,
        "tasks": [_task_row(task) for task in tasks],
        "recent_events": recent_events,
        "agent_latency_histogram": latency_histogram,
        "agent_latency_avg_ms": latency_avg,
        "agent_latency_percentiles": latency_percentiles,
        "agent_latency_trends": latency_trends,
        "tuning_recommendations": tuning_recommendations,
        "flow_timeline": flow_timeline,
        "flow_summary": flow_summary,
        "provider_ops": provider_ops,
    }


def render_dashboard_html(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    pilot = payload.get("pilot_metrics", {})
    budget = payload.get("budget", {})

    cards = [
        ("Tasks", str(payload.get("task_total", 0))),
        ("Execution Success", f"{summary.get('task_execution_success_rate', 0)}%"),
        ("Pro Share", f"{pilot.get('pro_share_percent', 0)}%"),
        ("API Share", f"{summary.get('api_share_percent', 0)}%"),
        ("Compliance Violations", str(summary.get("compliance_violations", 0))),
        (
            "Daily Budget",
            f"${budget.get('daily_api_spend_usd', 0):.2f}/${budget.get('daily_api_budget_usd', 0):.2f}",
        ),
        (
            "Monthly Forecast",
            f"${budget.get('forecast_monthly_spend_usd', 0):.2f} ({(budget.get('forecast_utilization_ratio', 0) * 100):.1f}%)",
        ),
        ("Global p50", f"{summary.get('latency_p50', 0):.0f} ms"),
        ("Global p99", f"{summary.get('latency_p99', 0):.0f} ms"),
    ]

    ab_items = _kv_list(payload.get("task_ab_versions", {}))

    tasks_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('task_id', '')))}</td>"
        f"<td>{html.escape(str(item.get('state', '')))}</td>"
        f"<td>{html.escape(str(item.get('role', '')))}</td>"
        f"<td>{html.escape(str(item.get('assignee', '')))}</td>"
        f"<td>r{item.get('execution_round', 0)} / s{item.get('execution_sub_iteration', 0)} / g{item.get('gate_iteration', 0)}</td>"
        f"<td>{html.escape(str(item.get('blocked_reason', '')) or '-')}</td>"
        f"<td>{html.escape(str(item.get('title', '')))}</td>"
        f"<td>{item.get('total_latency_ms', 0):,} ms</td>"
        f"<td><div style='width: {min(100, int(item.get('evidence_lines', 0)))}px; height: 8px; background: var(--accent); border-radius: 4px;' title='{item.get('evidence_lines', 0)} lines diff'></div></td>"
        "</tr>"
        for item in payload.get("tasks", [])
    )

    flow_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('ts', '')))}</td>"
        f"<td>{html.escape(str(item.get('event_type', '')))}</td>"
        f"<td>{html.escape(str(item.get('task_id', '')))}</td>"
        f"<td>{html.escape(str(item.get('assignee', '')))}</td>"
        f"<td>r{item.get('execution_round', 0)} / s{item.get('execution_sub_iteration', 0)} / g{item.get('gate_iteration', 0)}</td>"
        f"<td>{html.escape(str(item.get('summary', '')))}</td>"
        "</tr>"
        for item in payload.get("flow_timeline", [])
    )

    event_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('ts', '')))}</td>"
        f"<td>{html.escape(str(item.get('event_type', '')))}</td>"
        f"<td>{html.escape(_compact_json(item.get('payload', {}), 180))}</td>"
        "</tr>"
        for item in payload.get("recent_events", [])
    )

    alerts = payload.get("summary", {}).get("alerts", [])
    alert_items = "\n".join(f"<li>{html.escape(str(alert))}</li>" for alert in alerts)
    if not alert_items:
        alert_items = "<li>none</li>"

    state_items = _kv_list(payload.get("task_state_counts", {}))
    role_items = _kv_list(payload.get("task_role_counts", {}))
    mode_items = _kv_list(payload.get("task_mode_counts", {}))
    channel_items = _kv_list(payload.get("summary", {}).get("channels", {}))
    provider_items = _kv_list(payload.get("summary", {}).get("providers", {}))
    memory_items = _kv_list(payload.get("memory_counts", {}))
    latency_items = _latency_histogram_list(
        payload.get("agent_latency_histogram", {}),
        payload.get("agent_latency_avg_ms", {}),
    )
    latency_percentile_items = _latency_percentiles_list(
        payload.get("agent_latency_percentiles", {})
    )
    latency_trend_items = _latency_trends_list(payload.get("agent_latency_trends", {}))
    tuning_items = _text_list(payload.get("tuning_recommendations", []))
    error_items = _kv_list(payload.get("summary", {}).get("error_breakdown", {}))
    flow_items = _kv_list(payload.get("flow_summary", {}))
    provider_ops = payload.get("provider_ops", {}) or {}
    provider_summary_items = _kv_list(provider_ops.get("summary", {}))
    provider_alert_items = _text_list(provider_ops.get("alerts", []))
    provider_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('adapter_name', '')))}</td>"
        f"<td>{html.escape(str(item.get('tier', '')))}</td>"
        f"<td>{html.escape(str(item.get('provider', '')))}</td>"
        f"<td>{html.escape(str(item.get('operational', '')))}</td>"
        f"<td>{html.escape(str(item.get('doctor_details', '')))}</td>"
        f"<td>{html.escape(str(item.get('smoke_details', '')))}</td>"
        "</tr>"
        for item in provider_ops.get("providers", [])
    )

    card_html = "\n".join(
        "<div class='card'>"
        f"<div class='label'>{html.escape(label)}</div>"
        f"<div class='value'>{html.escape(value)}</div>"
        "</div>"
        for label, value in cards
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Team Dashboard</title>
  <style>
    :root {{
      --bg: #f4f6f8;
      --surface: #ffffff;
      --ink: #12212f;
      --muted: #5f7182;
      --accent: #006f8a;
      --line: #d8e1e8;
    }}
    body {{ margin: 0; font-family: "Segoe UI", "Helvetica Neue", sans-serif; background: var(--bg); color: var(--ink); }}
    .wrap {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
    h1 {{ margin: 0 0 10px; font-size: 28px; }}
    .sub {{ color: var(--muted); margin-bottom: 18px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin-bottom: 16px; }}
    .card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 12px; }}
    .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }}
    .value {{ margin-top: 6px; font-weight: 700; font-size: 20px; }}
    .grid {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); margin-bottom: 16px; }}
    .panel {{ background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 12px; }}
    .panel h2 {{ margin: 0 0 8px; font-size: 16px; color: var(--accent); }}
    ul {{ margin: 0; padding-left: 18px; }}
    li {{ margin-bottom: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px; text-align: left; font-size: 13px; vertical-align: top; }}
    th {{ background: #edf3f7; }}
    .table-title {{ margin: 16px 0 8px; color: var(--accent); font-size: 16px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>AI Team Operations Dashboard</h1>
    <div class="sub">runtime: {html.escape(str(payload.get("runtime_dir", "")))}</div>
    <div class="cards">{card_html}</div>
    <div class="grid">
      <section class="panel"><h2>Alerts</h2><ul>{alert_items}</ul></section>
      <section class="panel"><h2>Error Breakdown</h2><ul>{error_items}</ul></section>
      <section class="panel"><h2>Task States</h2><ul>{state_items}</ul></section>
      <section class="panel"><h2>Roles</h2><ul>{role_items}</ul></section>
      <section class="panel"><h2>Bootstrap VS Refactor</h2><ul>{mode_items}</ul></section>
      <section class="panel"><h2>A/B Prompts</h2><ul>{ab_items}</ul></section>
      <section class="panel"><h2>Channels</h2><ul>{channel_items}</ul></section>
      <section class="panel"><h2>Providers</h2><ul>{provider_items}</ul></section>
      <section class="panel"><h2>Memory Entries</h2><ul>{memory_items}</ul></section>
      <section class="panel"><h2>Agent Latency</h2><ul>{latency_items}</ul></section>
      <section class="panel"><h2>Latency Percentiles</h2><ul>{latency_percentile_items}</ul></section>
      <section class="panel"><h2>Latency Trend (p95 by round)</h2><ul>{latency_trend_items}</ul></section>
      <section class="panel"><h2>Tuning Recommendations</h2><ul>{tuning_items}</ul></section>
      <section class="panel"><h2>Flow Summary</h2><ul>{flow_items}</ul></section>
      <section class="panel"><h2>Provider Ops Summary</h2><ul>{provider_summary_items}</ul></section>
      <section class="panel"><h2>Provider Alerts</h2><ul>{provider_alert_items}</ul></section>
    </div>

    <div class="table-title">Tasks & Evidence</div>
    <table>
      <thead><tr><th>ID</th><th>State</th><th>Role</th><th>Assignee</th><th>Flow</th><th>Blocked</th><th>Title</th><th>Total Latency</th><th>Evidence Bar</th></tr></thead>
      <tbody>{tasks_rows}</tbody>
    </table>

    <div class="table-title">Flow Timeline</div>
    <table>
      <thead><tr><th>Timestamp</th><th>Type</th><th>Task</th><th>Assignee</th><th>Flow</th><th>Summary</th></tr></thead>
      <tbody>{flow_rows}</tbody>
    </table>

    <div class="table-title">Provider Ops</div>
    <table>
      <thead><tr><th>Adapter</th><th>Tier</th><th>Provider</th><th>Operational</th><th>Doctor</th><th>Smoke</th></tr></thead>
      <tbody>{provider_rows}</tbody>
    </table>

    <div class="table-title">Recent Events</div>
    <table>
      <thead><tr><th>Timestamp</th><th>Type</th><th>Payload</th></tr></thead>
      <tbody>{event_rows}</tbody>
    </table>
  </div>
</body>
</html>
"""


def _task_row(task: WorkTask) -> dict[str, Any]:
    diff = task.metadata.get("git_diff_evidence", "")
    lines = len(diff.splitlines()) if diff else 0
    total_ms = task.metadata.get("total_latency_ms", 0)
    return {
        "task_id": task.task_id,
        "state": task.state.value,
        "role": task.role.value,
        "assignee": task.assignee or "",
        "execution_round": int(task.metadata.get("execution_round", 0) or 0),
        "execution_sub_iteration": int(
            task.metadata.get("execution_sub_iteration", 0) or 0
        ),
        "gate_iteration": int(task.metadata.get("gate_iteration", 0) or 0),
        "blocked_reason": str(task.metadata.get("blocked_reason", "") or ""),
        "title": task.title,
        "total_latency_ms": total_ms,
        "evidence_lines": lines,
    }


def _recent_events(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            items.append(record)
    return items[-max(1, limit) :]


def _flow_timeline(
    recent_events: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    interesting = {
        "task_started",
        "task_execution",
        "round_sub_iteration",
        "round_completed",
        "gate_iteration",
        "agent_handoff",
        "sync_meeting",
        "sync_meeting_skipped",
        "conversation_mailbox_consumed",
        "conversation_mailbox_reply",
        "sub_iteration_barrier",
    }
    rows: list[dict[str, Any]] = []
    for item in recent_events:
        event_type = str(item.get("event_type", "") or "")
        if event_type not in interesting:
            continue
        payload = (
            item.get("payload", {}) if isinstance(item.get("payload"), dict) else {}
        )
        rows.append(
            {
                "ts": str(item.get("ts", "") or ""),
                "event_type": event_type,
                "task_id": str(payload.get("task_id", "") or ""),
                "assignee": str(payload.get("assignee", payload.get("from", "")) or ""),
                "execution_round": int(payload.get("execution_round", 0) or 0),
                "execution_sub_iteration": int(
                    payload.get(
                        "execution_sub_iteration", payload.get("sub_iteration", 0)
                    )
                    or 0
                ),
                "gate_iteration": int(
                    payload.get("gate_iteration", payload.get("iteration", 0)) or 0
                ),
                "summary": _compact_json(payload, 160),
            }
        )
    return rows[-max(1, limit) :]


def _flow_summary(
    tasks: list[WorkTask], recent_events: list[dict[str, Any]]
) -> dict[str, Any]:
    blocked = sum(1 for task in tasks if task.state.value == "blocked")
    handoffs = 0
    gate_iterations = 0
    mailbox_turns = 0
    meetings = 0
    for item in recent_events:
        event_type = str(item.get("event_type", "") or "")
        if event_type == "agent_handoff":
            handoffs += 1
        elif event_type == "gate_iteration":
            gate_iterations += 1
        elif event_type in {
            "conversation_mailbox_consumed",
            "conversation_mailbox_reply",
        }:
            mailbox_turns += 1
        elif event_type in {"sync_meeting", "sync_meeting_skipped"}:
            meetings += 1
    return {
        "blocked_tasks": blocked,
        "handoffs": handoffs,
        "gate_iterations": gate_iterations,
        "mailbox_thread_events": mailbox_turns,
        "meeting_events": meetings,
    }


def _provider_ops(runtime_dir: Path) -> dict[str, Any]:
    path = runtime_dir / "provider_ops.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _kv_list(items: dict[str, Any]) -> str:
    if not items:
        return "<li>none</li>"
    return "\n".join(
        f"<li><strong>{html.escape(str(key))}</strong>: {html.escape(str(value))}</li>"
        for key, value in sorted(items.items(), key=lambda kv: str(kv[0]))
    )


def _compact_json(value: Any, max_chars: int) -> str:
    text = json.dumps(value, ensure_ascii=True)
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def _agent_latency_series(events: list[dict[str, Any]]) -> dict[str, list[int]]:
    series: dict[str, list[int]] = {}
    for record in events:
        if str(record.get("event_type", "")) != "task_execution":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        assignee = str(payload.get("assignee", "")).strip()
        if not assignee:
            continue
        latency = int(payload.get("latency_ms", 0) or 0)
        if latency <= 0:
            continue
        if assignee not in series:
            series[assignee] = []
        series[assignee].append(latency)
    return series


def _agent_latency_histogram(series: dict[str, list[int]]) -> dict[str, dict[str, int]]:
    buckets = ["0-199", "200-499", "500-999", "1000+"]
    histogram: dict[str, dict[str, int]] = {}
    for assignee, values in series.items():
        if assignee not in histogram:
            histogram[assignee] = {bucket: 0 for bucket in buckets}
        for latency in values:
            if latency < 200:
                histogram[assignee]["0-199"] += 1
            elif latency < 500:
                histogram[assignee]["200-499"] += 1
            elif latency < 1000:
                histogram[assignee]["500-999"] += 1
            else:
                histogram[assignee]["1000+"] += 1
    return histogram


def _agent_latency_average_ms(series: dict[str, list[int]]) -> dict[str, float]:
    avg: dict[str, float] = {}
    for assignee, values in series.items():
        if not values:
            continue
        avg[assignee] = round(sum(values) / len(values), 2)
    return avg


def _agent_latency_percentiles(
    series: dict[str, list[int]],
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for assignee, values in series.items():
        if not values:
            continue
        sorted_values = sorted(values)
        result[assignee] = {
            "count": float(len(sorted_values)),
            "p50_ms": _percentile(sorted_values, 50),
            "p95_ms": _percentile(sorted_values, 95),
        }
    return result


def _percentile(sorted_values: list[int], p: int) -> float:
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    position = max(0, min(n - 1, math.ceil((p / 100.0) * n) - 1))
    return float(sorted_values[position])


def _latency_histogram_list(
    histogram: dict[str, dict[str, int]],
    averages: dict[str, float],
) -> str:
    if not histogram:
        return "<li>none</li>"

    rows = []
    for assignee in sorted(histogram.keys()):
        buckets = histogram.get(assignee, {})
        avg_ms = averages.get(assignee, 0.0)
        bucket_text = ", ".join(
            f"{bucket}:{count}" for bucket, count in buckets.items()
        )
        rows.append(
            f"<li><strong>{html.escape(assignee)}</strong>: avg={html.escape(str(avg_ms))}ms, "
            f"{html.escape(bucket_text)}</li>"
        )
    return "\n".join(rows)


def _latency_percentiles_list(percentiles: dict[str, dict[str, float]]) -> str:
    if not percentiles:
        return "<li>none</li>"
    rows = []
    for assignee in sorted(percentiles.keys()):
        item = percentiles.get(assignee, {})
        p50 = item.get("p50_ms", 0.0)
        p95 = item.get("p95_ms", 0.0)
        count = int(item.get("count", 0.0))
        rows.append(
            f"<li><strong>{html.escape(assignee)}</strong>: p50={html.escape(str(p50))}ms, "
            f"p95={html.escape(str(p95))}ms, n={html.escape(str(count))}</li>"
        )
    return "\n".join(rows)


def _agent_round_latency_percentiles(
    events: list[dict[str, Any]],
    max_rounds: int,
) -> dict[str, list[dict[str, float | int]]]:
    grouped: dict[str, dict[int, list[int]]] = {}
    for record in events:
        if str(record.get("event_type", "")) != "task_execution":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        assignee = str(payload.get("assignee", "")).strip()
        if not assignee:
            continue
        execution_round = int(payload.get("execution_round", 0) or 0)
        if execution_round <= 0:
            continue
        latency = int(payload.get("latency_ms", 0) or 0)
        if latency <= 0:
            continue
        if assignee not in grouped:
            grouped[assignee] = {}
        if execution_round not in grouped[assignee]:
            grouped[assignee][execution_round] = []
        grouped[assignee][execution_round].append(latency)

    trends: dict[str, list[dict[str, float | int]]] = {}
    for assignee, per_round in grouped.items():
        rounds = sorted(per_round.keys())[-max(1, max_rounds) :]
        trends[assignee] = []
        for execution_round in rounds:
            values = sorted(per_round.get(execution_round, []))
            if not values:
                continue
            trends[assignee].append(
                {
                    "round": execution_round,
                    "count": len(values),
                    "p50_ms": _percentile(values, 50),
                    "p95_ms": _percentile(values, 95),
                }
            )
    return trends


def _latency_trends_list(trends: dict[str, list[dict[str, float | int]]]) -> str:
    if not trends:
        return "<li>none</li>"

    rows: list[str] = []
    for assignee in sorted(trends.keys()):
        series = trends.get(assignee, [])
        if not series:
            continue
        fragments = []
        for item in series:
            round_id = int(item.get("round", 0))
            p95 = float(item.get("p95_ms", 0.0))
            count = int(item.get("count", 0))
            fragments.append(f"r{round_id}:p95={p95}ms(n={count})")
        rows.append(
            f"<li><strong>{html.escape(assignee)}</strong>: {html.escape(' | '.join(fragments))}</li>"
        )
    return "\n".join(rows) if rows else "<li>none</li>"


def _parallel_tuning_recommendations(
    *,
    environment: str,
    summary: dict[str, Any],
    recent_events: list[dict[str, Any]],
    latency_percentiles: dict[str, dict[str, float]],
) -> list[str]:
    recommendations: list[str] = []

    success_rate = float(summary.get("task_execution_success_rate", 100.0) or 0.0)
    env = environment.strip().lower()
    success_threshold = 94.0 if env == "prod" else 90.0
    p95_threshold = 900.0 if env == "prod" else 1200.0

    if success_rate < success_threshold:
        recommendations.append(
            f"Task success por debajo de {success_threshold:.0f}%; prioriza estabilidad y reduce paralelismo en {env}."
        )

    high_latency_agents = [
        agent
        for agent, values in latency_percentiles.items()
        if float(values.get("p95_ms", 0.0)) >= p95_threshold
    ]
    if high_latency_agents:
        recommendations.append(
            "p95 alto en "
            + ", ".join(sorted(high_latency_agents))
            + f"; reduce paralelismo o divide tareas grandes antes de aumentar workers en {env}."
        )

    task_exec_count = int(summary.get("task_execution_total", 0) or 0)
    low_latency = (
        task_exec_count >= 6
        and success_rate >= 98.0
        and latency_percentiles
        and all(
            float(values.get("p95_ms", 99999.0)) < 450.0
            for values in latency_percentiles.values()
        )
    )
    if low_latency:
        recommendations.append(
            "Sistema estable y rapido; puedes subir AITEAM_MAX_PARALLEL_TASKS en stage de forma gradual (+1)."
        )

    tuning_events = [
        item
        for item in recent_events
        if str(item.get("event_type", "")) == "parallel_tuning"
        and isinstance(item.get("payload"), dict)
    ]
    if tuning_events:
        latest_payload = tuning_events[-1].get("payload", {})
        previous = int(latest_payload.get("parallel_previous", 0) or 0)
        current = int(latest_payload.get("parallel_current", 0) or 0)
        if current < previous:
            recommendations.append(
                f"Auto-tuning redujo paralelismo ({previous}->{current}); revisar latencia/fallos en esa ronda."
            )
        elif current > previous:
            recommendations.append(
                f"Auto-tuning aumento paralelismo ({previous}->{current}); mantener monitoreo p95 y failure rate."
            )
    else:
        recommendations.append(
            "Sin eventos de auto-tuning; activa AITEAM_PARALLEL_AUTOTUNE para ajustes dinamicos en stage."
        )

    if not recommendations:
        recommendations.append(
            "Operacion estable; mantener configuracion actual y seguir observando p95 por agente."
        )
    return recommendations[:5]


def _text_list(items: list[str]) -> str:
    if not items:
        return "<li>none</li>"
    return "\n".join(f"<li>{html.escape(str(item))}</li>" for item in items)
