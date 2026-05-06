from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from aiteam.db.liveness import reconcile_stalled_subtrees, reconcile_unassigned_role_issues, reconcile_unqueued_assigned_issues
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler

logger = logging.getLogger(__name__)


class HeartbeatLoop:
    """Async loop: tick timers → drain wakeup queue → execute runs."""

    def __init__(
        self,
        db_path: Path,
        executor: RunExecutor,
        *,
        tick_interval_sec: float = 30.0,
    ) -> None:
        self.db_path = Path(db_path)
        self.executor = executor
        self.tick_interval_sec = tick_interval_sec
        self._scheduler = HeartbeatScheduler(db_path)

    async def run_once(self) -> int:
        """Tick timers, run liveness reconciler, and drain the wakeup queue. Returns number of runs dispatched."""
        loop = asyncio.get_event_loop()
        now = datetime.now(timezone.utc)
        try:
            await loop.run_in_executor(None, self._scheduler.tick_timers, now)
        except Exception:
            logger.exception("tick_timers failed")

        try:
            materialized = await loop.run_in_executor(None, reconcile_unassigned_role_issues, self.db_path)
            recovered = await loop.run_in_executor(None, reconcile_unqueued_assigned_issues, self.db_path)
            stalled = await loop.run_in_executor(None, reconcile_stalled_subtrees, self.db_path)
            recovered = [*materialized, *recovered]
            if recovered:
                logger.info("liveness: re-enqueued %d orphaned issue(s): %s", len(recovered), recovered)
            if stalled:
                logger.info("liveness: escalated %d stalled subtree(s) to supervisor: %s", len(stalled), stalled)
        except Exception:
            logger.exception("liveness reconciler failed")

        dispatched = 0
        while True:
            try:
                result = await loop.run_in_executor(None, self._scheduler.dispatch_next)
            except Exception:
                logger.exception("dispatch_next failed")
                break
            if result is None:
                break
            try:
                await loop.run_in_executor(None, self.executor.execute, result)
            except Exception:
                logger.exception("executor.execute failed for run %s", result.run.get("id"))
            dispatched += 1

        return dispatched

    async def run_forever(self) -> None:
        """Loop forever: run_once then sleep tick_interval_sec."""
        logger.info("HeartbeatLoop started (interval=%.0fs)", self.tick_interval_sec)
        while True:
            try:
                count = await self.run_once()
                if count:
                    logger.debug("HeartbeatLoop dispatched %d run(s)", count)
            except asyncio.CancelledError:
                logger.info("HeartbeatLoop cancelled")
                return
            except Exception:
                logger.exception("HeartbeatLoop iteration failed")
            try:
                await asyncio.sleep(self.tick_interval_sec)
            except asyncio.CancelledError:
                logger.info("HeartbeatLoop cancelled during sleep")
                return
