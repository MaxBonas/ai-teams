import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from api.chat_delegate import _build_delegate_request, _execute_delegate_request
from api import main as api_main
from aiteam.adapters.api import ApiAdapter
from aiteam.adapters.subscription import SubscriptionAdapter
from aiteam.config import RouterPolicy
from aiteam.router import HybridRouter
from aiteam.types import Complexity, Criticality, Role, RoutingRequest, WorkTask
from aiteam.workflow_planner import PhaseSpec


def test_solo_lead_profile_normalizes_aliases() -> None:
    assert api_main._normalize_run_profile("solo_lead") == "solo_lead"
    assert api_main._normalize_run_profile("", chat_mode="direct") == "solo_lead"
    assert api_main._normalize_run_profile("team_advanced") == "team_advanced"


def test_direct_profile_reduces_plan_to_team_lead_build() -> None:
    phases = [
        PhaseSpec(
            phase_id="plan_engineering",
            role="ENGINEER",
            objective="Plan first",
            depends_on=[],
        ),
        PhaseSpec(
            phase_id="implement_toc",
            role="ENGINEER",
            objective="Implement TOC integration",
            depends_on=["plan_engineering"],
        ),
        PhaseSpec(
            phase_id="qa",
            role="QA",
            objective="Validate",
            depends_on=["implement_toc"],
        ),
    ]

    result = api_main._direct_profile_phase_specs(
        phases,
        user_message="Implement TOC integration",
    )

    assert len(result) == 1
    assert result[0].phase_id == "build"
    assert result[0].role == "TEAM_LEAD"
    assert result[0].depends_on == []
    assert "Perfil solo_lead/direct" in result[0].objective
    assert "Implement TOC integration" in result[0].objective
    assert "eso no es drift" in result[0].objective


def test_solo_lead_routing_prefers_openai_codex_mini_api_tier() -> None:
    policy = RouterPolicy()
    policy.role_primary_provider["team_lead"] = "anthropic"
    router = HybridRouter(
        adapters=[
            SubscriptionAdapter(
                name="claude_pro",
                provider="anthropic",
                model="claude-pro",
                capabilities={"reasoning", "coding"},
                role_targets={"team_lead"},
            ),
            ApiAdapter(
                name="openai_codex_mini",
                provider="openai",
                model="gpt-5-mini",
                capabilities={"reasoning", "coding"},
                role_targets={"team_lead"},
                require_key=False,
            ),
            ApiAdapter(
                name="openai_api",
                provider="openai",
                model="gpt-4.1-mini",
                capabilities={"reasoning", "coding"},
                role_targets={"team_lead"},
                require_key=False,
            ),
        ],
        policy=policy,
    )

    eligible = router.eligible_adapters(
        RoutingRequest(
            role=Role.TEAM_LEAD,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.LOW,
            required_capabilities={"reasoning", "coding"},
            preferred_tool_tier="advanced_api",
            preferred_adapters={"openai_codex_mini"},
        )
    )

    assert [adapter.name for adapter in eligible][:2] == [
        "openai_codex_mini",
        "openai_api",
    ]


def test_preferred_adapter_breaks_tie_inside_preferred_tool_tier() -> None:
    router = HybridRouter(
        adapters=[
            ApiAdapter(
                name="openai_codex_mini",
                provider="openai",
                model="gpt-5-mini",
                capabilities={"reasoning", "coding"},
                role_targets={"team_lead"},
                require_key=False,
            ),
            ApiAdapter(
                name="openai_api",
                provider="openai",
                model="gpt-4.1-mini",
                capabilities={"reasoning", "coding"},
                role_targets={"team_lead"},
                require_key=False,
            ),
            SubscriptionAdapter(
                name="claude_pro",
                provider="anthropic",
                model="claude-pro",
                capabilities={"reasoning", "coding"},
                role_targets={"team_lead"},
            ),
        ],
        policy=RouterPolicy(),
    )

    eligible = router.eligible_adapters(
        RoutingRequest(
            role=Role.TEAM_LEAD,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.LOW,
            required_capabilities={"reasoning", "coding"},
            preferred_tool_tier="advanced_api",
            preferred_adapters={"openai_api"},
        )
    )

    assert [adapter.name for adapter in eligible][:3] == [
        "openai_api",
        "openai_codex_mini",
        "claude_pro",
    ]


def test_solo_lead_profile_skips_lcp_delegate_execution() -> None:
    class _Taskboard:
        def __init__(self, task: WorkTask) -> None:
            self.task = task

        def get_task(self, task_id: str) -> WorkTask | None:
            return self.task if task_id == self.task.task_id else None

    class _EventLogger:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, object]]] = []

        def emit(self, event_type: str, payload: dict[str, object]) -> None:
            self.events.append((event_type, payload))

    class _Orchestrator:
        def __init__(self, workflow_state: dict[str, object], task: WorkTask) -> None:
            self.workflow_state = workflow_state
            self.taskboard = _Taskboard(task)
            self.event_logger = _EventLogger()
            self.saved = False

        def _get_workflow_state(self, task_root: str) -> dict[str, object]:
            return self.workflow_state

        def _save_workflow_state(self, *args: object, **kwargs: object) -> None:
            self.saved = True

    task_root = "CHAT-SOLO-DELEGATE"
    source_task = WorkTask(
        task_id=f"{task_root}::lead_close",
        title="Lead close",
        description="Close the direct run",
        role=Role.TEAM_LEAD,
        metadata={"phase": "lead_close", "run_profile": "solo_lead"},
    )
    workflow_state = {
        "run_profile": "solo_lead",
        "execution_profile": "direct",
        "phase_outputs": {
            "lead_close": (
                '[DELEGATE_TEST_RUN: "pytest tests/test_report_generator.py -q"]\n'
                "[WAIT_POLICY: quorum]\n"
                "[DELEGATE_BUDGET: 4]\n"
                "Cierre directo."
            )
        },
    }
    orch = _Orchestrator(workflow_state, source_task)

    result = _execute_delegate_request(
        orch=orch,
        task_root=task_root,
        workspace=Path.cwd(),
        runtime_dir=Path.cwd() / ".aiteam",
        delegate_request=_build_delegate_request(
            "delegate_test_run",
            query="pytest tests/test_report_generator.py -q",
            wait_policy="quorum",
            delegate_budget=4,
        ),
        source_task_id=source_task.task_id,
        source_phase="lead_close",
        delegate_cycle=0,
        rerun_budget=3,
    )

    assert result == {}
    assert orch.saved is True
    cleaned_output = str(workflow_state["phase_outputs"]["lead_close"])
    assert "DELEGATE_TEST_RUN" not in cleaned_output
    assert "WAIT_POLICY" not in cleaned_output
    assert "DELEGATE_BUDGET" not in cleaned_output
    assert cleaned_output == "Cierre directo."
    assert orch.event_logger.events[-1][0] == "lcp_directive_skipped"
    assert orch.event_logger.events[-1][1]["reason"] == "solo_lead_profile_disallows_delegation"


# ── Post-write validation ──────────────────────────────────────────────────

class _MinimalOrch:
    """Stub mínimo para probar _solo_lead_post_write_validation sin levantar infra."""

    def __init__(self, workspace: Path) -> None:
        self.execution = MagicMock()
        self.execution.executor.workspace_root = workspace
        self.event_logger = MagicMock()
        self.event_logger.emit = MagicMock()

    # Inyectar el método real desde el orchestrator
    from aiteam.orchestrator import AITeamOrchestrator
    _solo_lead_post_write_validation = AITeamOrchestrator._solo_lead_post_write_validation


def _make_task(artifact_paths: list[str]) -> WorkTask:
    task = MagicMock(spec=WorkTask)
    task.task_id = "CHAT-TEST::build"
    task.metadata = {"artifact_paths": artifact_paths, "direct_coding_executor": True}
    return task


def test_post_write_validation_passes_for_valid_python() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / "src").mkdir()
        (ws / "src" / "mod.py").write_text("def hello():\n    return 42\n")

        orch = _MinimalOrch(ws)
        task = _make_task(["src/mod.py"])
        result = orch._solo_lead_post_write_validation(task)
        assert result == "", f"Expected no error, got: {result}"


def test_post_write_validation_catches_syntax_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / "broken.py").write_text("def bad(\n    x =\n")  # SyntaxError

        orch = _MinimalOrch(ws)
        task = _make_task(["broken.py"])
        result = orch._solo_lead_post_write_validation(task)
        assert result.startswith("SyntaxError in broken.py"), f"Unexpected: {result}"
        assert "line" in result


def test_post_write_validation_skips_non_python_artifacts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / "README.md").write_text("# hello")

        orch = _MinimalOrch(ws)
        task = _make_task(["README.md"])
        result = orch._solo_lead_post_write_validation(task)
        assert result == ""


def test_post_write_validation_skips_missing_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        # artifact_paths apunta a archivo que no existe
        orch = _MinimalOrch(ws)
        task = _make_task(["nonexistent.py"])
        result = orch._solo_lead_post_write_validation(task)
        assert result == ""


def test_post_write_validation_does_not_create_pyc_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / "clean.py").write_text("x = 1\n")

        orch = _MinimalOrch(ws)
        task = _make_task(["clean.py"])
        orch._solo_lead_post_write_validation(task)

        # No debe haber creado __pycache__ ni .pyc
        pyc_files = list(ws.rglob("*.pyc"))
        assert pyc_files == [], f"Unexpected .pyc files: {pyc_files}"
