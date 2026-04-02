from __future__ import annotations

import os


_ENABLED_VALUES = {"1", "true", "yes", "on"}
_SIM_MODE_ENV_KEYS = ("AITEAM_SIM_MODE", "AITEAM_CHAT_DEMO_FAST")


def sim_mode_enabled() -> bool:
    """Devuelve si el runtime debe operar en modo simulado.

    `AITEAM_SIM_MODE` es la variable canonica.
    `AITEAM_CHAT_DEMO_FAST` se mantiene de forma transitoria por retrocompatibilidad.
    """

    for key in _SIM_MODE_ENV_KEYS:
        raw = os.getenv(key, "").strip().lower()
        if raw:
            return raw in _ENABLED_VALUES
    return False
