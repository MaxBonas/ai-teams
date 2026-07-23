#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONDONTWRITEBYTECODE=1

sh "$SCRIPT_DIR/python_local.sh" "$SCRIPT_DIR/cleanup_test_artifacts.py"
sh "$SCRIPT_DIR/python_local.sh" -m pytest -p no:cacheprovider "$@"
EXIT_CODE=$?
sh "$SCRIPT_DIR/python_local.sh" "$SCRIPT_DIR/cleanup_test_artifacts.py"
exit "$EXIT_CODE"
