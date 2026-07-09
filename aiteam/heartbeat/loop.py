from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from aiteam.adapters.registry import AdapterRegistry
from aiteam.autonomy import auto_resolve_operational_interactions
from aiteam.db.liveness import (
    reconcile_orphaned_interactions,
    reconcile_stalled_subtrees,
    reconcile_unassigned_role_issues,
    reconcile_unqueued_assigned_issues,
)
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler

logger = logging.getLogger(__name__)


class HeartbeatLoop:
    """Async loop: tick timers → drain wakeup queue → execute runs.

    Pass ``db_path_factory`` (a zero-arg callable that returns the current
    workspace DB path) so the loop can transparently switch to a new project
    when the user creates or switches workspaces at runtime — without needing
    a server restart.  ``registry`` is required when ``db_path_factory`` is
    supplied; it is used to re-create the executor for the new path.
    """

    def __init__(
        self,
        db_path: Path,
        executor: RunExecutor,
        *,
        tick_interval_sec: float = 30.0,
        db_path_factory: Callable[[], Path] | None = None,
        registry: AdapterRegistry | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.executor = executor
        self.tick_interval_sec = tick_interval_sec
        self._scheduler = HeartbeatScheduler(db_path)
        self._db_path_factory = db_path_factory
        self._registry = registry

    def _refresh_workspace(self) -> None:
        """Check whether the current workspace has changed and, if so, switch.

        Called at the top of every ``run_once`` tick so a project
        create/switch takes effect within one tick interval (≤30 s).
        """
        if self._db_path_factory is None:
            return
        try:
            fresh = Path(self._db_path_factory())
        except Exception:
            logger.warning("HeartbeatLoop: db_path_factory raised — keeping current path", exc_info=True)
            return
        if fresh == self.db_path:
            return
        if not fresh.exists():
            # New workspace DB not ready yet; wait for the next tick.
            return
        logger.info("HeartbeatLoop: workspace changed %s → %s", self.db_path, fresh)
        self.db_path = fresh
        self._scheduler = HeartbeatScheduler(fresh)
        if self._registry is not None:
            self.executor = RunExecutor(fresh, self._registry)

    async def run_once(self) -> int:
        """Tick timers, run liveness reconciler, and drain the wakeup queue. Returns number of runs dispatched."""
        loop = asyncio.get_event_loop()

        # Switch workspace if the user created / switched projects since last tick.
        await loop.run_in_executor(None, self._refresh_workspace)

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

        # Orphan cleanup BEFORE autonomy: a stale escalation gets cancelled,
        # not auto-accepted.
        try:
            orphaned = await loop.run_in_executor(None, reconcile_orphaned_interactions, self.db_path)
            if orphaned:
                logger.info("liveness: cancelled %d orphaned interaction(s): %s", len(orphaned), orphaned)
        except Exception:
            logger.exception("orphaned-interactions reconciler failed")

        try:
            auto_resolved = await loop.run_in_executor(None, auto_resolve_operational_interactions, self.db_path)
            if auto_resolved:
                logger.info(
                    "autonomy: auto-resolved %d operational interaction(s): %s",
                    len(auto_resolved), auto_resolved,
                )
        except Exception:
            logger.exception("autonomy reconciler failed")

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
