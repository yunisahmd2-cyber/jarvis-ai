#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OLLAMA_PIDS="$(lsof -tiTCP:11434 -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$OLLAMA_PIDS" ]; then
  echo "[jarvis] restarting ollama"
  kill $OLLAMA_PIDS 2>/dev/null || true
  sleep 1
fi

ollama serve
