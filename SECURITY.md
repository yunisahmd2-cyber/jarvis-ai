# Security Policy

## Supported Scope

This repository is a local experimental desktop assistant project intended for local/self-hosted use.

## Reporting a Vulnerability

If you discover a security issue:

1. Do not open a public issue with exploit details.
2. Use GitHub Security Advisories (preferred) or contact the maintainer privately.
3. Include:
   - affected file/area
   - reproduction steps
   - impact assessment
   - suggested mitigation if available

## Secrets and Sensitive Data

- Never commit real API keys, tokens, passwords, or private credentials.
- Keep local secrets in `.env` only.
- `.env.example` must contain placeholders only.
- Verify `git status` before committing.

## Local Permissions and Safety Notes (macOS)

This app can request permissions for:

- Microphone
- Screen Recording
- Automation/Accessibility

Grant only what you trust. Reduced permissions may degrade features, and the app should fall back gracefully.

## Trust Model

Desktop actions are designed to report truthful outcomes:

- `verified`
- `attempted_unverified`
- `failed`

Some OS actions cannot be perfectly verified on macOS; review sensitive operations carefully.
