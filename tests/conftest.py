"""
conftest.py — Aislamiento de entorno para la suite de tests.

api/main.py llama a load_dotenv() al importarse, lo que puede cargar
variables del .env real (AITEAM_REQUIRE_API_KEYS=1, AITEAM_ENABLE_LIVE_API=1, etc.)
que contaminan tests que esperan comportamiento en modo mock/neutral.

Este conftest restablece las variables críticas al inicio de la sesión de tests.
"""

import os
import pytest


@pytest.fixture(autouse=True, scope="session")
def _neutral_test_env():
    """Aislamiento de entorno para todos los tests.

    api/main.py llama load_dotenv() al importarse, que carga variables
    del .env real (AITEAM_REQUIRE_API_KEYS=1, AITEAM_ENABLE_LIVE_API=1,
    claves API reales) que contaminan tests que esperan modo mock/neutral.

    - AITEAM_ENABLE_LIVE_API=0: evita llamadas reales en router/orchestrator
    - AITEAM_REQUIRE_API_KEYS=0: adapters disponibles sin claves reales
    - *_API_KEY="": evita que _gemini_health y test_api_can_require_keys
      detecten claves reales y alteren el resultado esperado
    """
    overrides = {
        "AITEAM_REQUIRE_API_KEYS": "0",
        "AITEAM_ENABLE_LIVE_API": "0",
        "AITEAM_PROVIDER_OPENAI_DEGRADED": "0",
        "AITEAM_PROVIDER_GOOGLE_DEGRADED": "0",
        "AITEAM_PROVIDER_ANTHROPIC_DEGRADED": "0",
        "AITEAM_PROVIDER_GROQ_DEGRADED": "0",
        # Limpiar claves API para tests que verifican comportamiento sin claves
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "GOOGLE_API_KEY": "",
        "GEMINI_API_KEY": "",
        "GROQ_API_KEY": "",
    }
    previous = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    yield
    # Restore
    for k, v in previous.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
