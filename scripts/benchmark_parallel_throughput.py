#!/usr/bin/env python
"""Benchmark sequential vs parallel task throughput for AI Teams orchestrator."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from aiteam.adapters.subscription import SubscriptionAdapter
from aiteam.config import build_default_router_policy
from aiteam.orchestrator import AITeamOrchestrator
from aiteam.router import HybridRouter
from aiteam.types import Role, WorkTask


class SlowSubscriptionAdapter(SubscriptionAdapter):
    def __init__(self, delay_ms: int) -> None:
        super().__init__(
            name="benchmark_pro",
            provider="openai",
            model="gpt-5.3-codex",
            capabilities={"coding", "analysis", "reasoning", "review"},
        )
        self.delay_ms = delay_ms

    def invoke(self, prompt: str):
        time.sleep(max(0, self.delay_ms) / 1000.0)
        return super().invoke(prompt)


def run_once(task_count: int, delay_ms: int, parallel_tasks: int) -> float:
    with tempfile.TemporaryDirectory(prefix="aiteam_bench_") as tmp:
        runtime_dir = Path(tmp) / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)

        old_parallel = os.getenv("AITEAM_MAX_PARALLEL_TASKS")
        os.environ["AITEAM_MAX_PARALLEL_TASKS"] = str(parallel_tasks)
        try:
            adapter = SlowSubscriptionAdapter(delay_ms=delay_ms)
            router = HybridRouter(adapters=[adapter], policy=build_default_router_policy())
            orchestrator = AITeamOrchestrator(router=router, runtime_dir=runtime_dir)

            for idx in range(task_count):
                task = WorkTask(
                    task_id=f"B-{idx + 1:03d}",
                    title=f"Benchmark task {idx + 1}",
                    description="Measure orchestrator throughput",
                    role=Role.ENGINEER,
                    metadata={
                        "required_capabilities": ["coding"],
                        "skip_quality_gates": True,
                        "skip_peer_consultation": True,
                    },
                )
                orchestrator.submit_task(task)

            start = time.perf_counter()
            orchestrator.run_until_idle(max_rounds=task_count + 10)
            elapsed = time.perf_counter() - start
            return elapsed
        finally:
            if old_parallel is None:
                os.environ.pop("AITEAM_MAX_PARALLEL_TASKS", None)
            else:
                os.environ["AITEAM_MAX_PARALLEL_TASKS"] = old_parallel


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark sequential vs parallel throughput")
    parser.add_argument("--tasks", type=int, default=24, help="Number of tasks to execute")
    parser.add_argument("--delay-ms", type=int, default=80, help="Per-task adapter delay in ms")
    parser.add_argument("--parallel", type=int, default=4, help="Parallel worker count for comparison run")
    args = parser.parse_args()

    sequential = run_once(task_count=args.tasks, delay_ms=args.delay_ms, parallel_tasks=1)
    parallel = run_once(task_count=args.tasks, delay_ms=args.delay_ms, parallel_tasks=max(1, args.parallel))

    speedup = (sequential / parallel) if parallel > 0 else 0.0
    print("AI Teams Throughput Benchmark")
    print(f"tasks={args.tasks} delay_ms={args.delay_ms} parallel={max(1, args.parallel)}")
    print(f"sequential_seconds={sequential:.3f}")
    print(f"parallel_seconds={parallel:.3f}")
    print(f"speedup_x={speedup:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
