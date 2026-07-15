"""Notificación de escalaciones pendientes — el sistema deja de esperar en silencio.

Cuando el orquestador escala una decisión al usuario (request_confirmation),
la interacción quedaba `pending` hasta que alguien ABRIERA el cockpit: en modo
supervisado, la latencia de decisión humana es el mayor componente del lead
time y nadie la veía. Este módulo dispara un comando configurable por el
operador (webhook via curl, notificación de escritorio, lo que sea) con el
payload de la escalación por stdin.

Config: env ``AITEAM_NOTIFY_COMMAND`` — se ejecuta con shell, recibe JSON por
stdin: {kind, title, summary, issue_id, project}. Fire-and-forget: nunca
bloquea el heartbeat, y cualquier fallo se degrada a un log.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def notify_escalation(payload: dict[str, Any]) -> bool:
    """Lanza el comando de notificación configurado (si lo hay). No bloquea."""
    command = os.environ.get("AITEAM_NOTIFY_COMMAND", "").strip()
    if not command:
        return False
    try:
        body = json.dumps(payload, ensure_ascii=False)
        proc = subprocess.Popen(  # noqa: S602 — comando del propio operador
            command,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert proc.stdin is not None
        proc.stdin.write(body.encode("utf-8"))
        proc.stdin.close()
        return True
    except Exception:
        logger.warning("escalation notify command failed", exc_info=True)
        return False
