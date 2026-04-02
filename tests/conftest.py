"""
conftest.py — Aislamiento de entorno para la suite de tests.

api/main.py llama a load_dotenv() al importarse, lo que puede cargar
variables del .env real (AITEAM_REQUIRE_API_KEYS=1, AITEAM_ENABLE_LIVE_API=1, etc.)
que contaminan tests que esperan comportamiento en modo mock/neutral.

Este conftest restablece las variables críticas al inicio de la sesión de tests.
"""

import os
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
