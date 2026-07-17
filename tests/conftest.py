"""Fixtures compartidas de la suite viva.

La suite actual protege la reconstruccion v2: schema, checkout, runs, wakeups,
scheduler y shims minimos. Mantiene el entorno en modo mock para evitar que un
`.env` local active proveedores reales durante tests.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
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
_TEMP_PARENT = Path.cwd() / ".pytest-workspace-tmp"
_TEMP_SESSION = _TEMP_PARENT / f"session-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _remove_test_tree(path: Path) -> None:
    """Remove test artifacts on Windows, including read-only nested Git files."""

    if not path.exists():
        return

    def make_writeable(function, value, _exc_info) -> None:
        os.chmod(value, stat.S_IWRITE | stat.S_IREAD)
        function(value)

    shutil.rmtree(path, onerror=make_writeable)


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _clean_stale_test_sessions() -> None:
    _TEMP_PARENT.mkdir(exist_ok=True)
    for candidate in _TEMP_PARENT.iterdir():
        if candidate == _TEMP_SESSION:
            continue
        match = re.fullmatch(r"session-(\d+)-[0-9a-f]+", candidate.name)
        if match and _pid_is_running(int(match.group(1))):
            continue
        _remove_test_tree(candidate)


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
    _clean_stale_test_sessions()
    _TEMP_SESSION.mkdir(parents=True, exist_ok=True)


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

    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)[-80:]
    path = _TEMP_SESSION / f"{safe_name}-{uuid.uuid4().hex[:8]}"
    path.mkdir()
    # SQLite and TestClient can retain Windows handles until pytest exits.
    # scripts/pytest_local.bat cleans the whole session from a fresh process
    # after preserving pytest's exit code.
    yield path
