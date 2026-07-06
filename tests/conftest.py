"""Fixtures compartidas de la suite viva.

La suite actual protege la reconstruccion v2: schema, checkout, runs, wakeups,
scheduler y shims minimos. Mantiene el entorno en modo mock para evitar que un
`.env` local active proveedores reales durante tests.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
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
    "AITEAM_SUBSCRIPTION_OPENAI_LIMIT_REACHED": "0",
    "AITEAM_SUBSCRIPTION_ANTHROPIC_LIMIT_REACHED": "0",
    "AITEAM_SUBSCRIPTION_GOOGLE_LIMIT_REACHED": "0",
    "OPENAI_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
    "GOOGLE_API_KEY": "",
    "GEMINI_API_KEY": "",
    "GROQ_API_KEY": "",
    # Aisla la config de usuario (settings.json, projects_root) de la maquina
    # real; sin esto los tests leen LOCALAPPDATA/AI Teams si existe.
    "AITEAM_USER_CONFIG_DIR": str(Path(__file__).resolve().parent.parent / ".pytest-user-config-tmp"),
    "AITEAM_PROJECTS_ROOT": "",
    # El cost breaker se prueba en tests dedicados; apagado para el resto.
    "AITEAM_COST_BREAKER_CENTS": "0",
}
_PREVIOUS_TEST_ENV: dict[str, str | None] = {}


def _apply_test_env_overrides() -> None:
    global _PREVIOUS_TEST_ENV
    if not _PREVIOUS_TEST_ENV:
        _PREVIOUS_TEST_ENV = {key: os.environ.get(key) for key in _TEST_ENV_OVERRIDES}
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
    _apply_test_env_overrides()


def pytest_unconfigure(config) -> None:
    _restore_test_env_overrides()


def pytest_runtest_setup(item) -> None:
    _apply_test_env_overrides()


@pytest.fixture(autouse=True)
def _neutral_test_env():
    _apply_test_env_overrides()
    yield


@pytest.fixture
def tmp_path(request):
    """Workspace-local tmp_path replacement.

    The Windows sandbox used by this session can create `tempfile` directories
    with ACLs that immediately become unreadable. Creating test directories via
    normal `Path.mkdir` inside the repo avoids that OS-level flake.
    """

    root = Path.cwd() / ".pytest-workspace-tmp"
    root.mkdir(exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)[-80:]
    path = root / f"{safe_name}-{uuid.uuid4().hex[:8]}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
