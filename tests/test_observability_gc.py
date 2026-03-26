import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

from aiteam.observability import EventLogger
from aiteam.persistence import AtomicFileWriter

@pytest.fixture
def temp_runtime_dir(tmp_path: Path):
    return tmp_path

def test_prune_events_cleans_old_records(temp_runtime_dir):
    logger = EventLogger(temp_runtime_dir)
    
    now = datetime.now(timezone.utc)
    # Emit old event
    d_old = now - timedelta(days=40)
    logger.emit("task_failed", {"msg": "old_error"})
    
    # Manipulate TS to be old
    lines = logger.log_path.read_text().splitlines()
    r = json.loads(lines[0])
    r["ts"] = d_old.isoformat()
    # rewriting manually to simulate old record
    AtomicFileWriter.rewrite_jsonl_with_checksums(logger.log_path, [r])
    
    # Emit new event
    logger.emit("task_execution", {"success": True})
    
    assert len(logger._records()) == 2
    
    archive_dir = temp_runtime_dir / "archive"
    removed = logger.prune_events(max_days=30, archive_dir=archive_dir)
    
    assert removed == 1
    assert len(logger._records()) == 1
    assert logger._records()[0]["event_type"] == "task_execution"
    
    # Check archive
    archives = list(archive_dir.glob("*.jsonl"))
    assert len(archives) == 1
    archived_data = AtomicFileWriter.read_jsonl_with_dedup(archives[0])
    assert len(archived_data) == 1
    assert archived_data[0]["event_type"] == "task_failed"

def test_prune_events_does_nothing_if_all_recent(temp_runtime_dir):
    logger = EventLogger(temp_runtime_dir)
    logger.emit("task_execution", {"success": True})
    logger.emit("task_execution", {"success": False})
    
    removed = logger.prune_events(max_days=30)
    assert removed == 0
    assert len(logger._records()) == 2
