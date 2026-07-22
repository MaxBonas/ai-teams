from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from aiteam.adapters.registry import AdapterRegistry
from aiteam.autonomy import auto_resolve_operational_interactions
from aiteam.db.liveness import (
    reconcile_idle_parents,
    reconcile_orphaned_children_of_closed_parents,
    reconcile_orphaned_interactions,
    reconcile_stalled_subtrees,
    reconcile_unassigned_role_issues,
    reconcile_unqueued_assigned_issues,
)
from aiteam.db.runs import reconcile_stale_runs
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler
from aiteam.mcp_runtime import refresh_due_mcp_servers
from aiteam.mcp_needs import reconcile_mcp_needs

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

        # Stale-run recovery on EVERY tick, not just startup. A backend restart
        # mid-run leaves a 'running' zombie that is too fresh for the startup
        # sweep (seconds old at boot) and is never re-checked afterwards — the
        # zombie makes its issue look "live", so no reconciler ever re-enqueues
        # it and the whole subtree freezes (observed: frozen overnight). 15 min
        # is far beyond any legitimate CLI run, so concurrent UI-triggered runs
        # are safe.
        try:
            stale = await loop.run_in_executor(
                None, lambda: reconcile_stale_runs(self.db_path, max_age_sec=900)
            )
            if stale:
                logger.warning("tick: reconciled %d stale run(s): %s", len(stale), stale)
        except Exception:
            logger.exception("stale-run reconciler failed")

        # One due MCP probe at most per tick. Failed probes back off in the
        # registry and retire after three consecutive failures, so a broken
        # extension cannot create a process storm or installation loop.
        try:
            refreshed = await loop.run_in_executor(
                None,
                lambda: refresh_due_mcp_servers(self.db_path.parent, max_checks=1),
            )
            for result in refreshed:
                from aiteam.db.activity_log import log_activity

                log_activity(
                    self.db_path,
                    action="extension.health_periodic",
                    target_type="extension",
                    target_id=str(result.get("name") or ""),
                    payload={
                        "status": result.get("status"),
                        "health_status": (result.get("health") or {}).get("status"),
                        "consecutive_failures": (result.get("health") or {}).get("consecutive_failures", 0),
                    },
                )
        except Exception:
            logger.exception("periodic MCP health failed")

        try:
            suggested_needs = await loop.run_in_executor(None, reconcile_mcp_needs, self.db_path)
            if suggested_needs:
                logger.info("MCP need detector woke Lead for roots: %s", suggested_needs)
        except Exception:
            logger.exception("MCP need detector failed")

        try:
            materialized = await loop.run_in_executor(None, reconcile_unassigned_role_issues, self.db_path)
            recovered = await loop.run_in_executor(None, reconcile_unqueued_assigned_issues, self.db_path)
            stalled = await loop.run_in_executor(None, reconcile_stalled_subtrees, self.db_path)
            reopened_gap = await loop.run_in_executor(None, reconcile_orphaned_children_of_closed_parents, self.db_path)
            idle_parents = await loop.run_in_executor(None, reconcile_idle_parents, self.db_path)
            if idle_parents:
                logger.warning("reconciled %d idle parent(s) with all children terminal: %s", len(idle_parents), idle_parents)
            recovered = [*materialized, *recovered]
            if recovered:
                logger.info("liveness: re-enqueued %d orphaned issue(s): %s", len(recovered), recovered)
            if stalled:
                logger.info("liveness: escalated %d stalled subtree(s) to supervisor: %s", len(stalled), stalled)
            if reopened_gap:
                logger.info(
                    "liveness: escalated %d closed-parent/open-child gap(s): %s",
                    len(reopened_gap), reopened_gap,
                )
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
        from aiteam.policies import parallel_channels_enabled

        if parallel_channels_enabled():
            dispatched += await self._drain_parallel(loop)
            return dispatched

        from aiteam.heartbeat.scheduler import plan_sequential_batch

        while True:
            try:
                plan = await loop.run_in_executor(
                    None,
                    lambda: plan_sequential_batch(self.db_path),
                )
                if not plan.candidates:
                    break
                result = await loop.run_in_executor(
                    None,
                    lambda: self._scheduler.dispatch_next(
                        record_candidate_decision=False,
                    ),
                )
            except Exception:
                logger.exception("dispatch_next failed")
                break
            if result is None:
                # El dispatch acaba de drenar wakeups bloqueados/checkout.
                # Releer la cola evita confundir ese descarte con cola vacía.
                continue
            try:
                await loop.run_in_executor(None, self.executor.execute, result)
            except Exception:
                logger.exception("executor.execute failed for run %s", result.run.get("id"))
            dispatched += 1

        return dispatched

    async def _drain_parallel(self, loop: asyncio.AbstractEventLoop) -> int:
        """Drena la cola en batches concurrentes por pool de capacidad (opt-in).

        Cada batch respeta las restricciones de ``select_parallel_batch``
        (pools/agentes/subtrees distintos, un solo slot de trabajo). Los
        batches se ejecutan con gather y se espera el batch COMPLETO antes de
        formar el siguiente — sin solapamiento entre batches, la invariante de
        "un editor a la vez" se mantiene globalmente.
        """
        from aiteam.heartbeat.scheduler import plan_parallel_batch
        from aiteam.policies import parallel_batch_max

        dispatched = 0
        while True:
            try:
                plan = await loop.run_in_executor(
                    None,
                    lambda: plan_parallel_batch(
                        self.db_path,
                        max_runs=parallel_batch_max(),
                    ),
                )
                chosen = plan.selected_wakeup_ids
            except Exception:
                logger.exception("parallel batch selection failed — falling back to single dispatch")
                chosen = []
            if not chosen:
                # Cola vacía o sin candidatos válidos: un último dispatch_next
                # secuencial drena wakeups sin agente/issue que la selección
                # no supo clasificar, garantizando progreso.
                result = await loop.run_in_executor(None, self._scheduler.dispatch_next)
                if result is None:
                    return dispatched
                try:
                    await loop.run_in_executor(None, self.executor.execute, result)
                except Exception:
                    logger.exception("executor.execute failed for run %s", result.run.get("id"))
                dispatched += 1
                continue

            claims: list[Any] = []
            for wakeup_id in chosen:
                try:
                    result = await loop.run_in_executor(
                        None,
                        lambda wid=wakeup_id: self._scheduler.dispatch_next(
                            wakeup_ids={wid},
                            record_candidate_decision=False,
                        ),
                    )
                except Exception:
                    logger.exception("dispatch_next failed for wakeup %s", wakeup_id)
                    continue
                if result is not None:
                    claims.append(result)
            if not claims:
                continue
            if len(claims) > 1:
                logger.info(
                    "parallel dispatch: executing %d runs concurrently (%s)",
                    len(claims),
                    ", ".join(str(c.run.get("agent_id")) for c in claims),
                )

            async def _run_one(dispatch: Any) -> None:
                try:
                    await loop.run_in_executor(None, self.executor.execute, dispatch)
                except Exception:
                    logger.exception("executor.execute failed for run %s", dispatch.run.get("id"))

            await asyncio.gather(*(_run_one(c) for c in claims))
            dispatched += len(claims)

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
