#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
sh "$ROOT_DIR/scripts/prepare_dev_env.sh"
exec node "$ROOT_DIR/scripts/dev.mjs"
