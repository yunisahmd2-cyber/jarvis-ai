# Cleanup Report

## Scope

Repository publish-readiness cleanup for `jarvis-ai` with conservative safety rules:

- Preserve runtime architecture
- Preserve compatibility and launch entrypoints
- Remove only clearly safe generated/stale artifacts
- Keep uncertain files and document them

## Confirmed Critical Files Preserved

- `backend/app/main.py`
- `main_v7_backend.py`
- `src/main.ts`
- `src-tauri/src/lib.rs`
- `scripts/run_backend.sh`
- `scripts/start_jarvis.sh`

## What Was Deleted (Safe)

### Generated/cache artifacts

- Python bytecode cache files under `__pycache__/` and `*.pyc`
- Test bytecode cache under `backend/tests/__pycache__/`

### Runtime-generated audio cache

- Generated files under `audio/` (`.mp3`, `.aiff`)
- Replaced with `audio/.gitkeep` placeholder

### Local runtime data and export artifacts

- `backend/data/jarvis.db` (local SQLite runtime DB)
- `jarvis-audit-export.zip` (generated export)
- `audio_files_list.txt` (empty utility artifact)

### Legacy backend snapshots (proved unused by current runtime)

- `main_v4_backend.py`
- `main_v6_backend.py`
- `backend/app/legacy_v7_snapshot.py`

Proof basis:

- Current launcher/scripts point to `main_v7_backend.py`
- Current backend runtime imports/use `backend/app/main.py`
- No active runtime references in backend app modules or launch scripts

## Files Kept Because Uncertain or Runtime-Relevant

- `memory.json`, `notes.json`, `status.json`
  - Referenced by backend config as seed/import files
- `piper_voices/*`
  - TTS model assets, optional runtime path support
- `scripts/run_backend_terminal.sh`, `scripts/run_ollama_terminal.sh`, `scripts/run_tauri_terminal.sh`
  - Kept as helper launchers; fixed to use dynamic repo path
- `workspace/`
  - Runtime workspace location used by app settings

## Security/Path Risks Found

### Fixed

- Hardcoded local machine path in scripts:
  - `scripts/run_backend_terminal.sh`
  - `scripts/run_ollama_terminal.sh`
  - `scripts/run_tauri_terminal.sh`
- These now resolve project root dynamically.

### Observed

- No committed secrets/tokens discovered by pattern scan.
- `.env.example` now uses placeholder absolute paths (no personal username/path leakage).

## .gitignore Improvements

Added/updated ignore rules for:

- Python caches and virtual environments
- `.env` and `.env.*` (keeping `.env.example`)
- Tauri build output (`src-tauri/target`, `target`)
- Runtime-generated `audio/*` and `backend/data/*` with `.gitkeep` exceptions
- Logs and generated export zips

## Recommended Future Cleanup (Optional)

- If helper terminal scripts are no longer used by your workflow, consider deprecating them in favor of `scripts/start_jarvis.sh`.
- Consider adding CI checks for `pytest`, `npm run build`, and `cargo check` before merge.
