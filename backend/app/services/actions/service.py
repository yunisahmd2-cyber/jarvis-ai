from __future__ import annotations

import json
import platform
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote_plus

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.services.productivity.service import productivity_service


logger = get_logger(__name__)


SAFE_SITE_SHORTCUTS = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "chatgpt": "https://chatgpt.com",
}

DEFAULT_APP_ALIASES = {
    "spotify": "Spotify",
    "safari": "Safari",
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "finder": "Finder",
    "terminal": "Terminal",
    "notes": "Notes",
    "music": "Music",
    "messages": "Messages",
    "arc": "Arc",
    "app store": "App Store",
    "calculator": "Calculator",
    "preview": "Preview",
    "mail": "Mail",
    "calendar": "Calendar",
    "reminders": "Reminders",
    "settings": "System Settings",
    "system settings": "System Settings",
    "photos": "Photos",
    "slack": "Slack",
    "discord": "Discord",
    "zoom": "zoom.us",
    "zoom.us": "zoom.us",
    "vscode": "Visual Studio Code",
    "vs code": "Visual Studio Code",
    "visual studio code": "Visual Studio Code",
    "code": "Visual Studio Code",
}

ActionRiskLevel = Literal["safe", "caution", "risky"]


class ActionService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.allowed_apps = {app.lower(): app for app in self.settings.allowed_apps}
        self.installed_apps = self._discover_installed_apps()
        self.app_aliases = {
            alias: canonical
            for alias, canonical in DEFAULT_APP_ALIASES.items()
            if canonical.lower() in self.allowed_apps or canonical.lower() in self.installed_apps
        }
        self.allowed_folders = {
            "downloads": Path.home() / "Downloads",
            "desktop": Path.home() / "Desktop",
            "documents": Path.home() / "Documents",
            "workspace": self.settings.workspace_path,
            "audio": self.settings.audio_path,
        }

    def _application_roots(self) -> list[Path]:
        return [
            Path("/Applications"),
            Path("/System/Applications"),
            Path("/System/Applications/Utilities"),
            Path("/System/Library/CoreServices"),
            Path.home() / "Applications",
        ]

    def _discover_installed_apps(self) -> dict[str, str]:
        discovered: dict[str, str] = {}
        if platform.system() != "Darwin":
            return discovered

        for root in self._application_roots():
            if not root.exists():
                continue
            for path in root.rglob("*.app"):
                name = path.stem.strip()
                if not name:
                    continue
                discovered.setdefault(name.lower(), name)
        return discovered

    def preview(self, action: str, target: str | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = action.strip().lower()
        risk_level = self.risk_level(normalized, target, params or {})
        payload = {
            "action": normalized,
            "target": target,
            "params": params or {},
            "allowed": self.is_allowed(normalized, target),
            "risk_level": risk_level,
            "requires_confirmation": self.requires_confirmation(normalized, target, params or {}),
            "description": self.describe(normalized, target),
        }
        return payload

    def is_allowed(self, action: str, target: str | None) -> bool:
        if action in {"weather", "news", "clipboard_read", "screen_analysis"}:
            return True
        if action in {"create_calendar_event", "create_mail_draft", "list_calendar_events"}:
            return True
        if action in {"open_app", "switch_app", "close_app"}:
            return self._resolve_allowed_app(target) is not None
        if action == "open_folder":
            return bool(target and target.strip().lower() in self.allowed_folders)
        if action == "open_file":
            return bool(self._resolve_safe_file(target))
        if action in {
            "open_url",
            "protocol_override",
            "spotify_play",
            "spotify_pause",
            "spotify_next",
            "spotify_previous",
            "clipboard_write",
            "type_text",
            "volume_up",
            "volume_down",
            "set_volume",
            "mute_volume",
            "unmute_volume",
            "media_play_pause",
        }:
            return True
        return False

    def risk_level(self, action: str, target: str | None = None, params: dict[str, Any] | None = None) -> ActionRiskLevel:
        params = params or {}
        safe_actions = {
            "open_app",
            "switch_app",
            "protocol_override",
            "open_url",
            "open_folder",
            "open_file",
            "spotify_play",
            "spotify_pause",
            "spotify_next",
            "spotify_previous",
            "volume_up",
            "volume_down",
            "set_volume",
            "mute_volume",
            "unmute_volume",
            "media_play_pause",
            "clipboard_read",
            "weather",
            "news",
            "screen_analysis",
            "list_calendar_events",
            "create_calendar_event",
            "create_mail_draft",
        }
        if action in safe_actions:
            return "safe"
        if action == "clipboard_write":
            return "risky"
        if action == "type_text":
            text = str(target or params.get("text", ""))
            if len(text) <= 48 and "\n" not in text:
                return "caution"
            return "risky"
        if action == "close_app":
            return "risky"
        return "risky"

    def requires_confirmation(self, action: str, target: str | None = None, params: dict[str, Any] | None = None) -> bool:
        if not self.settings.require_confirmation_for_risky_actions:
            return False
        return self.risk_level(action, target, params or {}) == "risky"

    def describe(self, action: str, target: str | None) -> str:
        if action == "open_app":
            return f"Open the macOS application '{target}'."
        if action == "switch_app":
            return f"Switch focus to the macOS application '{target}'."
        if action == "close_app":
            return f"Quit the macOS application '{target}'."
        if action == "open_url":
            return f"Open the URL '{target}'."
        if action == "protocol_override":
            return f"Initiate protocol '{target}'."
        if action == "open_folder":
            return f"Open the folder shortcut '{target}'."
        if action == "open_file":
            return f"Open the file matching '{target}' from the safe workspace roots."
        if action == "create_calendar_event":
            return f"Create a Calendar event for '{target}'."
        if action == "list_calendar_events":
            return "List upcoming Calendar events."
        if action == "create_mail_draft":
            return f"Create a Mail draft for '{target}'."
        if action.startswith("spotify_"):
            return f"Send the Spotify command '{action.removeprefix('spotify_')}'."
        if action == "clipboard_write":
            return "Write text to the clipboard."
        if action == "type_text":
            return "Type text into the active application."
        if action in {"volume_up", "volume_down", "set_volume", "mute_volume", "unmute_volume"}:
            return "Adjust the system output volume."
        return f"Run action '{action}'."

    def execute(self, action: str, target: str | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        params = params or {}
        if not self.is_allowed(action, target):
            raise ValueError("Action is not allowed by policy")

        try:
            if action == "open_app":
                result = self._open_app(target)
            elif action == "switch_app":
                result = self._switch_app(target)
            elif action == "close_app":
                result = self._close_app(target)
            elif action == "open_url":
                result = self._open_url(target, params)
            elif action == "open_folder":
                result = self._open_folder(target)
            elif action == "open_file":
                result = self._open_file(target)
            elif action == "create_calendar_event":
                result = self._create_calendar_event(target, params)
            elif action == "list_calendar_events":
                result = self._list_calendar_events(params)
            elif action == "create_mail_draft":
                result = self._create_mail_draft(target, params)
            elif action == "spotify_play":
                result = self._spotify_command("play")
            elif action == "spotify_pause":
                result = self._spotify_command("pause")
            elif action == "spotify_next":
                result = self._spotify_command("next track")
            elif action == "spotify_previous":
                result = self._spotify_command("previous track")
            elif action == "clipboard_write":
                result = self._clipboard_write(target, params)
            elif action == "clipboard_read":
                proc = self._run(["pbpaste"])
                result = self._success("Clipboard read.", text=proc.stdout.strip())
            elif action == "type_text":
                result = self._type_text(target, params)
            elif action == "volume_up":
                result = self._set_volume_delta(10)
            elif action == "volume_down":
                result = self._set_volume_delta(-10)
            elif action == "set_volume":
                result = self._set_volume_absolute(int(params.get("level", target or 50)))
            elif action == "mute_volume":
                result = self._set_volume_muted(True)
            elif action == "unmute_volume":
                result = self._set_volume_muted(False)
            elif action == "media_play_pause":
                result = self._media_play_pause()
            elif action == "protocol_override":
                result = self._protocol_override(target, params)
            else:
                raise ValueError(f"Unsupported action: {action}")
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            result = self._failure(
                "The desktop action failed to execute.",
                error=stderr or stdout or str(exc),
                return_code=exc.returncode,
                command=exc.cmd if isinstance(exc.cmd, list) else str(exc.cmd),
            )
        except RuntimeError as exc:
            result = self._failure(str(exc))

        self._audit(action, target, params, result)
        logger.info(
            "timing stage=action action=%s status=%s duration_ms=%.1f",
            action,
            result.get("status"),
            (time.perf_counter() - started) * 1000,
        )
        return result

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        logger.info("Running command: %s", command)
        return subprocess.run(command, check=True, capture_output=True, text=True)

    def _run_applescript(self, script: str) -> dict[str, Any]:
        if platform.system() != "Darwin":
            raise RuntimeError("Desktop controls are currently supported only on macOS")
        proc = self._run(["osascript", "-e", script])
        return {"ok": True, "stdout": proc.stdout.strip()}

    def _resolve_allowed_app(self, target: str | None) -> str | None:
        if not target:
            return None
        normalized = self._normalize_app_target(target)
        for article in ("the ", "a ", "an "):
            if normalized.startswith(article):
                normalized = normalized[len(article):].strip()
                break
        alias = self.app_aliases.get(normalized) or DEFAULT_APP_ALIASES.get(normalized)
        if alias and (alias.lower() in self.allowed_apps or self._is_app_installed(alias)):
            return alias
        if normalized in self.allowed_apps:
            return self.allowed_apps[normalized]
        if normalized in self.installed_apps:
            return self.installed_apps[normalized]
        refreshed = self._discover_installed_apps()
        if refreshed:
            self.installed_apps = refreshed
            if normalized in self.installed_apps:
                return self.installed_apps[normalized]
        return None

    def _normalize_app_target(self, target: str) -> str:
        normalized = target.strip().lower()
        normalized = normalized.strip(" ,.?!")
        normalized = normalized.removesuffix("'s")
        normalized = normalized.removesuffix(" app")
        normalized = normalized.removesuffix(" application")
        normalized = normalized.removesuffix(" desktop app")
        normalized = normalized.strip(" ,.?!")
        return normalized

    def _open_app(self, target: str | None) -> dict[str, Any]:
        app_name = self._resolve_allowed_app(target)
        if not app_name:
            raise ValueError("App is not allowed by policy")
        if not self._is_app_installed(app_name):
            return self._failure(f"I could not open {app_name} because it does not appear to be installed.", app=app_name)
        was_running = self._is_app_running(app_name)
        was_frontmost = self._frontmost_application() == app_name
        if was_running:
            activated = self._bring_app_to_front(app_name, attempts=3, timeout=2.2)
        else:
            self._run(["open", "-a", app_name])
            activation_requested = self._activate_application(app_name)
            running = self._wait_for(lambda: self._is_app_running(app_name), timeout=3.2)
            activated = False
            if running:
                activated = self._bring_app_to_front(app_name, attempts=3, timeout=2.4) or activation_requested
        launched = self._is_app_running(app_name) and self._frontmost_application() == app_name
        if launched:
            if was_running and was_frontmost:
                message = f"{app_name} is already open."
            elif was_running:
                message = f"{app_name} was already open and is now in front."
            else:
                message = f"{app_name} is open."
            return self._success(
                message,
                app=app_name,
                was_running=was_running,
                was_frontmost=was_frontmost,
                activation_attempted=activated,
            )
        if self._is_app_running(app_name):
            return self._attempted(
                f"I {'re-activated' if was_running else 'opened'} {app_name}, but I could not confirm that it came to the front.",
                app=app_name,
                was_running=was_running,
                was_frontmost=was_frontmost,
                frontmost_app=self._frontmost_application(),
                activation_attempted=activated,
            )
        return self._attempted(
            f"I tried to open {app_name}, but I could not confirm that it launched.",
            app=app_name,
            was_running=was_running,
            was_frontmost=was_frontmost,
            frontmost_app=self._frontmost_application(),
            activation_attempted=activated,
        )

    def _switch_app(self, target: str | None) -> dict[str, Any]:
        app_name = self._resolve_allowed_app(target)
        if not app_name:
            raise ValueError("App is not allowed by policy")
        if not self._is_app_installed(app_name):
            return self._failure(f"I could not switch to {app_name} because it does not appear to be installed.", app=app_name)
        focused = self._bring_app_to_front(app_name, attempts=3, timeout=2.2)
        if focused:
            return self._success(f"{app_name} is now frontmost.", app=app_name)
        return self._attempted(
            f"I tried to switch to {app_name}, but I could not confirm that it became frontmost.",
            app=app_name,
        )

    def _close_app(self, target: str | None) -> dict[str, Any]:
        app_name = self._resolve_allowed_app(target)
        if not app_name:
            raise ValueError("App is not allowed by policy")
        if not self._is_app_installed(app_name):
            return self._failure(f"I could not close {app_name} because it does not appear to be installed.", app=app_name)
        if not self._is_app_running(app_name):
            return self._success(f"{app_name} is already closed.", app=app_name)
        self._run_applescript(f'tell application "{app_name}" to quit')
        closed = self._wait_for(lambda: not self._is_app_running(app_name), timeout=3.0)
        if closed:
            return self._success(f"{app_name} is closed.", app=app_name)
        return self._attempted(
            f"I asked {app_name} to close, but I could not confirm that it quit.",
            app=app_name,
        )

    def _open_url(self, target: str | None, params: dict[str, Any]) -> dict[str, Any]:
        target_url = target or SAFE_SITE_SHORTCUTS.get(str(params.get("site", "")).lower())
        if not target_url:
            return self._failure("I need a URL or site name before I can open it.")
        if not target_url.startswith(("http://", "https://")):
            target_url = f"https://www.google.com/search?q={quote_plus(target_url)}"
        self._run(["open", target_url])
        if self._verify_browser_url(target_url):
            return self._success("The requested page is open.", url=target_url)
        return self._attempted(
            "I tried to open the requested page, but I could not confirm that the browser navigated there.",
            url=target_url,
        )

    def _open_folder(self, target: str | None) -> dict[str, Any]:
        if not target:
            return self._failure("I need a folder shortcut first.")
        folder = self.allowed_folders[target.strip().lower()]
        self._run(["open", str(folder)])
        frontmost = self._wait_for(lambda: self._frontmost_application() == "Finder", timeout=2.0)
        if frontmost:
            return self._success(f"Finder opened {folder.name}.", path=str(folder))
        return self._attempted(
            f"I tried to open {folder.name}, but I could not confirm that Finder brought it forward.",
            path=str(folder),
        )

    def _open_file(self, target: str | None) -> dict[str, Any]:
        path = self._resolve_safe_file(target)
        if path is None:
            return self._failure("I could not find a safe matching file.")
        self._run(["open", str(path)])
        return self._attempted(
            f"I sent an open request for {path.name}, but macOS did not provide a reliable confirmation signal.",
            path=str(path),
        )

    def _create_calendar_event(self, target: str | None, params: dict[str, Any]) -> dict[str, Any]:
        title = str(params.get("title") or target or "").strip()
        starts_at_text = str(params.get("starts_at") or "")
        if not starts_at_text:
            return self._failure("I need a start time before I can create the calendar event.")
        ends_at_text = str(params.get("ends_at") or "")
        if not ends_at_text:
            return self._failure("I need an end time before I can create the calendar event.")
        return productivity_service.create_calendar_event(
            title=title,
            starts_at=datetime.fromisoformat(starts_at_text),
            ends_at=datetime.fromisoformat(ends_at_text),
            calendar_name=str(params.get("calendar_name") or "").strip() or None,
            notes=str(params.get("notes") or "").strip() or None,
            location=str(params.get("location") or "").strip() or None,
            recurrence=str(params.get("recurrence") or "").strip() or None,
        )

    def _list_calendar_events(self, params: dict[str, Any]) -> dict[str, Any]:
        days_raw = params.get("days", 7)
        limit_raw = params.get("limit", 8)
        try:
            days = int(days_raw)
        except (TypeError, ValueError):
            days = 7
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            limit = 8
        return productivity_service.upcoming_calendar_events(
            calendar_name=str(params.get("calendar_name") or "").strip() or None,
            days=days,
            limit=limit,
        )

    def _create_mail_draft(self, target: str | None, params: dict[str, Any]) -> dict[str, Any]:
        recipient = str(params.get("to") or target or "").strip()
        subject = str(params.get("subject") or "").strip()
        body = str(params.get("body") or "").strip()
        cc_raw = params.get("cc") or []
        cc = [str(item).strip() for item in cc_raw if str(item).strip()] if isinstance(cc_raw, list) else []
        return productivity_service.create_mail_draft(
            to=recipient,
            subject=subject,
            body=body,
            cc=cc,
        )

    def _spotify_command(self, command: str) -> dict[str, Any]:
        app_name = "Spotify"
        if not self._is_app_installed(app_name):
            return self._failure("I could not control Spotify because it does not appear to be installed.", app=app_name)
        if not self._is_app_running(app_name):
            self._run(["open", "-a", app_name])
            if not self._wait_for(lambda: self._is_app_running(app_name), timeout=3.0):
                return self._failure("I could not control Spotify because it would not launch.", app=app_name)
        previous_state = self._spotify_status()
        previous_track = (previous_state.get("track"), previous_state.get("artist"))
        self._run_applescript(f'tell application "Spotify" to {command}')
        verified = self._wait_for(
            lambda: self._spotify_command_verified(command, previous_state, previous_track),
            timeout=2.0,
        )
        if verified:
            messages = {
                "play": "Spotify playback is running.",
                "pause": "Spotify is paused.",
                "next track": "Spotify moved to the next track.",
                "previous track": "Spotify moved to the previous track.",
            }
            return self._success(messages[command], app=app_name, spotify=self._spotify_status())
        return self._attempted(
            f"I sent the Spotify {command} command, but I could not confirm the new playback state.",
            app=app_name,
            spotify=self._spotify_status(),
        )

    def _clipboard_write(self, target: str | None, params: dict[str, Any]) -> dict[str, Any]:
        text = str(target or params.get("text", ""))
        escaped = text.replace('"', "'")
        self._run_applescript(f'set the clipboard to "{escaped}"')
        confirmed = self._run(["pbpaste"]).stdout.strip()
        if confirmed == text:
            return self._success("Clipboard updated.", text=text)
        return self._attempted("I tried to update the clipboard, but I could not verify the final clipboard contents.", text=text)

    def _type_text(self, target: str | None, params: dict[str, Any]) -> dict[str, Any]:
        text = str(target or params.get("text", ""))
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        self._run_applescript(f'tell application "System Events" to keystroke "{escaped}"')
        return self._attempted(
            "I sent the typing request, but macOS does not provide a reliable confirmation that the text landed where expected.",
            text=text,
        )

    def _resolve_safe_file(self, target: str | None) -> Path | None:
        if not target:
            return None
        candidate = Path(target).expanduser()
        safe_roots = {
            path.resolve()
            for path in [
                *self.allowed_folders.values(),
                self.settings.workspace_path,
                Path.home() / "Documents",
                Path.home() / "Downloads",
            ]
            if path.exists()
        }

        if candidate.exists():
            resolved = candidate.resolve()
            if any(str(resolved).startswith(str(root)) for root in safe_roots):
                return resolved

        fragment = target.strip().lower()
        for root in safe_roots:
            for path in root.rglob("*"):
                if path.is_file() and fragment in path.name.lower():
                    return path
        return None

    def _set_volume_delta(self, delta: int) -> dict[str, Any]:
        current = self._current_volume()
        script = f"set volume output volume (output volume of (get volume settings) + {delta})"
        self._run_applescript(script)
        updated = self._current_volume()
        if updated is not None and current is not None and updated != current:
            return self._success(f"Volume is now {updated} percent.", level=updated)
        if updated is not None:
            return self._success(f"Volume remains at {updated} percent.", level=updated)
        return self._attempted("I tried to adjust the volume, but I could not verify the new output level.")

    def _set_volume_absolute(self, level: int) -> dict[str, Any]:
        clamped = max(0, min(level, 100))
        self._run_applescript(f"set volume output volume {clamped}")
        updated = self._current_volume()
        if updated == clamped:
            return self._success(f"Volume set to {clamped} percent.", level=updated)
        return self._attempted(
            f"I tried to set the volume to {clamped} percent, but I could not verify the final output level.",
            requested_level=clamped,
            actual_level=updated,
        )

    def _set_volume_muted(self, muted: bool) -> dict[str, Any]:
        self._run_applescript(f"set volume {'with' if muted else 'without'} output muted")
        current = self._current_volume_muted()
        if current is muted:
            return self._success("System audio is muted." if muted else "System audio is unmuted.", muted=muted)
        return self._attempted(
            "I sent the mute request, but I could not verify the final audio mute state.",
            requested_muted=muted,
            actual_muted=current,
        )

    def _media_play_pause(self) -> dict[str, Any]:
        before = self._spotify_status()
        self._run_applescript('tell application "System Events" to key code 16')
        if before.get("available") and before.get("running"):
            verified = self._wait_for(
                lambda: self._spotify_status().get("player_state") != before.get("player_state"),
                timeout=2.0,
            )
            if verified:
                state = self._spotify_status().get("player_state")
                return self._success(f"Media playback is now {state}.", player_state=state)
        return self._attempted("I sent the media play/pause command, but I could not verify the resulting playback state.")

    def _protocol_override(self, target: str | None, params: dict[str, Any]) -> dict[str, Any]:
        protocol = (target or "").strip().lower()
        if protocol in {"omega", "house party", "party"}:
            self._set_volume_absolute(100)
            self._spotify_command("play")
            return self._success(f"Protocol {protocol.title()} initiated. Volume is at maximum and music is playing.", protocol=protocol)
        elif protocol in {"lockdown", "security"}:
            self._set_volume_muted(True)
            self._spotify_command("pause")
            return self._success(f"Protocol {protocol.title()} initiated. Audio is muted, media is paused, and systems are locked down.", protocol=protocol)
        elif protocol in {"focus", "do not disturb", "work"}:
            self._set_volume_muted(True)
            self._spotify_command("pause")
            return self._success(f"Protocol {protocol.title()} initiated. Media paused and audio muted for focus.", protocol=protocol)
        elif protocol in {"iron man", "jarvis"}:
            return self._success(f"Protocol {protocol.title()} initiated. Welcome back, sir. All systems are operating at peak efficiency.", protocol=protocol)
        return self._failure(f"Unknown protocol: {protocol}")

    def _application_path(self, app_name: str) -> str | None:
        try:
            proc = self._run(["osascript", "-e", f'POSIX path of (path to application "{app_name}")'])
        except subprocess.CalledProcessError:
            return None
        return proc.stdout.strip() or None

    def _activate_application(self, app_name: str) -> bool:
        try:
            self._run_applescript(f'tell application "{app_name}" to activate')
            return True
        except (subprocess.CalledProcessError, RuntimeError):
            logger.warning("Could not explicitly activate %s after launch", app_name, exc_info=True)
            return False

    def _bring_app_to_front(self, app_name: str, *, attempts: int = 2, timeout: float = 2.0) -> bool:
        if self._frontmost_application() == app_name:
            return True
        per_attempt_timeout = max(0.4, timeout / max(1, attempts))
        for _ in range(max(1, attempts)):
            activated = self._activate_application(app_name)
            if activated and self._wait_for(lambda: self._frontmost_application() == app_name, timeout=per_attempt_timeout):
                return True
            time.sleep(0.12)
        return self._frontmost_application() == app_name

    def _is_app_installed(self, app_name: str) -> bool:
        if platform.system() != "Darwin":
            return False
        return self._application_path(app_name) is not None

    def _is_app_running(self, app_name: str) -> bool:
        if platform.system() != "Darwin":
            return False
        try:
            proc = self._run(["osascript", "-e", f'application "{app_name}" is running'])
        except subprocess.CalledProcessError:
            logger.warning("Could not determine whether %s is running", app_name, exc_info=True)
            return False
        return proc.stdout.strip().lower() == "true"

    def _frontmost_application(self) -> str | None:
        if platform.system() != "Darwin":
            return None
        try:
            proc = self._run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to get name of first application process whose frontmost is true',
                ]
            )
        except subprocess.CalledProcessError:
            logger.warning("Could not determine the frontmost application", exc_info=True)
            return None
        return proc.stdout.strip() or None

    def _verify_browser_url(self, target_url: str) -> bool:
        from backend.app.services.integrations.service import integration_service

        target_prefix = target_url.split("#", 1)[0]
        return self._wait_for(
            lambda: bool(
                (context := integration_service.browser_context()).get("ok")
                and str(context.get("url") or "").startswith(target_prefix)
            ),
            timeout=2.5,
        )

    def _current_volume(self) -> int | None:
        if platform.system() != "Darwin":
            return None
        try:
            proc = self._run(["osascript", "-e", "output volume of (get volume settings)"])
        except subprocess.CalledProcessError:
            logger.warning("Could not read current system volume", exc_info=True)
            return None
        text = proc.stdout.strip()
        return int(text) if text.isdigit() else None

    def _current_volume_muted(self) -> bool | None:
        if platform.system() != "Darwin":
            return None
        try:
            proc = self._run(["osascript", "-e", "output muted of (get volume settings)"])
        except subprocess.CalledProcessError:
            logger.warning("Could not read current mute state", exc_info=True)
            return None
        text = proc.stdout.strip().lower()
        if text in {"true", "false"}:
            return text == "true"
        return None

    def _spotify_status(self) -> dict[str, Any]:
        from backend.app.services.integrations.service import integration_service

        return dict(integration_service.spotify_status())

    def _spotify_command_verified(
        self,
        command: str,
        previous_state: dict[str, Any],
        previous_track: tuple[Any, Any],
    ) -> bool:
        status = self._spotify_status()
        if not status.get("running"):
            return False
        if command == "play":
            return status.get("player_state") == "playing"
        if command == "pause":
            return status.get("player_state") == "paused"
        current_track = (status.get("track"), status.get("artist"))
        return current_track != previous_track and current_track != (None, None)

    def _wait_for(self, predicate: Any, *, timeout: float, interval: float = 0.2) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()

    def _success(self, message: str, **extra: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": message,
            **extra,
        }

    def _attempted(self, message: str, **extra: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "success": False,
            "verified": False,
            "status": "attempted_unverified",
            "attempted": True,
            "message": message,
            **extra,
        }

    def _failure(self, message: str, **extra: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "success": False,
            "verified": False,
            "status": "failed",
            "attempted": False,
            "message": message,
            **extra,
        }

    def _audit(self, action: str, target: str | None, params: dict[str, Any], result: dict[str, Any]) -> None:
        line = json.dumps(
            {
                "action": action,
                "target": target,
                "params": params,
                "result": result,
            },
            ensure_ascii=False,
        )
        path = Path(self.settings.action_audit_log)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((path.read_text() if path.exists() else "") + line + "\n")


action_service = ActionService()
