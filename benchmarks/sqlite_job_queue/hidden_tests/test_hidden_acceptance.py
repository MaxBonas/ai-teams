from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from jobqueue import SQLiteJobQueue


def test_enqueue_claim_ack_and_stats(tmp_path: Path) -> None:
    q = SQLiteJobQueue(tmp_path / "q.db")
    job_id = q.enqueue({"task": "a"})
    assert q.stats() == {"pending": 1, "running": 0, "done": 0, "dead": 0}
    job = q.claim("w1", now=100.0)
    assert job["id"] == job_id and job["payload"] == {"task": "a"}
    assert job["attempts"] == 1 and job["worker_id"] == "w1"
    assert q.ack(job_id, "w1") is True
    assert q.stats() == {"pending": 0, "running": 0, "done": 1, "dead": 0}


def test_fifo_order(tmp_path: Path) -> None:
    q = SQLiteJobQueue(tmp_path / "q.db")
    ids = [q.enqueue({"n": n}) for n in range(3)]
    assert [q.claim(f"w{n}", now=10.0)["id"] for n in range(3)] == ids


def test_wrong_worker_cannot_ack_or_fail(tmp_path: Path) -> None:
    q = SQLiteJobQueue(tmp_path / "q.db")
    job_id = q.enqueue({"x": 1})
    q.claim("owner", now=1.0)
    assert q.ack(job_id, "intruder") is False
    assert q.fail(job_id, "intruder", "no") is False
    assert q.stats()["running"] == 1


def test_expired_lease_is_reclaimed_and_attempt_increments(tmp_path: Path) -> None:
    q = SQLiteJobQueue(tmp_path / "q.db")
    job_id = q.enqueue({"x": 1})
    q.claim("w1", lease_seconds=5, now=100.0)
    assert q.claim("w2", now=104.9) is None
    again = q.claim("w2", now=105.0)
    assert again["id"] == job_id
    assert again["attempts"] == 2
    assert again["worker_id"] == "w2"


def test_fail_retry_delay_and_dead_letter(tmp_path: Path) -> None:
    q = SQLiteJobQueue(tmp_path / "q.db")
    job_id = q.enqueue({"x": 1}, max_attempts=2)
    q.claim("w1", now=10.0)
    assert q.fail(job_id, "w1", "first", retry_delay=5, now=11.0)
    assert q.claim("w2", now=15.9) is None
    second = q.claim("w2", now=16.0)
    assert second["attempts"] == 2
    assert q.fail(job_id, "w2", "second", now=17.0)
    assert q.stats()["dead"] == 1
    assert q.claim("w3", now=100.0) is None


def test_concurrent_claims_are_unique(tmp_path: Path) -> None:
    db = tmp_path / "q.db"
    q = SQLiteJobQueue(db)
    expected = {q.enqueue({"n": n}) for n in range(20)}

    def claim(n: int):
        return SQLiteJobQueue(db).claim(f"worker-{n}", now=50.0)

    with ThreadPoolExecutor(max_workers=12) as pool:
        claimed = [job for job in pool.map(claim, range(30)) if job is not None]
    ids = [job["id"] for job in claimed]
    assert len(ids) == 20
    assert len(set(ids)) == 20
    assert set(ids) == expected


@pytest.mark.parametrize("payload", [{"bad": object()}, {"nested": [object()]}])
def test_non_json_payload_rejected(tmp_path: Path, payload: dict) -> None:
    q = SQLiteJobQueue(tmp_path / "q.db")
    with pytest.raises(ValueError):
        q.enqueue(payload)


def test_invalid_max_attempts_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SQLiteJobQueue(tmp_path / "q.db").enqueue({}, max_attempts=0)


def test_cli_enqueue_stats_and_invalid_json(tmp_path: Path) -> None:
    root = Path.cwd()
    db = tmp_path / "cli.db"
    created = subprocess.run(
        [sys.executable, str(root / "queue_cli.py"), "--db", str(db), "enqueue", '{"a":1}'],
        capture_output=True, text=True,
    )
    assert created.returncode == 0
    assert created.stdout.strip() and "\n" not in created.stdout.strip()
    stats = subprocess.run(
        [sys.executable, str(root / "queue_cli.py"), "--db", str(db), "stats"],
        capture_output=True, text=True,
    )
    assert json.loads(stats.stdout) == {"pending": 1, "running": 0, "done": 0, "dead": 0}
    bad = subprocess.run(
        [sys.executable, str(root / "queue_cli.py"), "--db", str(db), "enqueue", "not-json"],
        capture_output=True, text=True,
    )
    assert bad.returncode == 2 and bad.stderr.strip()
