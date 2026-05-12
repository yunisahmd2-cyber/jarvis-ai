# Jarvis AI Desktop Assistant

A local, macOS-focused desktop assistant with a cinematic orb-first interface, built for practical system control and truthful AI responses.

## Status

- Experimental project
- Local-first (no cloud backend or paid API required for core assistant flow)
- macOS-focused
- Stability and honest behavior are prioritized over flashy demos

## What It Does

- Runs a native desktop assistant UI (Tauri) with an orb-first interaction model
- Accepts voice input, routes intent, and returns spoken/text responses
- Uses a local Ollama llama-family model (`llama3.1:8b`) for assistant reasoning
- Performs bounded desktop actions with confirmation gates for risky operations
- Maintains context with reminders, memory/preferences, and app/page awareness

## Current Features

- **Voice pipeline**
  - STT via `faster-whisper`
  - TTS with free Edge TTS preferred for realistic voice output; macOS `say` and local Piper remain fallback options
- **Assistant orchestration**
  - Local Ollama responses (`llama3.1:8b`)
  - Bounded command chaining with stop-on-failure behavior
  - Lightweight session follow-up handling for simple references like "it" and clarification replies
  - Session command history for simple "what did I just ask?" and safe repeat flows
  - Lightweight response-style preferences such as concise, normal, detailed, casual, and professional
- **Desktop control**
  - App open/switch/close flows (allowlist-driven)
  - Safe media/volume controls where macOS or Spotify control is available
  - Risk-based confirmation overlay for sensitive actions
  - Truthful action result reporting (`verified`, `attempted_unverified`, `failed`)
- **Context intelligence**
  - Active app awareness
  - Browser/page awareness helpers
  - Spotify status/control integration
  - Request-based CPU/RAM/disk/battery status with simple interpretation when available
- **Vision/screen context**
  - Screenshot capture through Tauri
  - OCR/metadata fallback structure (non-semantic)
- **Productivity**
  - Reminders (create/list/due/complete)
  - Memory/preferences persistence
  - Basic Mode / Advanced Mode behavior profile
- **UI direction**
  - Orb-first main experience
  - Mission Control/context drawer for deeper system context

## Known Limitations

- Wake word is **not** a full always-on, production wake-listening loop yet.
- Vision is **not** true semantic visual understanding; current flow is OCR/metadata-oriented fallback.
- Some macOS actions cannot always be fully verified; those return `attempted_unverified`.
- Jarvis can open safe search URLs, but it does not safely click or choose the "first result" yet.
- Brightness control is not currently wired into the safe action layer.
- Ollama and backend services must be running for full assistant behavior.
- Several capabilities depend on macOS permissions (Microphone, Screen Recording, Automation/Accessibility).
- This is a local experimental assistant, not a finished commercial system.

## Tech Stack

- **Desktop app shell:** Tauri (Rust + WebView)
- **Frontend:** TypeScript
- **Backend:** FastAPI (Python)
- **LLM runtime:** Ollama (local)
- **Model profile:** llama-family (`llama3.1:8b`)
- **STT:** faster-whisper
- **TTS:** free Edge TTS preferred; macOS `say` and local Piper are fallback options

## Repository Layout

```text
jarvis-ai/
├── backend/
│   ├── app/
│   │   ├── main.py                 # Main FastAPI app
│   │   ├── api/routes/             # HTTP routes
│   │   ├── services/               # Assistant, actions, voice, integrations
│   │   └── core/                   # Config, logging, safety policy
│   ├── requirements.txt
│   └── tests/
├── src/                            # Frontend (orb-first UI)
│   └── main.ts                     # Frontend entrypoint
├── src-tauri/
│   └── src/lib.rs                  # Tauri shell
├── scripts/
│   ├── run_backend.sh              # Backend launcher
│   └── start_jarvis.sh             # Full startup (Ollama + backend + app)
├── main_v7_backend.py              # Compatibility launcher (live)
└── README.md
```

## Requirements

- macOS
- Python 3.11+ (venv recommended)
- Node.js 18+ and npm
- Rust + Cargo (for Tauri dev/build)
- Ollama installed locally
- Ollama model available: `llama3.1:8b`

## Quick Start

```bash
cd /path/to/jarvis-ai
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r backend/requirements.txt
npm install
cp .env.example .env
ollama pull llama3.1:8b
./scripts/doctor.sh
./scripts/start_jarvis.sh
```

## Running (Desktop App)

### Recommended one-command startup

```bash
./scripts/start_jarvis.sh
```

This starts Ollama (if needed), starts the backend, then launches the Tauri desktop app.

### Setup doctor

Run this when setup or launch fails:

```bash
./scripts/doctor.sh
```

It checks the Python venv, backend dependencies, npm dependencies, Cargo, Ollama, the `llama3.1:8b` model, backend port availability, runtime folders, and macOS permission reminders.

### Manual startup

1. Start Ollama:

```bash
ollama serve
```

2. Start backend:

```bash
./scripts/run_backend.sh
```

3. Start desktop app:

```bash
npm run dev:tauri
```

## macOS Permissions

Jarvis may require these permissions for full capability:

- **Microphone** (voice input)
- **Screen Recording** (screenshot capture)
- **Automation / Accessibility** (app and browser control)

If permissions are missing, behavior should degrade gracefully, and action outcomes remain explicit.

## Runtime Files

Jarvis generates local runtime files during normal use. These are intentionally not included in the public repository:

- `memory.json`
- `notes.json`
- `status.json`
- `backend/data/*.db`
- `backend/data/*.log`
- `audio/*.mp3`, `audio/*.aiff`, `audio/*.wav`

Clean examples are provided as `memory.example.json`, `notes.example.json`, and `status.example.json`. The app can start without the real JSON files; SQLite-backed memory is initialized in `backend/data/` locally.

`piper_voices/` contains optional local Piper TTS voice assets. Free Edge TTS is the default voice path, so Piper assets are only needed if you explicitly configure Piper voice mode.

## Safety and Honesty Model

Jarvis does not claim success when success cannot be confirmed.

Action outcomes are reported as:

- **`verified`**: result was confirmed
- **`attempted_unverified`**: action attempted, but reliable confirmation unavailable
- **`failed`**: action failed

Risky actions require explicit confirmation; safer bounded actions can execute directly under allowlist/policy controls.

## Troubleshooting

- **"Backend not healthy"**
  - Check backend log: `.jarvis-backend.log`
  - Verify backend health endpoint: `http://127.0.0.1:8000/health`
- **No LLM response**
  - Ensure Ollama is running and `llama3.1:8b` is available
- **Voice input not working**
  - Verify macOS microphone permission
- **Screen inspection issues**
  - Verify Screen Recording permission
- **App/browser control issues**
  - Verify Automation/Accessibility permissions

Sanity checks:

```bash
./venv/bin/python -m pytest backend/tests -q
npm run build
cd src-tauri && cargo check
```

## Roadmap

- Improve verification coverage for additional desktop actions
- Continue refining bounded context reasoning and chain reliability
- Improve clarification coverage for more natural follow-up wording
- Strengthen low-heat behavior in Basic Mode while preserving useful responsiveness
- Improve wake-word reliability without introducing heavy always-on load
- Expand testing and release hygiene for safer public iteration

## Disclaimer

Jarvis AI Desktop Assistant is a local experimental desktop assistant project.
It is not a finished commercial product, and it should be used with informed permission settings and practical expectations.
