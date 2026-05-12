#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH_COMMAND="cd \"$ROOT_DIR\" && ./scripts/start_jarvis.sh"

printf '[jarvis] open_jarvis_tabs.sh now forwards to scripts/start_jarvis.sh\n'

osascript - "$LAUNCH_COMMAND" <<'APPLESCRIPT'
on run argv
  tell application "Terminal"
    activate
    do script (item 1 of argv)
  end tell
end run
APPLESCRIPT
