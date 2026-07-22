"""Servicio local con caché breve para el read model canónico de modelos."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from aiteam.model_catalog_read_model import build_current_model_catalog_read_model
from aiteam.user_config import user_config_dir


_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}


def get_current_model_catalog(
    *, db_paths: tuple[Path, ...] = (), max_age_seconds: float = 30.0
) -> dict[str, Any]:
    """Construye una vez por configuración/DB y evita probes o red."""
    key = _cache_key(db_paths)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None and now - cached[0] <= max_age_seconds:
            return cached[1]
    read_model = build_current_model_catalog_read_model(db_paths=db_paths)
    with _CACHE_LOCK:
        _CACHE[key] = (now, read_model)
        if len(_CACHE) > 8:
            oldest = min(_CACHE, key=lambda item: _CACHE[item][0])
            _CACHE.pop(oldest, None)
    return read_model


def invalidate_model_catalog_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _cache_key(db_paths: tuple[Path, ...]) -> tuple[Any, ...]:
    config_dir = user_config_dir()
    return (
        str(config_dir.resolve()),
        _tree_mtime(config_dir),
        tuple((str(path.resolve()), _path_mtime(path)) for path in db_paths),
    )


def _tree_mtime(path: Path) -> int:
    if not path.exists():
        return 0
    return max(
        (_path_mtime(item) for item in path.rglob("*") if item.is_file()), default=0
    )


def _path_mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0
