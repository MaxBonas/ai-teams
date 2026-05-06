from __future__ import annotations

from pathlib import Path
from typing import Any


class AITeamOrchestrator:
    """Compatibility stub for the retired round-based orchestrator.

    The active product direction is the v2 control plane: issues, runs,
    wakeup_requests and heartbeat scheduling. The previous monolithic
    orchestrator was intentionally removed so new code cannot keep extending
    `process_once()` / `run_until_idle()` flows by accident.
    """

    def __init__(
        self,
        *args: Any,
        runtime_dir: str | Path | None = None,
        project_root: str | Path | None = None,
        **kwargs: Any,
    ) -> None:
        self.args = args
        self.kwargs = kwargs
        self.runtime_dir = Path(runtime_dir) if runtime_dir is not None else Path("runtime")
        self.project_root = Path(project_root) if project_root is not None else Path.cwd()

    def process_once(self) -> int:
        raise RuntimeError(_RETIRED_MESSAGE)

    def run_until_idle(self, max_rounds: int = 10) -> None:
        raise RuntimeError(_RETIRED_MESSAGE)

    def run_until_idle_with_progress(self, max_rounds: int = 10):
        raise RuntimeError(_RETIRED_MESSAGE)


_RETIRED_MESSAGE = (
    "The legacy round-based AITeamOrchestrator has been retired. "
    "Use the v2 control plane: issues, runs, wakeup_requests and "
    "aiteam.heartbeat.scheduler. Legacy process_once()/run_until_idle() "
    "flows must not be reintroduced."
)
