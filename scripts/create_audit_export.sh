#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_ZIP="${1:-$ROOT_DIR/jarvis-audit-export.zip}"

TMP_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-audit-export.XXXXXX")"
STAGE_DIR="$TMP_PARENT/jarvis-audit-export"
mkdir -p "$STAGE_DIR"

cleanup() {
  rm -rf "$TMP_PARENT"
}
trap cleanup EXIT

echo "[audit-export] staging files from: $ROOT_DIR"
echo "[audit-export] output zip: $OUTPUT_ZIP"

rsync -a \
  --exclude=".git/" \
  --exclude=".DS_Store" \
  --exclude="venv/" \
  --exclude="node_modules/" \
  --exclude="dist/" \
  --exclude=".pytest_cache/" \
  --exclude="__pycache__/" \
  --exclude="*.pyc" \
  --exclude=".jarvis-*.log" \
  --exclude="*.log" \
  --exclude="audio/" \
  --exclude="workspace/" \
  --exclude="backend/data/" \
  --exclude="src-tauri/target/" \
  --exclude="piper_voices/" \
  --exclude="jarvis-audit-export.zip" \
  --exclude="*.zip" \
  "$ROOT_DIR/" "$STAGE_DIR/"

rm -f "$OUTPUT_ZIP"

(
  cd "$TMP_PARENT"
  zip -qr "$OUTPUT_ZIP" "jarvis-audit-export"
)

echo "[audit-export] done"
echo "[audit-export] size: $(du -h "$OUTPUT_ZIP" | awk '{print $1}')"
