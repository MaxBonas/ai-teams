import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from aiteam.observability import EventLogger
from aiteam.metrics import MetricsAggregator

@pytest.fixture
def temp_logger(tmp_path: Path):
    logger = EventLogger(tmp_path)
    now = datetime.now(timezone.utc)
    
    # 3 events 10 minutes ago
    for i in range(3):
        logger.emit("task_execution", {"success": True, "latency_ms": 100 * (i+1)})
    
    # modify timestamps manually to avoid mocking datetime everywhere
    lines = tmp_path.joinpath("events.jsonl").read_text(encoding="utf-8").splitlines()
    new_lines = []
    for line in lines:
        r = json.loads(line)
        r["ts"] = (now - timedelta(minutes=10)).isoformat()
        new_lines.append(json.dumps(r))
    tmp_path.joinpath("events.jsonl").write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    
    # 2 events 3 hours ago
    logger.emit("task_execution", {"success": False, "latency_ms": 500})
    logger.emit("task_failed", {"reason": "timeout from mock payload"})
    
    lines = tmp_path.joinpath("events.jsonl").read_text(encoding="utf-8").splitlines()
    final_lines = new_lines.copy()
    for line in lines[len(new_lines):]:
        r = json.loads(line)
        r["ts"] = (now - timedelta(hours=3)).isoformat()
        final_lines.append(json.dumps(r))
    tmp_path.joinpath("events.jsonl").write_text("\n".join(final_lines) + "\n", encoding="utf-8")
    
    return logger

def test_recent_events_filters_by_hours(temp_logger):
    events_1h = temp_logger.recent_events(hours=1)
    assert len(events_1h) == 3

    events_4h = temp_logger.recent_events(hours=4)
    assert len(events_4h) == 5

def test_percentile_latency_p50(temp_logger):
    agg = MetricsAggregator(temp_logger)
    # 1h window: latencies [100, 200, 300] => p50 should be 200
    p50 = agg.percentile_latency(50, window_hours=1)
    assert 200.0 == pytest.approx(p50)

def test_percentile_latency_p95(temp_logger):
    agg = MetricsAggregator(temp_logger)
    # 4h window: latencies [100, 200, 300, 500] => p100 should be 500
    p100 = agg.percentile_latency(100, window_hours=4)
    assert p100 == 500.0

def test_error_categorization_by_type(temp_logger):
    agg = MetricsAggregator(temp_logger)
    errors = agg.error_categorization(window_hours=4)
    assert errors.get("timeout") == 1

def test_event_type_breakdown(temp_logger):
    agg = MetricsAggregator(temp_logger)
    breakdown = agg.event_type_breakdown(window_hours=4)
    assert breakdown.get("task_execution") == 4
    assert breakdown.get("task_failed") == 1

def test_summary_respects_window_hours_parameter(temp_logger):
    summary_1h = temp_logger.summary(window_hours=1)
    assert summary_1h["task_execution_total"] == 3
    
    summary_4h = temp_logger.summary(window_hours=4)
    assert summary_4h["task_execution_total"] == 4
