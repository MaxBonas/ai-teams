"""Notificación de escalaciones pendientes — el sistema deja de esperar en silencio.

Cuando el orquestador escala una decisión al usuario (request_confirmation),
la interacción quedaba `pending` hasta que alguien ABRIERA el cockpit: en modo
supervisado, la latencia de decisión humana es el mayor componente del lead
time y nadie la veía. Este módulo dispara un comando configurable por el
operador (webhook via curl, notificación de escritorio, lo que sea) con el
payload de la escalación por stdin.

Config: env ``AITEAM_NOTIFY_COMMAND`` — se separa en argv y se ejecuta sin
shell; recibe JSON UTF-8 por stdin: {kind, title, summary, issue_id, project}. Fire-and-forget: nunca
bloquea el heartbeat, y cualquier fallo se degrada a un log.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from typing import Any

from aiteam.platform_runtime import UTF8_SUBPROCESS_OPTIONS, platform_id, process_group_popen_options

logger = logging.getLogger(__name__)


def notify_escalation(payload: dict[str, Any]) -> bool:
    """Lanza el comando de notificación configurado (si lo hay). No bloquea."""
    command = os.environ.get("AITEAM_NOTIFY_COMMAND", "").strip()
    if not command:
        return False
    try:
        body = json.dumps(payload, ensure_ascii=False)
        argv = shlex.split(command, posix=platform_id() != "windows")
        if platform_id() == "windows":
            argv = [
                item[1:-1]
                if len(item) >= 2 and item[0] == item[-1] and item[0] in {"'", '"'}
                else item
                for item in argv
            ]
        if not argv:
            return False
        proc = subprocess.Popen(
            argv,
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **UTF8_SUBPROCESS_OPTIONS,
            **process_group_popen_options(),
        )
        assert proc.stdin is not None
        proc.stdin.write(body)
        proc.stdin.close()
        return True
    except Exception:
        logger.warning("escalation notify command failed", exc_info=True)
        return False
