#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TAURI_PIDS="$(pgrep -f 'target/debug/jarvis|cargo run --no-default-features' || true)"
if [ -n "$TAURI_PIDS" ]; then
  echo "[jarvis] restarting tauri"
  kill $TAURI_PIDS 2>/dev/null || true
  sleep 1
fi

npm run dev:tauri
