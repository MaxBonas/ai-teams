"""
conftest.py — Aislamiento de entorno y fixtures compartidos para la suite de tests.

api/main.py llama a load_dotenv() al importarse, lo que puede cargar
variables del .env real (AITEAM_REQUIRE_API_KEYS=1, AITEAM_ENABLE_LIVE_API=1, etc.)
que contaminan tests que esperan comportamiento en modo mock/neutral.

Este conftest restablece las variables críticas al inicio de la sesión de tests.

Fixtures disponibles
--------------------
aiteam_workspace  — par (workspace, runtime_dir) con dirs pre-creados en tmp_path.
make_orchestrator — factory: recibe lista de adapters, devuelve AITeamOrchestrator.
api_test_client   — (TestClient, workspace, runtime_dir) con workspace aislado y restaurado.
"""

import os
from pathlib import Path

import pytest


_TEST_ENV_OVERRIDES = {
    "AITEAM_REQUIRE_API_KEYS": "0",
    "AITEAM_ENABLE_LIVE_API": "0",
    "AITEAM_MAX_SUBSCRIPTION_ATTEMPTS": "3",
    "AITEAM_MAX_API_ATTEMPTS": "2",
    "AITEAM_PROVIDER_OPENAI_DEGRADED": "0",
    "AITEAM_PROVIDER_GOOGLE_DEGRADED": "0",
    "AITEAM_PROVIDER_ANTHROPIC_DEGRADED": "0",
    "AITEAM_PROVIDER_GROQ_DEGRADED": "0",
    # Evitar que límites/estado de producción bloqueen adapters en tests
    "AITEAM_SUBSCRIPTION_OPENAI_LIMIT_REACHED": "0",
    "AITEAM_SUBSCRIPTION_ANTHROPIC_LIMIT_REACHED": "0",
    "AITEAM_SUBSCRIPTION_GOOGLE_LIMIT_REACHED": "0",
    # Limpiar claves API para tests que verifican comportamiento sin claves
    "OPENAI_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
    "GOOGLE_API_KEY": "",
    "GEMINI_API_KEY": "",
    "GROQ_API_KEY": "",
}
_PREVIOUS_TEST_ENV: dict[str, str | None] = {}


def _apply_test_env_overrides() -> None:
    global _PREVIOUS_TEST_ENV
    if not _PREVIOUS_TEST_ENV:
        _PREVIOUS_TEST_ENV = {
            key: os.environ.get(key) for key in _TEST_ENV_OVERRIDES
        }
    os.environ.update(_TEST_ENV_OVERRIDES)


def _restore_test_env_overrides() -> None:
    global _PREVIOUS_TEST_ENV
    for key, value in _PREVIOUS_TEST_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    _PREVIOUS_TEST_ENV = {}


def pytest_configure(config) -> None:
    """Aislamiento temprano, también para unittest y módulos importados pronto."""
    _apply_test_env_overrides()


def pytest_unconfigure(config) -> None:
    _restore_test_env_overrides()


def pytest_runtest_setup(item) -> None:
    """Reaplica overrides por test por si algún import vuelve a cargar .env."""
    _apply_test_env_overrides()


@pytest.fixture(autouse=True)
def _neutral_test_env():
    """Mantiene los overrides durante cada test, también tras imports con side-effects."""
    _apply_test_env_overrides()
    yield


# ---------------------------------------------------------------------------
# Fixtures de infraestructura reutilizables
# ---------------------------------------------------------------------------

@pytest.fixture
def aiteam_workspace(tmp_path: Path):
    """Devuelve (workspace, runtime_dir) con ambos directorios ya creados.

    Uso en tests pytest-style::

        def test_algo(aiteam_workspace):
            workspace, runtime_dir = aiteam_workspace
            ...
    """
    workspace = tmp_path / "workspace"
    runtime_dir = tmp_path / "runtime"
    workspace.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    return workspace, runtime_dir


@pytest.fixture
def make_orchestrator(tmp_path: Path):
    """Factory que construye un AITeamOrchestrator con los adapters indicados.

    Uso en tests pytest-style::

        def test_algo(make_orchestrator):
            from aiteam.adapters import FakeSuccessAdapter
            orch = make_orchestrator([FakeSuccessAdapter(name="fake", provider="test")])
            ...

    Acepta ``runtime_dir`` y ``project_root`` opcionales; si no se pasan se
    crean en ``tmp_path`` automáticamente.
    """
    from aiteam.config import build_default_router_policy
    from aiteam.orchestrator import AITeamOrchestrator
    from aiteam.router import HybridRouter

    def _factory(adapters, *, runtime_dir: Path | None = None, project_root: Path | None = None):
        _runtime_dir = runtime_dir or (tmp_path / "runtime")
        _project_root = project_root or (tmp_path / "workspace")
        _runtime_dir.mkdir(parents=True, exist_ok=True)
        _project_root.mkdir(parents=True, exist_ok=True)
        router = HybridRouter(
            adapters=list(adapters),
            policy=build_default_router_policy(),
        )
        return AITeamOrchestrator(
            router=router,
            runtime_dir=_runtime_dir,
            project_root=_project_root,
        )

    return _factory


@pytest.fixture
def api_test_client(tmp_path: Path):
    """TestClient de api.main con workspace temporal aislado.

    Devuelve (client, workspace, runtime_dir). El workspace activo se restaura
    automáticamente al finalizar el test.

    Uso en tests pytest-style::

        def test_algo(api_test_client):
            client, workspace, runtime_dir = api_test_client
            response = client.get("/api/aiteam/state?environment=dev")
            ...
    """
    import api.main as api_main
    from api.utils import get_current_workspace, set_current_workspace

    workspace = tmp_path / "workspace"
    runtime_dir = workspace / ".aiteam"
    workspace.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)

    previous = get_current_workspace()
    set_current_workspace(workspace)
    try:
        from fastapi.testclient import TestClient
        client = TestClient(api_main.app)
        yield client, workspace, runtime_dir
    finally:
        set_current_workspace(previous)
