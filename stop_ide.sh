#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_EXE="$ROOT_DIR/venv/bin/python"

if [ ! -x "$PYTHON_EXE" ]; then
    echo "[AI Team IDE] ERROR: falta el Python local; no se detendran procesos sin verificar identidad." >&2
    exit 1
fi
exec "$PYTHON_EXE" "$ROOT_DIR/scripts/ide_processes.py" stop
