#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN=""

if [ -x "venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "[jarvis] No Python interpreter found (tried venv/bin/python, python3, python)." >&2
  exit 1
fi

if [ "$PYTHON_BIN" != "$ROOT_DIR/venv/bin/python" ]; then
  echo "[jarvis] Warning: venv/bin/python was not found; using $PYTHON_BIN." >&2
  echo "[jarvis] Recommended setup: python3 -m venv venv && ./venv/bin/python -m pip install -r backend/requirements.txt" >&2
fi

"$PYTHON_BIN" - <<'PY'
missing = []
for module in ("fastapi", "uvicorn", "pydantic"):
    try:
        __import__(module)
    except Exception:
        missing.append(module)
if missing:
    print("[jarvis] Missing backend Python dependencies: " + ", ".join(missing))
    print("[jarvis] Fix: ./venv/bin/python -m pip install -r backend/requirements.txt")
    raise SystemExit(1)
PY

"$PYTHON_BIN" main_v7_backend.py
