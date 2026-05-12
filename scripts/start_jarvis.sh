#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

log() {
  printf '[jarvis] %s\n' "$1"
}

load_env_file() {
  local env_path="$1"
  if [ -f "$env_path" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_path"
    set +a
  fi
}

load_env_file "$ROOT_DIR/.env"
load_env_file "$ROOT_DIR/backend/.env"

BACKEND_HOST="${APP_HOST:-127.0.0.1}"
BACKEND_PORT="${APP_PORT:-8000}"
OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"
PYTHON_BIN="$ROOT_DIR/venv/bin/python"

require_command() {
  local command_name="$1"
  local install_hint="$2"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    log "Missing required command: $command_name"
    log "$install_hint"
    exit 1
  fi
}

require_path() {
  local path="$1"
  local install_hint="$2"
  if [ ! -e "$path" ]; then
    log "Missing required path: $path"
    log "$install_hint"
    exit 1
  fi
}

run_preflight() {
  log "Running setup checks..."
  if ! "$ROOT_DIR/scripts/doctor.sh"; then
    log "Startup stopped because setup checks failed."
    exit 1
  fi
}

is_port_open() {
  local host="$1"
  local port="$2"
  "$PYTHON_BIN" - "$host" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect((host, port))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

wait_for_port() {
  local host="$1"
  local port="$2"
  local label="$3"
  local attempts="${4:-20}"
  for _ in $(seq 1 "$attempts"); do
    if is_port_open "$host" "$port"; then
      return 0
    fi
    sleep 1
  done
  log "$label did not become available on ${host}:${port}."
  return 1
}

is_http_healthy() {
  local url="$1"
  "$PYTHON_BIN" - "$url" <<'PY'
import sys
from urllib.request import urlopen

url = sys.argv[1]
try:
    with urlopen(url, timeout=1.5) as response:
        raise SystemExit(0 if 200 <= response.status < 300 else 1)
except Exception:
    raise SystemExit(1)
PY
}

wait_for_http_healthy() {
  local url="$1"
  local label="$2"
  local attempts="${3:-25}"
  for _ in $(seq 1 "$attempts"); do
    if is_http_healthy "$url"; then
      return 0
    fi
    sleep 1
  done
  log "$label did not become healthy."
  return 1
}

backend_matches_expected_config() {
  local url="$1"
  "$PYTHON_BIN" - "$url" "$ROOT_DIR" <<'PY'
import json
import sys
from urllib.request import urlopen

url = sys.argv[1]
root = sys.argv[2]
sys.path.insert(0, root)

from backend.app.core.config import get_settings  # noqa: E402

expected = get_settings()
try:
    with urlopen(url, timeout=1.5) as response:
        payload = json.load(response)
except Exception:
    raise SystemExit(1)

config = payload.get("config") if isinstance(payload, dict) else None
if not isinstance(config, dict):
    raise SystemExit(1)

checks = {
    "database_url": expected.database_url,
    "ollama_model": expected.ollama_model,
}
for key, expected_value in checks.items():
    if config.get(key) != expected_value:
        raise SystemExit(1)
PY
}

restart_backend_listener() {
  local backend_pids watcher_pids
  backend_pids="$(lsof -tiTCP:${BACKEND_PORT} -sTCP:LISTEN 2>/dev/null || true)"
  watcher_pids="$(pgrep -f 'main_v7_backend.py|python.*main_v7_backend.py|uvicorn.*main_v7_backend|watchfiles' || true)"
  if [ -n "$backend_pids" ]; then
    log "Stopping unhealthy backend listener on port ${BACKEND_PORT}."
    kill $backend_pids 2>/dev/null || true
  fi
  if [ -n "$watcher_pids" ]; then
    kill $watcher_pids 2>/dev/null || true
  fi
  sleep 1
  backend_pids="$(lsof -tiTCP:${BACKEND_PORT} -sTCP:LISTEN 2>/dev/null || true)"
  watcher_pids="$(pgrep -f 'main_v7_backend.py|python.*main_v7_backend.py|uvicorn.*main_v7_backend|watchfiles' || true)"
  if [ -n "$backend_pids" ] || [ -n "$watcher_pids" ]; then
    [ -n "$backend_pids" ] && kill -9 $backend_pids 2>/dev/null || true
    [ -n "$watcher_pids" ] && kill -9 $watcher_pids 2>/dev/null || true
    sleep 1
  fi
}

start_ollama() {
  if is_port_open "$OLLAMA_HOST" "$OLLAMA_PORT"; then
    log "Ollama is already available on ${OLLAMA_HOST}:${OLLAMA_PORT}."
    return
  fi
  log "Starting Ollama..."
  nohup ollama serve > "$ROOT_DIR/.jarvis-ollama.log" 2>&1 &
  if ! wait_for_port "$OLLAMA_HOST" "$OLLAMA_PORT" "Ollama" 20; then
    log "Ollama did not become ready. Check .jarvis-ollama.log."
    exit 1
  fi
}

start_backend() {
  if is_http_healthy "http://${BACKEND_HOST}:${BACKEND_PORT}/health"; then
    if backend_matches_expected_config "http://${BACKEND_HOST}:${BACKEND_PORT}/status"; then
      log "Backend is already healthy on ${BACKEND_HOST}:${BACKEND_PORT}."
      return
    fi
    log "Backend is healthy but using stale config; restarting."
    restart_backend_listener
  fi
  if is_port_open "$BACKEND_HOST" "$BACKEND_PORT"; then
    restart_backend_listener
  fi
  log "Starting backend..."
  nohup "$ROOT_DIR/scripts/run_backend.sh" > "$ROOT_DIR/.jarvis-backend.log" 2>&1 &
  if ! wait_for_http_healthy "http://${BACKEND_HOST}:${BACKEND_PORT}/health" "Backend" 30; then
    log "Check .jarvis-backend.log for details."
    exit 1
  fi
}

start_tauri() {
  if pgrep -f "npm run dev:tauri|cargo.*tauri dev|tauri dev" >/dev/null 2>&1; then
    log "Tauri dev process already appears to be running."
    return
  fi
  log "Starting Tauri desktop app..."
  npm run dev:tauri
}

require_command "python3" "Install Python 3 or ensure it is available on PATH."
require_command "npm" "Install Node.js and npm, then run npm install in the repo."
require_command "cargo" "Install Rust/Cargo from https://rustup.rs."
require_command "ollama" "Install Ollama and make sure 'ollama serve' can run on this Mac."
require_path "$PYTHON_BIN" "Create the venv and install backend dependencies: python3 -m venv venv && ./venv/bin/python -m pip install -r backend/requirements.txt"
require_path "$ROOT_DIR/node_modules" "Install frontend dependencies: npm install"
run_preflight

start_ollama
start_backend
start_tauri
