#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
VENV_DIR="$ROOT_DIR/venv"
PYTHON_EXE="$VENV_DIR/bin/python"
STATE_HASH="$VENV_DIR/.aiteam-pyproject.sha256"
FRONTEND_DIR="$ROOT_DIR/ide-frontend"
FRONTEND_STATE="$FRONTEND_DIR/node_modules/.aiteam-lock.sha256"
LOCK_DIR="$ROOT_DIR/runtime/.bootstrap.lock.d"
LOCK_OWNER="$LOCK_DIR/owner"

if command -v python3 >/dev/null 2>&1; then
    BASE_PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    BASE_PYTHON=python
else
    echo "[prepare_dev_env] ERROR: Python 3 no encontrado." >&2
    exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
    echo "[prepare_dev_env] ERROR: npm no encontrado." >&2
    exit 1
fi

for REQUIRED in \
    "$ROOT_DIR/pyproject.toml" \
    "$ROOT_DIR/requirements-dev.lock" \
    "$FRONTEND_DIR/package.json" \
    "$FRONTEND_DIR/package-lock.json" \
    "$SCRIPT_DIR/audit_installation_support.py"; do
    if [ ! -f "$REQUIRED" ]; then
        echo "[prepare_dev_env] ERROR: bootstrap incompleto; falta un input versionado. No se modifico runtime." >&2
        exit 1
    fi
done

mkdir -p "$ROOT_DIR/runtime"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    LOCK_PID=
    if [ -f "$LOCK_OWNER" ]; then
        LOCK_PID=$(sed -n '1p' "$LOCK_OWNER" 2>/dev/null || true)
    fi
    if [ -z "$LOCK_PID" ]; then
        echo "[prepare_dev_env] ERROR: bootstrap ocupado; owner aun no observable." >&2
        exit 1
    fi
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        echo "[prepare_dev_env] ERROR: bootstrap ocupado por PID $LOCK_PID." >&2
        exit 1
    fi
    rm -f "$LOCK_OWNER"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        echo "[prepare_dev_env] ERROR: no se pudo recuperar el lock de bootstrap." >&2
        exit 1
    fi
fi
printf '%s\n' "$$" > "$LOCK_OWNER"
release_lock() {
    rm -f "$LOCK_OWNER"
    rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap release_lock EXIT
trap 'exit 130' HUP INT TERM

if [ ! -x "$PYTHON_EXE" ]; then
    "$BASE_PYTHON" -m venv "$VENV_DIR"
fi

PYPROJECT_HASH=$("$BASE_PYTHON" -c "import hashlib,pathlib,sys; h=hashlib.sha256(); [h.update(p.name.encode()+b':' + hashlib.sha256(p.read_bytes()).hexdigest().encode()+b'\\n') for p in map(pathlib.Path,sys.argv[1:])]; print(h.hexdigest())" "$ROOT_DIR/pyproject.toml" "$ROOT_DIR/requirements-dev.lock")
STORED_PYPROJECT_HASH=
if [ -f "$STATE_HASH" ]; then
    STORED_PYPROJECT_HASH=$(tr -d '\r\n' < "$STATE_HASH")
fi
if [ "$PYPROJECT_HASH" != "$STORED_PYPROJECT_HASH" ]; then
    (
        cd "$ROOT_DIR"
        "$PYTHON_EXE" -m pip install --disable-pip-version-check --require-hashes -r requirements-dev.lock
        "$PYTHON_EXE" -m pip install --disable-pip-version-check --no-deps --no-build-isolation -e .
    )
    printf '%s\n' "$PYPROJECT_HASH" > "$STATE_HASH"
fi

mkdir -p "$ROOT_DIR/runtime/archive" "$ROOT_DIR/runtime/ollama"
for NAME in control_plane agents; do
    SOURCE="$ROOT_DIR/config/$NAME.example.json"
    TARGET="$ROOT_DIR/runtime/$NAME.json"
    if [ ! -f "$TARGET" ]; then
        cp "$SOURCE" "$TARGET"
    fi
done

FRONTEND_HASH=$("$BASE_PYTHON" -c "import hashlib,pathlib,sys; p=pathlib.Path(sys.argv[1]); h=hashlib.sha256(); [h.update(x.read_bytes()) for x in (p/'package.json',p/'package-lock.json') if x.exists()]; print(h.hexdigest())" "$FRONTEND_DIR")
STORED_FRONTEND_HASH=
if [ -f "$FRONTEND_STATE" ]; then
    STORED_FRONTEND_HASH=$(tr -d '\r\n' < "$FRONTEND_STATE")
fi
if [ ! -d "$FRONTEND_DIR/node_modules" ] || [ "$FRONTEND_HASH" != "$STORED_FRONTEND_HASH" ]; then
    (
        cd "$FRONTEND_DIR"
        npm ci --prefer-offline --no-fund --no-audit
    )
    printf '%s\n' "$FRONTEND_HASH" > "$FRONTEND_STATE"
fi

"$PYTHON_EXE" "$SCRIPT_DIR/audit_installation_support.py"
echo "[prepare_dev_env] Entorno local listo."
