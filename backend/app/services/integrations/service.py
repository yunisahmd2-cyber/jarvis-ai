from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime
from urllib.parse import quote_plus, urlparse

import httpx

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.services.productivity.service import productivity_service
from backend.app.services.memory.service import memory_service
from backend.app.services.reminders.service import reminder_service
from backend.app.services.voice.wakeword import wake_word_service


logger = get_logger(__name__)


class IntegrationService:
    def _read_cpu_usage(self) -> dict[str, object]:
        load_1m, load_5m, load_15m = os.getloadavg()
        cpu_count = max(1, os.cpu_count() or 1)
        data: dict[str, object] = {
            "load_1m": round(load_1m, 2),
            "load_5m": round(load_5m, 2),
            "load_15m": round(load_15m, 2),
            "cpu_count": cpu_count,
            "load_ratio_percent": round((load_1m / cpu_count) * 100, 1),
            "usage_percent": None,
            "idle_percent": None,
        }
        try:
            proc = subprocess.run(
                ["top", "-l", "1", "-n", "0", "-stats", "cpu"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            match = re.search(r"CPU usage:\s*([\d.]+)% user,\s*([\d.]+)% sys,\s*([\d.]+)% idle", proc.stdout)
            if match:
                user = float(match.group(1))
                system = float(match.group(2))
                idle = float(match.group(3))
                data.update(
                    {
                        "user_percent": round(user, 1),
                        "system_percent": round(system, 1),
                        "idle_percent": round(idle, 1),
                        "usage_percent": round(user + system, 1),
                    }
                )
        except Exception:
            logger.debug("CPU usage percentage read failed", exc_info=True)
        return data

    def _read_memory_usage(self) -> dict[str, object]:
        total_proc = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        total_bytes = int(total_proc.stdout.strip())
        vm_proc = subprocess.run(
            ["vm_stat"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        page_size_match = re.search(r"page size of (\d+) bytes", vm_proc.stdout)
        page_size = int(page_size_match.group(1)) if page_size_match else 4096
        pages: dict[str, int] = {}
        for line in vm_proc.stdout.splitlines():
            match = re.match(r"Pages ([^:]+):\s+([\d.]+)\.", line.strip())
            if match:
                pages[match.group(1).strip().lower()] = int(match.group(2).replace(".", ""))
        free_pages = pages.get("free", 0) + pages.get("speculative", 0)
        free_bytes = free_pages * page_size
        used_bytes = max(0, total_bytes - free_bytes)
        return {
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "free_bytes": max(0, total_bytes - used_bytes),
            "used_percent": round((used_bytes / total_bytes) * 100, 1) if total_bytes else None,
        }

    def _read_battery(self) -> dict[str, object] | None:
        try:
            proc = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=2, check=False)
        except Exception:
            return None
        percent = re.search(r"(\d+)%", proc.stdout)
        if not percent:
            return None
        return {
            "percent": int(percent.group(1)),
            "charging": "AC Power" in proc.stdout or "charging" in proc.stdout.lower(),
        }

    def _read_top_processes(self, limit: int = 5) -> list[dict[str, object]]:
        try:
            proc = subprocess.run(
                ["ps", "-arcwwwxo", "pid,comm,%cpu,%mem"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except Exception:
            logger.debug("Top process read failed", exc_info=True)
            return []
        processes: list[dict[str, object]] = []
        for line in proc.stdout.splitlines()[1:]:
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            try:
                processes.append(
                    {
                        "pid": int(parts[0]),
                        "name": parts[1],
                        "cpu_percent": float(parts[2]),
                        "memory_percent": float(parts[3]),
                    }
                )
            except ValueError:
                continue
            if len(processes) >= limit:
                break
        return processes

    def system_status(self) -> dict[str, object]:
        try:
            cpu = self._read_cpu_usage()
            memory = self._read_memory_usage()
            disk_usage = shutil.disk_usage("/")
            battery = self._read_battery()
            top_processes = self._read_top_processes()
        except Exception as exc:
            logger.exception("System status read failed")
            return {
                "ok": False,
                "status": "failed",
                "summary": f"I couldn't read system status reliably: {exc}",
            }

        cpu_usage = cpu.get("usage_percent")
        cpu_text = (
            f"CPU usage is {cpu_usage}%"
            if cpu_usage is not None
            else f"CPU load average is {cpu['load_1m']} across {cpu['cpu_count']} cores"
        )
        memory_text = f"RAM usage is {memory['used_percent']}%"
        disk_percent = round((disk_usage.used / disk_usage.total) * 100, 1) if disk_usage.total else 0.0
        disk_text = f"disk usage is {disk_percent}%"
        battery_text = ""
        if battery:
            battery_text = f" Battery is at {battery['percent']}%."
        status = "verified" if cpu_usage is not None else "attempted_unverified"
        precision_note = "" if status == "verified" else " Exact CPU percentage was unavailable, so I used load average."
        notes: list[str] = []
        if isinstance(cpu_usage, (int, float)) and cpu_usage >= 85:
            notes.append("CPU load is high.")
        load_ratio = cpu.get("load_ratio_percent")
        if cpu_usage is None and isinstance(load_ratio, (int, float)) and load_ratio >= 85:
            notes.append("CPU load average is high.")
        memory_percent = memory.get("used_percent")
        if isinstance(memory_percent, (int, float)) and memory_percent >= 85:
            notes.append("RAM usage is high, so memory pressure may be contributing.")
        if disk_percent >= 90:
            notes.append("Disk usage is nearly full.")
        if battery and isinstance(battery.get("percent"), int) and int(battery["percent"]) <= 20 and not battery.get("charging"):
            notes.append("Battery is low.")
        if not notes:
            notes.append("No obvious local resource pressure stands out from these readings.")
        if any(term in " ".join(notes).lower() for term in ("high", "full", "low")):
            if top_processes:
                top = top_processes[0]
                notes.append(
                    f"Top visible process sample: {top['name']} using about {top['cpu_percent']}% CPU."
                )
            notes.append("I cannot identify the exact process cause from this check alone.")
        interpretation = " ".join(notes)
        return {
            "ok": True,
            "status": status,
            "cpu": cpu,
            "memory": memory,
            "disk": {
                "total_bytes": disk_usage.total,
                "used_bytes": disk_usage.used,
                "free_bytes": disk_usage.free,
                "used_percent": disk_percent,
            },
            "battery": battery,
            "top_processes": top_processes,
            "interpretation": interpretation,
            "summary": f"{cpu_text}. {memory_text}; {disk_text}.{battery_text} {interpretation}{precision_note}",
        }

    async def _safe_weather(self, place: str) -> dict[str, object]:
        try:
            return await self.get_weather(place)
        except Exception:
            logger.exception("Weather integration failed")
            return {
                "ok": False,
                "summary": "Live weather is temporarily unavailable. I can still help with local tasks.",
                "raw": None,
            }

    async def _safe_news(self, topic: str) -> dict[str, object]:
        try:
            return await self.get_news(topic)
        except Exception:
            logger.exception("News integration failed")
            return {
                "ok": False,
                "summary": "Live headlines are temporarily unavailable. I can still run desktop actions.",
                "headlines": [],
            }

    async def _safe_page_summary(self, context: dict[str, object] | None = None) -> dict[str, object]:
        try:
            return await self.summarize_current_page(context=context)
        except Exception:
            logger.exception("Page summary integration failed")
            return {
                "ok": False,
                "summary": "I couldn't summarize the current page right now, but core assistant controls are still online.",
                "context": {"ok": False, "message": "Page summary unavailable."},
            }

    def _page_domain(self, url: str | None) -> str | None:
        if not url:
            return None
        try:
            parsed = urlparse(url)
        except Exception:
            return None
        domain = (parsed.netloc or "").strip().lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain or None

    def _app_action_suggestions(
        self,
        active_app: dict[str, object],
        browser: dict[str, object],
        spotify: dict[str, object],
    ) -> list[str]:
        app_name = str(active_app.get("app") or "").strip().lower()

        if app_name in {"safari", "google chrome", "arc"}:
            title = str(browser.get("title") or "the current page").strip()
            return [
                f"Summarize {title}.",
                "Search Google for related context.",
                "Open a new tab and continue the current task.",
            ]
        if app_name == "finder":
            return [
                "Open Downloads.",
                "Open Documents.",
                "Switch back to the previous app when done.",
            ]
        if app_name in {"calendar", "reminders"}:
            return [
                "List upcoming reminders.",
                "Open Mail next.",
                "Give me a quick operator briefing.",
            ]
        if app_name in {"notes", "mail"}:
            return [
                "Summarize what is on screen.",
                "Search Google for related context.",
                "Switch back after handling this item.",
            ]
        if app_name in {"slack", "discord"}:
            return [
                "Summarize the current thread or topic.",
                "Open Calendar after this.",
                "Switch back to the previous app when done.",
            ]
        if app_name == "spotify" or spotify.get("running"):
            return [
                "Resume or pause playback.",
                "Skip to the next track.",
                "Give me the current track and artist.",
            ]
        if app_name in {"terminal", "visual studio code", "xcode"}:
            return [
                "Search the current issue on Google.",
                "Open Safari for documentation.",
                "Give me a quick operator briefing.",
            ]
        return [
            "Open Google and search for something.",
            "Give me a quick operator briefing.",
            "Tell me what app is active right now.",
        ]

    def _merge_suggestions(self, *groups: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for group in groups:
            for item in group:
                normalized = item.strip()
                key = normalized.lower().rstrip(".")
                if not normalized or key in seen:
                    continue
                seen.add(key)
                merged.append(normalized)
        return merged

    def _contextual_suggestions(
        self,
        active_app: dict[str, object],
        browser: dict[str, object],
        spotify: dict[str, object],
        page_summary: str | None = None,
    ) -> list[str]:
        mode = memory_service.get_power_mode()
        suggestions = self._app_action_suggestions(active_app, browser, spotify)
        app_name = str(active_app.get("app") or "").strip().lower()
        browser_title = str(browser.get("title") or "").strip().lower()

        page_driven: list[str] = []
        if browser.get("ok"):
            page_driven.extend(
                [
                    "Summarize this page.",
                    "Search based on this page.",
                    "Open this in Google.",
                ]
            )
            if "youtube" in browser_title:
                page_driven.append("Compare this with a source article.")
            if "github" in browser_title:
                page_driven.append("Search based on this page for related issues.")
            if "docs" in browser_title or "documentation" in browser_title:
                page_driven.append("Summarize this page into key action steps.")
        elif app_name == "finder":
            page_driven.extend(
                [
                    "Open Downloads.",
                    "Open Documents.",
                    "Switch back when done.",
                ]
            )

        if page_summary and "couldn't fetch" in page_summary.lower():
            page_driven.append("Use active app context for search instead.")

        merged = self._merge_suggestions(suggestions, page_driven)
        if mode == "basic":
            return merged[:4]
        return merged[:6]

    def mode_profile(self) -> dict[str, object]:
        mode = memory_service.get_power_mode()
        if mode == "advanced":
            return {
                "mode": mode,
                "summary": (
                    "Advanced mode uses richer context synthesis, deeper page summaries, and more expansive suggestions. "
                    "Heavier analysis remains on-demand only."
                ),
                "features": {
                    "llm_budget": "higher",
                    "page_summary_depth": "richer",
                    "suggestion_depth": "expanded",
                    "vision_analysis": "on-demand richer",
                    "background_load": "moderate",
                },
            }
        return {
            "mode": mode,
            "summary": (
                "Basic mode keeps Jarvis thermally lighter with shorter responses, compact summaries, and reduced suggestion depth. "
                "Vision/OCR remain on-demand."
            ),
            "features": {
                "llm_budget": "lower",
                "page_summary_depth": "compact",
                "suggestion_depth": "focused",
                "vision_analysis": "on-demand lightweight",
                "background_load": "low",
            },
        }

    def active_app_intelligence(self) -> dict[str, object]:
        active_app = self.active_application()
        app_name = str(active_app.get("app") or "Unknown")
        confidence = "high" if active_app.get("ok") else "low"
        if memory_service.get_power_mode() == "basic":
            suggestions = self._contextual_suggestions(active_app, {"ok": False}, {"running": False})
            return {
                "ok": bool(active_app.get("ok")),
                "app": app_name,
                "active_app": active_app,
                "browser": {"ok": False, "message": "Browser context skipped in Basic Mode for faster active-app checks."},
                "spotify": {"running": False, "message": "Spotify status skipped in Basic Mode for faster active-app checks."},
                "suggestions": suggestions,
                "confidence": confidence,
                "summary": (
                    f"{active_app.get('message', 'I could not identify the active application.')} "
                    f"Suggested next actions: {'; '.join(suggestions[:3])}."
                ),
            }
        browser = self.browser_context()
        spotify = self.spotify_status()
        suggestions = self._contextual_suggestions(active_app, browser, spotify)
        summary_parts = [
            str(active_app.get("message", "I couldn't identify the active application.")),
            f"Suggested next actions: {'; '.join(suggestions[:3])}.",
        ]
        if browser.get("ok"):
            summary_parts.append(
                f"Current browser tab: {browser.get('title') or browser.get('url')}."
            )
        return {
            "ok": bool(active_app.get("ok")),
            "app": app_name,
            "active_app": active_app,
            "browser": browser,
            "spotify": spotify,
            "suggestions": suggestions,
            "confidence": confidence,
            "summary": " ".join(summary_parts),
        }

    def page_awareness(self) -> dict[str, object]:
        context = self.browser_context()
        if not context.get("ok"):
            return {
                "ok": False,
                "app": context.get("app"),
                "url": context.get("url"),
                "title": context.get("title"),
                "domain": self._page_domain(str(context.get("url") or "")),
                "message": context.get("message", "Current page context is unavailable."),
            }

        url = str(context.get("url") or "")
        title = str(context.get("title") or "").strip() or "Untitled page"
        domain = self._page_domain(url) or "unknown domain"
        app_name = str(context.get("app") or "browser")
        return {
            "ok": True,
            "app": app_name,
            "url": url,
            "title": title,
            "domain": domain,
            "message": f"You are in {app_name} on {title} ({domain}).",
        }

    async def search_based_on_current_page(self, modifier: str | None = None) -> dict[str, object]:
        context = self.browser_context()
        if not context.get("ok"):
            active = self.active_application()
            active_app = str(active.get("app") or "").strip()
            fallback_query = " ".join(piece for piece in [modifier or "", active_app] if piece).strip()
            if fallback_query:
                search = await self.search_web(fallback_query)
                return {
                    "ok": bool(search.get("ok")),
                    "query": fallback_query,
                    "context": context,
                    "active_app": active_app,
                    "summary": (
                        f"I couldn't read browser page context, so I used active app context ({active_app}). "
                        f"{search.get('summary', '')}"
                    ).strip(),
                    "google_url": search.get("google_url"),
                    "results": search.get("results", []),
                }
            return {
                "ok": False,
                "query": None,
                "context": context,
                "summary": context.get("message", "I couldn't read the current browser context."),
                "google_url": None,
            }

        title = str(context.get("title") or "").strip()
        url = str(context.get("url") or "").strip()
        domain = self._page_domain(url) or ""
        query_bits = [piece for piece in [title, domain, modifier or ""] if piece]
        query = " ".join(query_bits).strip() or (title or domain)
        if not query:
            return {
                "ok": False,
                "query": None,
                "context": context,
                "summary": "I can see the page, but I don't have enough text context to build a search query.",
                "google_url": None,
            }

        search = await self.search_web(query)
        summary = (
            f"Using the current page as context ({title or domain}), I searched for {modifier or query}. "
            f"{search.get('summary', '')}"
        ).strip()
        return {
            "ok": bool(search.get("ok")),
            "query": query,
            "context": context,
            "summary": summary,
            "google_url": search.get("google_url"),
            "results": search.get("results", []),
        }

    async def contextual_brief(self) -> dict[str, object]:
        active_app = self.active_application()
        browser = self.browser_context()
        spotify = self.spotify_status()

        page_summary: dict[str, object] | None = None
        if browser.get("ok"):
            page_summary = await self._safe_page_summary(context=browser)
        suggestions = self._contextual_suggestions(active_app, browser, spotify, str((page_summary or {}).get("summary") or ""))

        lines = [
            str(active_app.get("message", "Active app context is unavailable.")),
        ]
        if browser.get("ok"):
            lines.append(
                f"Current tab: {browser.get('title') or browser.get('url')}."
            )
        else:
            lines.append(str(browser.get("message", "Browser context is unavailable.")))
        if page_summary and page_summary.get("summary"):
            lines.append(str(page_summary.get("summary")))
        if spotify.get("running"):
            lines.append(
                f"Spotify is {spotify.get('player_state')} with {spotify.get('track') or 'Unknown track'} by {spotify.get('artist') or 'Unknown artist'}."
            )
        lines.append("Suggested next actions: " + "; ".join(suggestions[:3]) + ".")

        return {
            "ok": True,
            "active_app": active_app,
            "browser": browser,
            "page_summary": page_summary,
            "spotify": spotify,
            "suggestions": suggestions,
            "confidence": "high" if active_app.get("ok") else "low",
            "summary": " ".join(lines),
        }

    def capability_report(self) -> dict[str, object]:
        settings = get_settings()
        active_app = self.active_application()
        browser = self.browser_context()
        spotify = self.spotify_status() if settings.spotify_enabled else {
            "enabled": False,
            "available": False,
            "running": False,
            "message": "Spotify integration is disabled.",
        }
        capabilities = {
            "desktop_actions": platform.system() == "Darwin",
            "active_app_awareness": bool(active_app.get("ok")),
            "browser_awareness": bool(browser.get("ok")),
            "spotify": bool(spotify.get("enabled")),
            "calendar_write": platform.system() == "Darwin",
            "mail_drafts": platform.system() == "Darwin",
            "vision_route": settings.screen_analysis_enabled or True,
            "wake_word_optional": True,
            "risky_actions_require_confirmation": settings.require_confirmation_for_risky_actions,
        }
        summary_parts = [
            "Safe desktop control is available." if capabilities["desktop_actions"] else "Desktop control is limited on this platform.",
            f"Allowed apps: {', '.join(settings.allowed_apps[:6])}" + ("..." if len(settings.allowed_apps) > 6 else "."),
            "Browser awareness is online." if capabilities["browser_awareness"] else "Browser awareness is limited right now.",
            "Spotify control is available." if capabilities["spotify"] else "Spotify control is disabled.",
            "Calendar writing is available." if capabilities["calendar_write"] else "Calendar writing is unavailable on this platform.",
            "Mail drafting is available." if capabilities["mail_drafts"] else "Mail drafting is unavailable on this platform.",
            "Risky actions still require confirmation." if capabilities["risky_actions_require_confirmation"] else "Risky actions are not currently confirmation-gated.",
        ]
        return {
            "ok": True,
            "platform": platform.system(),
            "allowed_apps": settings.allowed_apps,
            "allowed_folders": ["downloads", "desktop", "documents", "workspace", "audio"],
            "capabilities": capabilities,
            "wake_word": wake_word_service.status(),
            "summary": " ".join(summary_parts),
        }

    async def get_weather(self, place: str) -> dict[str, object]:
        geo_url = "https://geocoding-api.open-meteo.com/v1/search"
        normalized_place = place.strip() or "Muscat"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                geo_response = await client.get(
                    geo_url,
                    params={"name": normalized_place, "count": 1, "language": "en", "format": "json"},
                )
                geo_response.raise_for_status()
                geo_data = geo_response.json()
        except httpx.HTTPError:
            logger.warning("Weather geocoding request failed for %s", normalized_place, exc_info=True)
            return {
                "ok": False,
                "summary": f"I couldn't reach free weather services for {normalized_place} right now.",
                "raw": None,
            }

        results = geo_data.get("results") or []
        if not results:
            return {"ok": False, "summary": f"I couldn't find weather data for {normalized_place}."}

        place_data = results[0]
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": place_data["latitude"],
                        "longitude": place_data["longitude"],
                        "current": "temperature_2m,wind_speed_10m",
                        "daily": "temperature_2m_max,temperature_2m_min",
                        "forecast_days": 1,
                        "timezone": "auto",
                    },
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError:
            logger.warning("Weather forecast request failed for %s", normalized_place, exc_info=True)
            return {
                "ok": False,
                "summary": f"I found {place_data.get('name', normalized_place)}, but free forecast data is temporarily unavailable.",
                "raw": None,
            }

        current = data.get("current", {})
        daily = data.get("daily", {})
        summary = (
            f"The current weather in {place_data['name']}, {place_data.get('country', '')} is "
            f"{current.get('temperature_2m')}C with winds around {current.get('wind_speed_10m')} km/h. "
            f"Today's range is {daily.get('temperature_2m_min', [None])[0]}C to {daily.get('temperature_2m_max', [None])[0]}C."
        )
        return {"ok": True, "summary": summary, "raw": data}

    async def get_news(self, topic: str) -> dict[str, object]:
        normalized_topic = topic.strip() or "technology"
        return {
            "ok": False,
            "summary": f"Live news for {normalized_topic} is disabled in local-only mode.",
            "headlines": [],
        }

    def spotify_status(self) -> dict[str, object]:
        if platform.system() != "Darwin":
            return {
                "enabled": True,
                "available": False,
                "running": False,
                "player_state": "unsupported",
                "track": None,
                "artist": None,
                "album": None,
                "position_seconds": None,
                "message": "Spotify status is currently implemented only on macOS.",
            }

        script = """
        if application "Spotify" is running then
          tell application "Spotify"
            set trackName to ""
            set trackArtist to ""
            set trackAlbum to ""
            set positionSeconds to 0
            set playerStateText to (player state as string)
            if playerStateText is not "stopped" then
              set trackName to name of current track
              set trackArtist to artist of current track
              set trackAlbum to album of current track
              set positionSeconds to player position
            end if
            return "true" & linefeed & playerStateText & linefeed & trackName & linefeed & trackArtist & linefeed & trackAlbum & linefeed & (positionSeconds as text)
          end tell
        else
          return "false" & linefeed & "not_running" & linefeed & "" & linefeed & "" & linefeed & "" & linefeed & ""
        end if
        """

        try:
            completed = subprocess.run(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
                text=True,
            )
            parts = completed.stdout.splitlines()
            while len(parts) < 6:
                parts.append("")
            running = parts[0].strip().lower() == "true"
            position_text = parts[5].strip()
            payload = {
                "running": running,
                "player_state": parts[1].strip() or ("not_running" if not running else "unknown"),
                "track": parts[2].strip() or None,
                "artist": parts[3].strip() or None,
                "album": parts[4].strip() or None,
                "position_seconds": float(position_text) if position_text else None,
            }
        except subprocess.CalledProcessError as exc:
            return {
                "enabled": True,
                "available": False,
                "running": False,
                "player_state": "error",
                "track": None,
                "artist": None,
                "album": None,
                "position_seconds": None,
                "message": exc.stderr.strip() or str(exc),
            }
        except ValueError as exc:
            return {
                "enabled": True,
                "available": False,
                "running": False,
                "player_state": "error",
                "track": None,
                "artist": None,
                "album": None,
                "position_seconds": None,
                "message": f"Unexpected Spotify script output: {exc}",
            }

        payload["enabled"] = True
        payload["available"] = True
        payload["message"] = "Spotify is running." if payload.get("running") else "Spotify is not running."
        return payload

    async def search_web(self, query: str) -> dict[str, object]:
        cleaned = query.strip()
        if not cleaned:
            return {"ok": False, "summary": "I need a search query first.", "results": []}
        google_url = f"https://www.google.com/search?q={quote_plus(cleaned)}"
        return {
            "ok": True,
            "summary": f"Prepared a local browser search for {cleaned}.",
            "results": [],
            "google_url": google_url,
        }

    def browser_context(self) -> dict[str, object]:
        if platform.system() != "Darwin":
            return {
                "ok": False,
                "app": None,
                "url": None,
                "title": None,
                "message": "Browser context reading is currently implemented only on macOS.",
            }

        script = """
        tell application "System Events"
          set frontApp to name of first application process whose frontmost is true
        end tell

        if frontApp is "Safari" then
          tell application "Safari"
            if (count of windows) is 0 then
              return "{\"ok\":false,\"app\":\"Safari\",\"url\":null,\"title\":null,\"message\":\"Safari has no open windows.\"}"
            end if
            set currentTab to current tab of front window
            return "{\"ok\":true,\"app\":\"Safari\",\"url\":\"" & (URL of currentTab) & "\",\"title\":\"" & my escape_json(name of currentTab) & "\"}"
          end tell
        else if frontApp is "Google Chrome" then
          tell application "Google Chrome"
            if (count of windows) is 0 then
              return "{\"ok\":false,\"app\":\"Google Chrome\",\"url\":null,\"title\":null,\"message\":\"Chrome has no open windows.\"}"
            end if
            set currentTab to active tab of front window
            return "{\"ok\":true,\"app\":\"Google Chrome\",\"url\":\"" & (URL of currentTab) & "\",\"title\":\"" & my escape_json(title of currentTab) & "\"}"
          end tell
        else if frontApp is "Arc" then
          tell application "Arc"
            if (count of windows) is 0 then
              return "{\"ok\":false,\"app\":\"Arc\",\"url\":null,\"title\":null,\"message\":\"Arc has no open windows.\"}"
            end if
            set currentTab to active tab of front window
            return "{\"ok\":true,\"app\":\"Arc\",\"url\":\"" & (URL of currentTab) & "\",\"title\":\"" & my escape_json(title of currentTab) & "\"}"
          end tell
        else
          return "{\"ok\":false,\"app\":\"" & my escape_json(frontApp) & "\",\"url\":null,\"title\":null,\"message\":\"The frontmost app is not a supported browser.\"}"
        end if

        on escape_json(inputText)
          set outputText to inputText
          set outputText to my replace_text("\\\\", "\\\\\\\\", outputText)
          set outputText to my replace_text("\"", "\\\\\"", outputText)
          return outputText
        end escape_json

        on replace_text(find, replace, subject)
          set AppleScript's text item delimiters to find
          set subjectItems to every text item of subject
          set AppleScript's text item delimiters to replace
          set newSubject to subjectItems as text
          set AppleScript's text item delimiters to ""
          return newSubject
        end replace_text
        """

        try:
            completed = subprocess.run(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout.strip())
            payload.setdefault("message", "Browser context retrieved.")
            return payload
        except subprocess.CalledProcessError as exc:
            return {"ok": False, "app": None, "url": None, "title": None, "message": exc.stderr.strip() or str(exc)}
        except json.JSONDecodeError as exc:
            return {"ok": False, "app": None, "url": None, "title": None, "message": f"Unexpected browser output: {exc}"}

    def active_application(self) -> dict[str, object]:
        if platform.system() != "Darwin":
            return {"ok": False, "app": None, "message": "Active application detection is currently implemented only on macOS."}
        script = """
        tell application "System Events"
          set frontApp to name of first application process whose frontmost is true
        end tell
        return frontApp
        """
        try:
            completed = subprocess.run(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
                text=True,
            )
            app_name = completed.stdout.strip()
            return {"ok": True, "app": app_name, "message": f"The active application is {app_name}."}
        except subprocess.CalledProcessError as exc:
            return {"ok": False, "app": None, "message": exc.stderr.strip() or str(exc)}

    async def summarize_current_page(self, context: dict[str, object] | None = None) -> dict[str, object]:
        context = context or self.browser_context()
        if not context.get("ok") or not context.get("url"):
            return {
                "ok": False,
                "summary": context.get("message", "I could not read the current page."),
                "context": context,
            }

        url = str(context["url"])
        domain = self._page_domain(url)
        page_name = str(context.get("title") or url)
        summary = (
            f"I can see {page_name} on {domain or url}. "
            "Live page-content fetching is disabled in local-only mode."
        )

        return {
            "ok": True,
            "summary": summary,
            "context": context,
            "mode": memory_service.get_power_mode(),
        }

    def system_report(self) -> dict[str, object]:
        power_mode = memory_service.get_power_mode()
        wake_word = wake_word_service.status()
        active_app = self.active_application()
        browser = self.browser_context()
        spotify = self.spotify_status()
        suggestions = self._contextual_suggestions(active_app, browser, spotify)

        lines = [
            f"Status report at {datetime.now().astimezone().strftime('%I:%M %p')}.",
            f"Power mode is {power_mode}.",
            f"Wake word is {'ready' if wake_word.get('effective_enabled') else 'paused'}.",
            str(active_app.get("message", "Active app is unavailable.")),
        ]
        if browser.get("ok"):
            lines.append(f"Current page: {browser.get('title') or browser.get('url')}.")
        if spotify.get("running"):
            track = spotify.get("track") or "Unknown track"
            artist = spotify.get("artist") or "Unknown artist"
            lines.append(f"Spotify is {spotify.get('player_state')} with {track} by {artist}.")
        else:
            lines.append("Spotify is not running.")

        return {
            "ok": True,
            "power_mode": power_mode,
            "wake_word": wake_word,
            "active_app": active_app,
            "browser": browser,
            "spotify": spotify,
            "suggestions": suggestions,
            "summary": " ".join(lines),
        }

    async def daily_briefing(self) -> dict[str, object]:
        power_mode = memory_service.get_power_mode()
        active_app = self.active_application()
        weather = await self._safe_weather(str(memory_service.get_preference("location", "Muscat") or "Muscat"))
        news = await self._safe_news("technology")
        spotify = self.spotify_status()
        reminders = reminder_service.summarize_active()
        calendar = productivity_service.upcoming_calendar_events(days=2, limit=3)

        lines = [
            f"Power mode is {power_mode}.",
            str(active_app.get("message", "Active application context is unavailable.")),
            str(weather.get("summary", "Weather is unavailable.")),
            str(news.get("summary", "News is unavailable.")),
            reminders,
            str(calendar.get("message", "Calendar is unavailable.")),
        ]
        if spotify.get("running"):
            lines.append(
                f"Spotify is {spotify.get('player_state')} with {spotify.get('track') or 'Unknown track'} by {spotify.get('artist') or 'Unknown artist'}."
            )

        return {
            "ok": True,
            "summary": " ".join(lines),
            "power_mode": power_mode,
            "active_app": active_app,
            "weather": weather,
            "news": news,
            "reminders": reminders,
            "calendar": calendar,
            "spotify": spotify,
        }

    async def operator_briefing(self) -> dict[str, object]:
        power_mode = memory_service.get_power_mode()
        active_app = self.active_application()
        browser = self.browser_context()
        page_summary = await self._safe_page_summary(context=browser)
        weather = await self._safe_weather(str(memory_service.get_preference("location", "Muscat") or "Muscat"))
        news = await self._safe_news("technology")
        spotify = self.spotify_status()
        reminders = reminder_service.summarize_active()
        calendar = productivity_service.upcoming_calendar_events(days=2, limit=3)
        suggestions = self._contextual_suggestions(active_app, browser, spotify, str(page_summary.get("summary") or ""))

        summary_lines = [
            f"Operator briefing at {datetime.now().astimezone().strftime('%I:%M %p')}.",
            f"Power mode is {power_mode}.",
            str(active_app.get("message", "Active application context is unavailable.")),
        ]

        if browser.get("ok"):
            summary_lines.append(
                f"Browser focus: {browser.get('title') or browser.get('url')}."
            )
        if page_summary.get("summary"):
            summary_lines.append(str(page_summary["summary"]))
        if spotify.get("running"):
            summary_lines.append(
                f"Spotify is {spotify.get('player_state')} with {spotify.get('track') or 'Unknown track'} by {spotify.get('artist') or 'Unknown artist'}."
            )
        else:
            summary_lines.append(str(spotify.get("message", "Spotify is not running.")))

        summary_lines.append(reminders)
        summary_lines.append(str(calendar.get("message", "Calendar is unavailable.")))
        summary_lines.append(str(weather.get("summary", "Weather is unavailable.")))
        summary_lines.append(str(news.get("summary", "News is unavailable.")))
        summary_lines.append("Suggested next actions: " + " ".join(suggestions))

        return {
            "ok": True,
            "summary": " ".join(summary_lines),
            "power_mode": power_mode,
            "active_app": active_app,
            "browser": browser,
            "page_summary": page_summary,
            "weather": weather,
            "news": news,
            "reminders": reminders,
            "calendar": calendar,
            "spotify": spotify,
            "suggestions": suggestions,
        }

integration_service = IntegrationService()
