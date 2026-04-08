from __future__ import annotations

from datetime import datetime


def local_now() -> datetime:
    return datetime.now().astimezone()


def local_now_iso() -> str:
    return local_now().isoformat()


def local_display_timestamp() -> str:
    current = local_now()
    zone = str(current.tzname() or "").strip() or current.strftime("%z")
    return current.strftime("%Y-%m-%d %H:%M ") + zone
