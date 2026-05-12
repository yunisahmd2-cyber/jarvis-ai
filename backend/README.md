# Jarvis Backend

This package contains the modular FastAPI backend used by the Tauri desktop frontend.

## Entry points

- Primary app import: `backend.app.main:app`
- Compatibility wrapper: `main_v7_backend.py`

## Main areas

- `app/api/routes`: REST routes plus websocket-compatible voice entrypoint
- `app/services/assistant`: orchestration, command routing, confirmations
- `app/services/actions`: allowlisted desktop and Spotify actions
- `app/services/voice` and `app/services/tts`: local STT/TTS hooks
- `app/services/memory`: SQLite-backed conversation memory and preferences
- `app/services/vision`: screenshot analysis fallback and workspace image persistence
- `app/services/integrations`: free weather, local system context, browser awareness, and Spotify status reads
- `app/core/config.py`: environment-driven settings

## Notes

- Ollama stays on `llama3.1:8b` by default.
- The backend auto-imports legacy `memory.json` and `notes.json` into SQLite on first startup.
- Screenshot capture is triggered from the frontend through a Tauri command, then analyzed by `/vision/analyze`.
- Spotify status is available via `GET /integrations/spotify/status`.
