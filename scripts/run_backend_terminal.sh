#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BACKEND_PIDS="$(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$BACKEND_PIDS" ]; then
  echo "[jarvis] restarting backend"
  kill $BACKEND_PIDS 2>/dev/null || true
  sleep 1
fi

./scripts/run_backend.sh
