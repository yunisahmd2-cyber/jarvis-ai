# Contributing

Thanks for contributing to Jarvis AI Desktop Assistant.

## Development Setup

```bash
python3 -m venv venv
./venv/bin/python -m pip install -r backend/requirements.txt
npm install
```

## Before Opening a PR

Run:

```bash
./venv/bin/python -m pytest backend/tests -q
npm run build
cd src-tauri && cargo check
```

## Contribution Guidelines

- Keep architecture stable (`backend/app/main.py`, `main_v7_backend.py`, Tauri shell)
- Keep orb-first UI direction intact
- Avoid adding fake/unverified feature claims
- Keep risky desktop actions confirmation-gated
- Prefer conservative, test-backed changes

## Commit Hygiene

- Do not commit secrets
- Do not commit local `.env`
- Do not commit generated caches/logs/build artifacts
- Check `git status` before commit
