#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON_EXE="$ROOT_DIR/venv/bin/python"

if [ ! -x "$PYTHON_EXE" ]; then
    echo "[python_local] ERROR: falta venv local; ejecuta: sh scripts/prepare_dev_env.sh" >&2
    exit 1
fi
exec "$PYTHON_EXE" "$@"
