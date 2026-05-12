#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

APP_HOST="${APP_HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-8000}"
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.1:8b}"
PYTHON_BIN="$ROOT_DIR/venv/bin/python"
FAILURES=0
WARNINGS=0

pass() { printf '[PASS] %s\n' "$1"; }
warn() { WARNINGS=$((WARNINGS + 1)); printf '[WARN] %s\n' "$1"; }
fail() { FAILURES=$((FAILURES + 1)); printf '[FAIL] %s\n' "$1"; }
info() { printf '[INFO] %s\n' "$1"; }

version_line() {
  local output
  output="$("$1" --version 2>&1 || true)"
  if [ "$1" = "ollama" ]; then
    printf '%s\n' "$output" | grep -Ei 'ollama.*version|version.*ollama' | head -n 1
    return
  fi
  printf '%s\n' "$output" | head -n 1
}

check_command() {
  local name="$1"
  local fix="$2"
  if command -v "$name" >/dev/null 2>&1; then
    pass "$name found: $(version_line "$name")"
  else
    fail "Missing $name. Fix: $fix"
  fi
}

check_port_available() {
  if [ ! -x "$PYTHON_BIN" ]; then
    warn "Skipping backend port check because venv Python is missing."
    return
  fi
  "$PYTHON_BIN" - "$APP_HOST" "$APP_PORT" <<'PY'
import socket
import sys
host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect((host, port))
except OSError:
    raise SystemExit(0)
finally:
    sock.close()
raise SystemExit(1)
PY
  case "$?" in
    0) pass "Backend port ${APP_HOST}:${APP_PORT} is available." ;;
    *) warn "Backend port ${APP_HOST}:${APP_PORT} is already in use. If Jarvis is not running, stop the conflicting process." ;;
  esac
}

check_python_deps() {
  if [ ! -x "$PYTHON_BIN" ]; then
    fail "Python virtual environment not found at venv/. Fix: python3 -m venv venv && ./venv/bin/python -m pip install -r backend/requirements.txt"
    return
  fi
  pass "Python virtual environment found: venv/"
  "$PYTHON_BIN" - <<'PY'
missing = []
for module in ("fastapi", "uvicorn", "pydantic"):
    try:
        __import__(module)
    except Exception:
        missing.append(module)
if missing:
    print(", ".join(missing))
    raise SystemExit(1)
PY
  case "$?" in
    0) pass "Backend Python dependencies are importable." ;;
    *) fail "Backend dependencies are missing. Fix: ./venv/bin/python -m pip install -r backend/requirements.txt" ;;
  esac
}

check_node_deps() {
  if [ -d "$ROOT_DIR/node_modules" ]; then
    pass "npm dependencies found: node_modules/"
  else
    fail "npm dependencies missing. Fix: npm install"
  fi
}

check_ollama_model() {
  if ! command -v ollama >/dev/null 2>&1; then
    fail "Cannot check Ollama model because ollama is missing. Fix: brew install --cask ollama"
    return
  fi
  if ollama list 2>/dev/null | awk '{print $1}' | grep -Fxq "$OLLAMA_MODEL"; then
    pass "Ollama model available: $OLLAMA_MODEL"
  else
    fail "Ollama model missing: $OLLAMA_MODEL. Fix: ollama pull $OLLAMA_MODEL"
  fi
}

check_runtime_dirs() {
  [ -d "$ROOT_DIR/audio" ] && pass "audio/ directory exists." || warn "audio/ is missing; backend will create it when needed."
  [ -d "$ROOT_DIR/backend/data" ] && pass "backend/data/ directory exists." || warn "backend/data/ is missing; backend config creates it on import."
  [ -f "$ROOT_DIR/audio/.gitkeep" ] && pass "audio/.gitkeep is present." || warn "audio/.gitkeep is missing; repository placeholder should be restored."
  [ -f "$ROOT_DIR/backend/data/.gitkeep" ] && pass "backend/data/.gitkeep is present." || warn "backend/data/.gitkeep is missing; repository placeholder should be restored."
}

printf '=== Jarvis AI Setup Doctor ===\n'
info "Repo: $ROOT_DIR"

check_command python3 "Install Python 3.11+"
check_command node "Install Node.js 18+"
check_command npm "Install npm, usually bundled with Node.js"
check_command cargo "Install Rust/Cargo from https://rustup.rs"
check_command ollama "Install Ollama from https://ollama.com/download"

check_python_deps
check_node_deps
check_ollama_model
check_port_available
check_runtime_dirs

printf '\nmacOS permission reminder:\n'
printf '%s\n' '- Microphone: required for voice input.'
printf '%s\n' '- Screen Recording: required for screenshot/context capture.'
printf '%s\n' '- Automation/Accessibility: required for app/browser control.'

printf '\n=== Summary ===\n'
if [ "$FAILURES" -gt 0 ]; then
  fail "$FAILURES critical check(s) failed. Fix those before running ./scripts/start_jarvis.sh."
  [ "$WARNINGS" -gt 0 ] && warn "$WARNINGS warning(s) also found."
  exit 1
fi
if [ "$WARNINGS" -gt 0 ]; then
  warn "No critical failures, but $WARNINGS warning(s) should be reviewed."
  exit 0
fi
pass "Environment looks ready for ./scripts/start_jarvis.sh."
