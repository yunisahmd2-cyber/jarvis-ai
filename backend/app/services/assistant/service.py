from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote_plus

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.services.actions.service import SAFE_SITE_SHORTCUTS, action_service
from backend.app.services.confirmations.service import confirmation_service
from backend.app.services.integrations.service import integration_service
from backend.app.services.llm.provider import llm_provider_service
from backend.app.services.memory.service import memory_service
from backend.app.services.reminders.service import reminder_service
from backend.app.services.session.service import session_service
from backend.app.services.tts.service import tts_service
from backend.app.services.timers.service import timer_service
from backend.app.services.vision.service import vision_service
from backend.app.services.voice.wakeword import wake_word_service


logger = get_logger(__name__)


class AssistantService:
    _AMBIGUOUS_APP_TARGETS = {
        "",
        "it",
        "that",
        "this",
        "there",
        "something",
        "anything",
        "some app",
        "an app",
        "the app",
        "whatever",
    }

    def _strip_prefix(self, text: str, prefix: str) -> str:
        if text.lower().startswith(prefix.lower()):
            return text[len(prefix):].strip()
        return text.strip()

    def _normalize_app_target(self, text: str) -> str:
        normalized = text.strip()
        normalized = re.sub(r"^my\s+", "", normalized, flags=re.IGNORECASE)
        for article in ("the ", "a ", "an "):
            if normalized.lower().startswith(article):
                normalized = normalized[len(article):].strip()
                break
        normalized = re.sub(r"\s+(app|application|desktop app)$", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+(for me|please|right now|now)$", "", normalized, flags=re.IGNORECASE)
        normalized = normalized.strip(" ,.?!")
        if normalized.lower() in {"browser", "default browser", "web browser", "internet browser"}:
            return self._default_browser_target()
        return action_service._resolve_allowed_app(normalized) or normalized

    def _is_ambiguous_app_target(self, target: str | None) -> bool:
        normalized = (target or "").strip().lower().strip(" ,.?!")
        return normalized in self._AMBIGUOUS_APP_TARGETS

    def _interaction_context_key(self, session_id: str) -> str:
        return f"session:{session_id}:interaction_context"

    def _load_interaction_context(self, session_id: str) -> dict[str, Any]:
        item = memory_service.get_memory(self._interaction_context_key(session_id))
        if not item:
            return {}
        try:
            payload = json.loads(str(item.get("value") or "{}"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _store_interaction_context(self, session_id: str, **updates: Any) -> None:
        context = self._load_interaction_context(session_id)
        for key, value in updates.items():
            if value is None or value == "":
                continue
            context[key] = value
        context["updated_at"] = datetime.now(UTC).isoformat()
        # Keep this deliberately small: one short-lived session context blob, not long-term memory.
        allowed_keys = {
            "updated_at",
            "recent_action",
            "recent_app_target",
            "previous_app_target",
            "active_app_target",
            "recent_search_query",
            "recent_search_summary",
            "recent_page_title",
            "recent_page_url",
            "last_command",
            "last_action_text",
            "last_action_status",
            "pending_clarification",
        }
        trimmed = {key: context[key] for key in allowed_keys if key in context}
        memory_service.store_memory(self._interaction_context_key(session_id), json.dumps(trimmed), "session")

    def _set_pending_clarification(self, session_id: str, payload: dict[str, Any]) -> None:
        self._store_interaction_context(session_id, pending_clarification=payload)

    def _clear_pending_clarification(self, session_id: str) -> None:
        context = self._load_interaction_context(session_id)
        if "pending_clarification" not in context:
            return
        context.pop("pending_clarification", None)
        context["updated_at"] = datetime.now(UTC).isoformat()
        memory_service.store_memory(self._interaction_context_key(session_id), json.dumps(context), "session")

    def _remember_app_context(self, session_id: str, target: str | None, *, action: str = "context") -> None:
        app_name = action_service._resolve_allowed_app(target) or (target or "").strip()
        if not app_name or app_name.lower() == "jarvis":
            return
        context = self._load_interaction_context(session_id)
        previous = context.get("recent_app_target")
        updates: dict[str, Any] = {"recent_action": action, "recent_app_target": app_name, "active_app_target": app_name}
        if previous and previous != app_name:
            updates["previous_app_target"] = previous
        self._store_interaction_context(session_id, **updates)

    def _remember_search_context(self, session_id: str, query: str, summary: str | None = None) -> None:
        self._store_interaction_context(
            session_id,
            recent_action="search",
            recent_search_query=query.strip(),
            recent_search_summary=(summary or "").strip()[:500],
        )

    def _remember_user_command(self, session_id: str, command: str) -> None:
        if command.strip():
            self._store_interaction_context(session_id, last_command=command.strip())

    def _last_user_command(self, session_id: str) -> str | None:
        context = self._load_interaction_context(session_id)
        command = str(context.get("last_command") or "").strip()
        return command or None

    def _last_action_summary(self, session_id: str) -> tuple[str | None, str | None]:
        context = self._load_interaction_context(session_id)
        text = str(context.get("last_action_text") or "").strip() or None
        status = str(context.get("last_action_status") or "").strip() or None
        return text, status

    def _resolve_recent_app_reference(self, session_id: str) -> str | None:
        context = self._load_interaction_context(session_id)
        for key in ("active_app_target", "recent_app_target"):
            app_name = str(context.get(key) or "").strip()
            if app_name and app_name.lower() != "jarvis":
                return action_service._resolve_allowed_app(app_name) or app_name
        preference = memory_service.get_preference("recent_app_target")
        return action_service._resolve_allowed_app(preference) or preference

    def _default_browser_target(self) -> str:
        bundle_map = {
            "com.apple.safari": "Safari",
            "com.google.chrome": "Google Chrome",
            "company.thebrowser.browser": "Arc",
        }
        try:
            proc = subprocess.run(
                ["osascript", "-e", 'id of app (path to default application for URL "https://example.com")'],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            app_name = bundle_map.get(proc.stdout.strip().lower())
            if app_name and action_service._resolve_allowed_app(app_name):
                return app_name
        except Exception:
            pass
        for app_name in ("Safari", "Google Chrome", "Arc"):
            if action_service._resolve_allowed_app(app_name):
                return app_name
        return "Safari"

    def _looks_like_explicit_website_target(self, target: str) -> bool:
        lowered = target.strip().lower()
        if lowered in SAFE_SITE_SHORTCUTS:
            return True
        if lowered.startswith(("http://", "https://", "www.")):
            return True
        return "." in lowered and " " not in lowered

    def _parse_time_fragment(self, fragment: str) -> tuple[int, int] | None:
        value = fragment.strip().lower().replace(".", "")
        for fmt in ("%I:%M %p", "%I %p", "%H:%M", "%H"):
            try:
                parsed = datetime.strptime(value, fmt)
                return parsed.hour, parsed.minute
            except ValueError:
                continue
        return None

    def _parse_date_fragment(self, fragment: str) -> datetime | None:
        now = datetime.now().astimezone()
        value = fragment.strip().lower()
        if value.startswith("on "):
            value = value[3:].strip()
        if value == "today":
            return now
        if value == "tomorrow":
            return now + timedelta(days=1)
        weekdays = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        if value in weekdays:
            delta = (weekdays[value] - now.weekday()) % 7
            delta = 7 if delta == 0 else delta
            return now + timedelta(days=delta)
        for fmt in ("%Y-%m-%d", "%B %d %Y", "%B %d, %Y", "%b %d %Y", "%b %d, %Y", "%B %d", "%b %d"):
            try:
                parsed = datetime.strptime(value, fmt)
                year = parsed.year if "%Y" in fmt else now.year
                result = now.replace(year=year, month=parsed.month, day=parsed.day)
                if result < now and "%Y" not in fmt:
                    result = result.replace(year=year + 1)
                return result
            except ValueError:
                continue
        return None

    def _next_recurring_start(self, recurrence: str, hour: int, minute: int) -> datetime:
        now = datetime.now().astimezone()
        start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if recurrence == "FREQ=DAILY;INTERVAL=1":
            if start <= now:
                start += timedelta(days=1)
            return start
        if recurrence == "FREQ=WEEKLY;INTERVAL=1":
            if start <= now:
                start += timedelta(days=7)
            return start
        if recurrence == "FREQ=MONTHLY;INTERVAL=1":
            if start > now:
                return start
            month = start.month + 1
            year = start.year
            if month > 12:
                month = 1
                year += 1
            day = min(start.day, 28)
            return start.replace(year=year, month=month, day=day)
        return start

    def _parse_calendar_command(self, text: str) -> dict[str, Any] | None:
        lower = text.lower().strip()
        if lower in {
            "what's on my calendar",
            "what is on my calendar",
            "show my calendar",
            "show calendar",
            "upcoming calendar",
            "calendar today",
            "calendar tomorrow",
        }:
            params = {"days": 1 if lower == "calendar today" else 2 if lower == "calendar tomorrow" else 7, "limit": 8}
            return action_service.preview("list_calendar_events", "calendar", params)

        recurring_pattern = re.compile(
            r"^(?:add to calendar|create calendar event|create calendar|schedule)\s+(.+?)\s+(every day|daily|every week|weekly|every month|monthly)\s+at\s+(.+?)(?:\s+for\s+(\d+)\s+minutes?)?$",
            flags=re.IGNORECASE,
        )
        match = recurring_pattern.match(text.strip())
        if match:
            title = match.group(1).strip(" ,.")
            recurrence_label = match.group(2).lower()
            time_text = match.group(3).strip()
            duration = int(match.group(4) or "30")
            parsed_time = self._parse_time_fragment(time_text)
            if not parsed_time:
                return None
            hour, minute = parsed_time
            recurrence_map = {
                "every day": "FREQ=DAILY;INTERVAL=1",
                "daily": "FREQ=DAILY;INTERVAL=1",
                "every week": "FREQ=WEEKLY;INTERVAL=1",
                "weekly": "FREQ=WEEKLY;INTERVAL=1",
                "every month": "FREQ=MONTHLY;INTERVAL=1",
                "monthly": "FREQ=MONTHLY;INTERVAL=1",
            }
            recurrence = recurrence_map[recurrence_label]
            starts_at = self._next_recurring_start(recurrence, hour, minute)
            ends_at = starts_at + timedelta(minutes=duration)
            return action_service.preview(
                "create_calendar_event",
                title,
                {
                    "title": title,
                    "starts_at": starts_at.isoformat(),
                    "ends_at": ends_at.isoformat(),
                    "recurrence": recurrence,
                },
            )

        single_pattern = re.compile(
            r"^(?:add to calendar|create calendar event|create calendar|schedule)\s+(.+?)\s+(today|tomorrow|on\s+.+?)\s+at\s+(.+?)(?:\s+for\s+(\d+)\s+minutes?)?$",
            flags=re.IGNORECASE,
        )
        match = single_pattern.match(text.strip())
        if not match:
            return None
        title = match.group(1).strip(" ,.")
        date_text = match.group(2).strip()
        time_text = match.group(3).strip()
        duration = int(match.group(4) or "30")
        base_date = self._parse_date_fragment(date_text)
        parsed_time = self._parse_time_fragment(time_text)
        if not base_date or not parsed_time:
            return None
        hour, minute = parsed_time
        starts_at = base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        ends_at = starts_at + timedelta(minutes=duration)
        return action_service.preview(
            "create_calendar_event",
            title,
            {
                "title": title,
                "starts_at": starts_at.isoformat(),
                "ends_at": ends_at.isoformat(),
            },
        )

    def _parse_mail_draft_command(self, text: str) -> dict[str, Any] | None:
        pattern = re.compile(
            r"^(?:draft email|create email draft)\s+to\s+(\S+@\S+?)(?:\s+subject\s+(.+?))?(?:\s+body\s+(.+))?$",
            flags=re.IGNORECASE,
        )
        match = pattern.match(text.strip())
        if match:
            recipient = match.group(1).strip(" ,.")
            subject = (match.group(2) or "").strip(" ,.")
            body = (match.group(3) or "").strip()
            return action_service.preview(
                "create_mail_draft",
                recipient,
                {
                    "to": recipient,
                    "subject": subject or "Draft from Jarvis",
                    "body": body or "",
                    "cc": [],
                },
            )

        alt_pattern = re.compile(
            r"^(?:draft email|create email draft)\s+to\s+(\S+@\S+?)\s+about\s+(.+)$",
            flags=re.IGNORECASE,
        )
        alt_match = alt_pattern.match(text.strip())
        if not alt_match:
            return None
        recipient = alt_match.group(1).strip(" ,.")
        subject = alt_match.group(2).strip(" ,.")
        return action_service.preview(
            "create_mail_draft",
            recipient,
            {
                "to": recipient,
                "subject": subject,
                "body": "",
                "cc": [],
            },
        )

    def _parse_timer_duration(self, text: str) -> tuple[int, str] | None:
        match = re.search(r"(\d+)\s*(second|seconds|minute|minutes|hour|hours)", text.lower())
        if not match:
            return None
        amount = int(match.group(1))
        unit = match.group(2)
        seconds = amount * 3600 if unit.startswith("hour") else amount * 60 if unit.startswith("minute") else amount
        label = re.sub(r"^(?:set|start)\s+(?:a\s+)?timer(?:\s+for)?\s+", "", text, flags=re.IGNORECASE).strip(" .")
        label = re.sub(r"\b\d+\s*(?:second|seconds|minute|minutes|hour|hours)\b", "", label, flags=re.IGNORECASE).strip(" .")
        return seconds, label or "timer"

    def _format_note_list(self) -> str:
        notes = memory_service.list_by_category("note", limit=5)
        if not notes:
            return "You have no saved notes."
        lines = [f"{index + 1}. {str(item['value'])[:140]}" for index, item in enumerate(notes)]
        return "Your latest notes: " + " ".join(lines)

    def build_system_prompt(self) -> str:
        preferences = memory_service.list_preferences()
        preference_lines = [f"{item['key']}: {item['value']}" for item in preferences[:10]]
        settings = get_settings()
        power_mode = memory_service.get_power_mode()
        response_style = (memory_service.get_preference("response_style", "normal") or "normal").strip().lower()
        tone_style = (memory_service.get_preference("tone_style", "professional") or "professional").strip().lower()
        current_time = datetime.now().astimezone().strftime("%A, %B %d %Y %I:%M %p")
        if power_mode == "basic":
            preference_lines = preference_lines[:5]
            return (
                "You are JARVIS, a concise local macOS desktop assistant. "
                "Use short, direct replies. "
                "Do not claim desktop actions succeeded unless the action layer verified them. "
                "Ask one brief clarification question when the request is unclear. "
                "Never use cloud-only claims or fake features. "
                f"Local time is {current_time}. "
                f"Power mode is basic. Wake word is {settings.wake_word}. "
                f"Response style is {response_style}; tone is {tone_style}. "
                f"Preference hints: {'; '.join(preference_lines) if preference_lines else 'none'}."
            )
        return (
            "You are JARVIS, a polished voice-first desktop assistant for macOS. "
            "Sound concise, competent, calm, and controlled. "
            "Carry the understated elegance and composure people associate with a high-end cinematic systems assistant. "
            "Use light butler-style polish without becoming theatrical. "
            "Be warm, precise, and quietly confident. "
            "If you use an honorific, use sir only. Never use madam or ma'am. "
            "Avoid rambling. Prefer short, direct responses unless the user asks for depth. "
            "Use the configured assistant model without pretending cloud responses are local. "
            "Treat desktop actions like real system actions: be precise, safe, and confirmation-aware. "
            "Never claim an action succeeded unless the action layer verified or clearly confirmed it. "
            "If a task clearly needs several safe steps, plan them briefly and execute them in order. "
            "Keep confirmations minimal and explicit. "
            "When you can answer quickly, do so with minimal preamble. "
            f"The current local time is {current_time}. "
            f"Current power mode is {power_mode}. "
            f"The configured wake word is {settings.wake_word}. "
            f"Response style is {response_style}; tone is {tone_style}. "
            f"Current preference hints: {'; '.join(preference_lines) if preference_lines else 'none stored yet'}."
        )

    def infer_follow_up(self, text: str) -> bool:
        clean = text.strip().lower()
        return clean.endswith("?") or clean.startswith(("what ", "which ", "where ", "when ", "who ", "could you ", "would you "))

    def _get_session_vision_context(self, session_id: str) -> dict[str, Any] | None:
        item = memory_service.get_memory(f"session:{session_id}:last_vision_context")
        if not item:
            return None
        value = str(item.get("value") or "").strip()
        if not value:
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _vision_context_excerpt(self, payload: dict[str, Any] | None, *, max_chars: int = 300) -> str | None:
        if not payload:
            return None
        summary = str(payload.get("summary") or "").strip()
        ocr_text = str(payload.get("ocr_text") or "").strip()
        metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
        captured_at = str(metadata.get("captured_at") or "").strip()
        if ocr_text:
            ocr_text = ocr_text[:max_chars] + ("…" if len(ocr_text) > max_chars else "")
        if summary and ocr_text:
            return f"{summary} Detected text: {ocr_text}"
        if summary:
            return summary
        if ocr_text:
            return f"Last captured screen text: {ocr_text}"
        if captured_at:
            return f"I have a prior screen capture context from {captured_at}."
        return None

    def _polish_reply(self, text: str) -> str:
        cleaned = " ".join(text.split())
        if not cleaned:
            return "Ready."
        replacements = {
            "I am": "I'm",
            "I will": "I'll",
            "do not": "don't",
            "Madam": "Sir",
            "madam": "sir",
            "Ma'am": "Sir",
            "ma'am": "sir",
        }
        for src, dst in replacements.items():
            cleaned = cleaned.replace(src, dst)
        mode = memory_service.get_power_mode()
        if mode == "basic" and cleaned.count(".") >= 2:
            sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
            if sentences:
                cleaned = ". ".join(sentences[:2]).strip()
                if not cleaned.endswith((".", "!", "?")):
                    cleaned += "."
        return cleaned

    async def respond(
        self,
        *,
        text: str,
        session_id: str | None,
        include_audio: bool = False,
        screenshot_base64: str | None = None,
        include_screen_context: bool = False,
    ) -> dict[str, Any]:
        request_started = time.perf_counter()
        route_started = request_started
        session = session_service.ensure_session(session_id)
        active_session_id = str(session["session_id"])
        user_text = self._normalize_user_text(text)
        previous_command = self._last_user_command(active_session_id)
        memory_service.add_message(active_session_id, "user", user_text)

        screen_context = None
        if include_screen_context and screenshot_base64:
            screen_context = vision_service.analyze_screenshot(screenshot_base64)

        routed = await self._route_command(user_text, active_session_id, screen_context)
        route_ms = (time.perf_counter() - route_started) * 1000
        llm_ms = 0.0
        if routed is None:
            history = memory_service.get_recent_messages(active_session_id)
            prompt = user_text
            if screen_context:
                prompt = f"{user_text}\n\nScreen context:\n{screen_context['summary']}"
            llm_started = time.perf_counter()
            llm_result = await llm_provider_service.generate(
                prompt=prompt,
                system_prompt=self.build_system_prompt(),
                history=history,
                mode=memory_service.get_power_mode(),
            )
            reply_text = llm_result.text
            llm_ms = (time.perf_counter() - llm_started) * 1000
            metadata = {"source": llm_result.source}
            if llm_result.fallback_reason:
                metadata["fallback_reason"] = llm_result.fallback_reason
            response: dict[str, Any] = {
                "session_id": active_session_id,
                "text": self._polish_reply(reply_text),
                "follow_up": self.infer_follow_up(reply_text),
                "confirmation_required": False,
                "confirmation_id": None,
                "action_preview": None,
                "memory_updated": False,
                "metadata": metadata,
            }
        else:
            response = routed

        audio_task = None
        tts_ms = 0.0
        if include_audio and response.get("text"):
            tts_started = time.perf_counter()
            audio_task = asyncio.create_task(tts_service.synthesize(str(response["text"])))

        await asyncio.to_thread(
            self._persist_assistant_response,
            active_session_id,
            str(response["text"]),
        )

        if audio_task is not None:
            audio = await audio_task
            tts_ms = (time.perf_counter() - tts_started) * 1000
            response["audio_url"] = audio["audio_url"]
            response.setdefault("metadata", {})["tts_provider"] = audio["provider"]
        else:
            response["audio_url"] = None

        if screen_context:
            response.setdefault("metadata", {})["screen_context"] = screen_context["summary"]

        response.setdefault("metadata", {})["previous_command"] = previous_command
        if response.get("metadata", {}).get("source") != "history":
            self._remember_user_command(active_session_id, user_text)
        total_ms = (time.perf_counter() - request_started) * 1000
        response.setdefault("metadata", {})["timing_ms"] = {
            "route": round(route_ms, 1),
            "llm": round(llm_ms, 1),
            "tts": round(tts_ms, 1),
            "total": round(total_ms, 1),
        }
        logger.info(
            "timing stage=assistant source=%s route_ms=%.1f llm_ms=%.1f tts_ms=%.1f total_ms=%.1f audio=%s",
            response.get("metadata", {}).get("source"),
            route_ms,
            llm_ms,
            tts_ms,
            total_ms,
            bool(response.get("audio_url")),
        )
        return response

    async def _route_command(
        self,
        text: str,
        session_id: str,
        screen_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        pending = await self._resolve_pending_clarification(text, session_id)
        if pending is not None:
            return pending
        chained = await self._run_task_chain(text, session_id, screen_context)
        if chained is not None:
            return chained
        return await self._route_single_command(text, session_id, None, screen_context)

    async def _resolve_pending_clarification(self, text: str, session_id: str) -> dict[str, Any] | None:
        context = self._load_interaction_context(session_id)
        pending = context.get("pending_clarification")
        if not isinstance(pending, dict):
            return None

        pending_type = str(pending.get("type") or "")
        answer = text.strip(" ,.?!")
        lower = answer.lower()
        if not answer:
            return None
        if lower in {"cancel", "never mind", "nevermind", "forget it", "stop"}:
            self._clear_pending_clarification(session_id)
            return self._simple_response(session_id, "Cancelled.", source="clarification")

        if pending_type == "open_target":
            if lower.startswith(("open ", "launch ")):
                answer = self._strip_prefix(answer, "open ") if lower.startswith("open ") else self._strip_prefix(answer, "launch ")
            target = self._normalize_app_target(answer)
            if self._is_ambiguous_app_target(target):
                self._set_pending_clarification(session_id, pending)
                return self._simple_response(session_id, "Which app or website should I open?", source="clarification")

            app_preview = action_service.preview("open_app", target)
            if app_preview["allowed"]:
                self._clear_pending_clarification(session_id)
                return self._confirmation_or_execute(session_id, app_preview)

            if self._looks_like_explicit_website_target(target):
                self._clear_pending_clarification(session_id)
                site_target = SAFE_SITE_SHORTCUTS.get(target.lower(), target)
                url_preview = action_service.preview("open_url", site_target)
                return self._confirmation_or_execute(session_id, url_preview)

            self._clear_pending_clarification(session_id)
            return self._simple_response(
                session_id,
                f"I can't open {target} as a trusted macOS app yet. If you meant a website, say open the website explicitly.",
                source="policy",
            )

        if pending_type == "reminder_time":
            title = str(pending.get("title") or "").strip()
            if not title:
                self._clear_pending_clarification(session_id)
                return None
            reminder = self._create_reminder_from_time_answer(session_id, title, answer)
            if reminder is not None:
                self._clear_pending_clarification(session_id)
                return reminder
            self._set_pending_clarification(session_id, pending)
            return self._simple_response(
                session_id,
                "I need a clear time, for example: in 20 minutes, tomorrow, tonight, or at 4 PM.",
                source="reminder",
            )

        if pending_type == "search_query":
            if lower in {"it", "that", "this", "something"}:
                self._set_pending_clarification(session_id, pending)
                return self._simple_response(session_id, "What should I search for?", source="search")
            self._clear_pending_clarification(session_id)
            search = await integration_service.search_web(answer)
            if search.get("ok"):
                self._remember_search_context(session_id, answer, str(search.get("summary") or ""))
            return self._simple_response(session_id, str(search["summary"]), source="search")

        return None

    async def _route_single_command(
        self,
        text: str,
        session_id: str,
        chain_context: dict[str, Any] | None,
        screen_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        lower = text.lower().strip()
        if lower.startswith("check "):
            return await self._route_single_command(self._strip_prefix(text, "check "), session_id, chain_context, screen_context)
        if lower.startswith("tell me "):
            return await self._route_single_command(self._strip_prefix(text, "tell me "), session_id, chain_context, screen_context)
        if lower in {"use basic mode", "switch to basic mode", "enable basic mode"}:
            memory_service.set_power_mode("basic")
            return self._simple_response(
                session_id,
                "Basic mode is active. I'll stay lighter and cooler. Wake word background listening remains paused in this mode.",
                source="mode",
            )
        if lower in {"use advanced mode", "switch to advanced mode", "enable advanced mode"}:
            memory_service.set_power_mode("advanced")
            return self._simple_response(session_id, "Advanced mode is active. I'll use deeper reasoning and richer context.", source="mode")
        if lower in {"what mode are you in", "current mode", "which mode are you using"}:
            return self._simple_response(session_id, f"I'm currently in {memory_service.get_power_mode()} mode.", source="mode")
        if lower in {"enable wake word", "turn on wake word", "wake word on"}:
            status = wake_word_service.set_enabled(True)
            return self._simple_response(session_id, str(status["reason"]), source="wake_word")
        if lower in {"disable wake word", "turn off wake word", "wake word off"}:
            status = wake_word_service.set_enabled(False)
            return self._simple_response(session_id, "Wake word is disabled. Manual activation is still available.", source="wake_word")
        if lower in {"wake word status", "is wake word enabled", "wake word on or off"}:
            status = wake_word_service.status()
            state_text = "enabled" if status["effective_enabled"] else "paused"
            return self._simple_response(session_id, f"Wake word is {state_text}. {status['reason']}", source="wake_word")
        if self._is_system_status_query(lower):
            status = integration_service.system_status()
            text = " ".join(str(status["summary"]).split())
            if "cpu" in lower and any(term in lower for term in ("load", "usage", "using")):
                cpu_data = status.get("cpu", {})
                if isinstance(cpu_data, dict):
                    usage = cpu_data.get("usage_percent")
                    if usage is not None:
                        text = f"CPU usage is {usage}%."
                    else:
                        text = f"CPU load average is {cpu_data.get('load_1m')}."
            elif any(term in lower for term in ("ram", "memory")) and any(term in lower for term in ("usage", "using", "left", "free")):
                memory_data = status.get("memory", {})
                if isinstance(memory_data, dict):
                    text = f"RAM usage is {memory_data.get('used_percent')}%."
            elif "battery" in lower:
                battery_data = status.get("battery")
                if isinstance(battery_data, dict):
                    state = "charging" if battery_data.get("charging") else "not charging"
                    text = f"Battery is at {battery_data.get('percent')}% and {state}."
                else:
                    text = "Battery status is unavailable on this Mac."
            elif any(term in lower for term in ("storage", "disk")):
                disk_data = status.get("disk", {})
                if isinstance(disk_data, dict):
                    free_gb = round(float(disk_data.get("free_bytes") or 0) / (1024**3), 1)
                    text = f"Disk usage is {disk_data.get('used_percent')}%, with about {free_gb} GB free."
            elif "using my mac" in lower or "what's using" in lower or "what is using" in lower:
                processes = status.get("top_processes", [])
                if isinstance(processes, list) and processes:
                    parts = [
                        f"{item.get('name')} at {item.get('cpu_percent')}% CPU"
                        for item in processes[:3]
                        if isinstance(item, dict)
                    ]
                    text = "Top visible processes: " + "; ".join(parts) + "."
                else:
                    text = "I couldn't identify top processes from this check."
            return {
                "session_id": session_id,
                "text": text,
                "follow_up": False,
                "confirmation_required": False,
                "confirmation_id": None,
                "action_preview": None,
                "memory_updated": False,
                "metadata": {"source": "system_status", "status": status.get("status"), "system_status": status},
            }
        if lower in {
            "status report",
            "system report",
            "system check",
            "jarvis report",
            "system sweep",
            "full systems check",
            "systems check",
        }:
            report = integration_service.system_report()
            return self._simple_response(session_id, str(report["summary"]), source="system")
        if lower in {
            "brief me",
            "daily briefing",
            "morning briefing",
            "jarvis briefing",
            "give me a briefing",
            "mission brief",
            "morning report",
        }:
            report = await integration_service.daily_briefing()
            return self._simple_response(session_id, str(report["summary"]), source="system")
        if lower in {
            "operator briefing",
            "mission control briefing",
            "give me the operator briefing",
            "speak the briefing",
            "inspect this",
            "operator overview",
            "mission status",
            "tactical overview",
            "threat scan",
        }:
            report = await integration_service.operator_briefing()
            return self._simple_response(session_id, str(report["summary"]), source="system")
        if lower in {
            "what can you control",
            "what can you access",
            "what can you access safely",
            "capability report",
            "system capabilities",
        }:
            report = integration_service.capability_report()
            return self._simple_response(session_id, str(report["summary"]), source="system")
        if lower.startswith("remember "):
            memory_service.store_memory(
                key=f"user_memory_{abs(hash(text))}",
                value=self._strip_prefix(text, "remember "),
                category="memory",
            )
            return self._simple_response(session_id, "I'll remember that.", memory_updated=True, source="memory")
        if lower.startswith("take a note:") or lower.startswith("take note:") or lower.startswith("note:"):
            payload = re.sub(r"^(?:take a note:|take note:|note:)\s*", "", text, flags=re.IGNORECASE).strip()
            if not payload:
                return self._simple_response(session_id, "What should I write down?", source="note")
            memory_service.store_memory(f"note_{datetime.now(UTC).timestamp()}", payload, "note")
            return self._simple_response(session_id, "Noted.", memory_updated=True, source="note")
        if lower in {"show my notes", "show notes", "list notes", "what are my notes"}:
            return self._simple_response(session_id, self._format_note_list(), source="note")
        if lower.startswith("delete note "):
            index_text = self._strip_prefix(text, "delete note ").strip()
            if not index_text.isdigit():
                return self._simple_response(session_id, "Say the note number to delete, for example: delete note 1.", source="note")
            notes = memory_service.list_by_category("note", limit=20)
            index = int(index_text) - 1
            if index < 0 or index >= len(notes):
                return self._simple_response(session_id, "I couldn't find that note number.", source="note")
            deleted = memory_service.delete_memory(str(notes[index]["key"]))
            return self._simple_response(session_id, "Note deleted." if deleted else "I couldn't delete that note.", source="note")
        if lower in {"delete that note", "delete my note", "remove that note"}:
            return self._simple_response(session_id, "Tell me the note number first, for example: delete note 1.", source="note")
        if lower.startswith("set personality "):
            value = self._strip_prefix(text, "set personality ")
            memory_service.store_memory("personality", value, "preference")
            return self._simple_response(session_id, f"Personality set to {value}.", memory_updated=True, source="preference")
        if lower.startswith("set voice "):
            value = self._strip_prefix(text, "set voice ")
            memory_service.store_memory("voice_mode", value, "preference")
            return self._simple_response(session_id, f"Voice mode set to {value}.", memory_updated=True, source="preference")
        if lower in {"be more concise", "use concise responses", "keep it short", "keep responses short"}:
            memory_service.store_memory("response_style", "concise", "preference")
            return self._simple_response(session_id, "I'll keep responses more concise.", memory_updated=True, source="preference")
        if lower in {"give more detail", "be more detailed", "use detailed responses", "explain more"}:
            memory_service.store_memory("response_style", "detailed", "preference")
            return self._simple_response(session_id, "I'll give more detail when useful.", memory_updated=True, source="preference")
        if lower in {"use normal responses", "normal response style", "be normal"}:
            memory_service.store_memory("response_style", "normal", "preference")
            return self._simple_response(session_id, "Normal response style is active.", memory_updated=True, source="preference")
        if lower in {"talk more casually", "be more casual", "use casual tone"}:
            memory_service.store_memory("tone_style", "casual", "preference")
            return self._simple_response(session_id, "I'll use a more casual tone.", memory_updated=True, source="preference")
        if lower in {"be more professional", "talk more professionally", "use professional tone"}:
            memory_service.store_memory("tone_style", "professional", "preference")
            return self._simple_response(session_id, "Professional tone is active.", memory_updated=True, source="preference")
        if lower in {"what did i just ask", "what was my last command", "what did i ask last"}:
            previous = self._last_user_command(session_id)
            if not previous:
                return self._simple_response(session_id, "I don't have a previous command in this session yet.", source="history")
            return self._simple_response(session_id, f"You just asked: {previous}.", source="history")
        if lower in {"what did you just do", "what was the last action", "what happened last"}:
            action_text, action_status = self._last_action_summary(session_id)
            if not action_text:
                return self._simple_response(session_id, "I haven't completed an action in this session yet.", source="history")
            status_text = f" Status: {action_status}." if action_status else ""
            return self._simple_response(session_id, f"Last action: {action_text}.{status_text}", source="history")
        if lower in {"repeat last command", "run that again", "do that again"}:
            previous = self._last_user_command(session_id)
            if not previous or previous.lower() in {"repeat last command", "run that again", "do that again"}:
                return self._simple_response(session_id, "I don't have a runnable previous command in this session yet.", source="history")
            action_text, action_status = self._last_action_summary(session_id)
            if action_status == "failed":
                return self._simple_response(
                    session_id,
                    f"The previous action failed, so I won't rerun it blindly. Last result: {action_text or 'unknown failure'}.",
                    source="history",
                )
            return await self._route_command(previous, session_id, screen_context)
        if lower.startswith("initiate protocol ") or lower.startswith("run protocol ") or lower.startswith("activate protocol "):
            prefix = "initiate protocol " if lower.startswith("initiate protocol ") else "run protocol " if lower.startswith("run protocol ") else "activate protocol "
            protocol_name = self._strip_prefix(lower, prefix).strip(" ,.")
            preview = action_service.preview("protocol_override", protocol_name)
            return self._confirmation_or_execute(session_id, preview)
        if lower.startswith("remind me "):
            reminder = self._parse_reminder(text, session_id)
            if reminder is not None:
                return reminder
            title = re.sub(r"^remind me(?:\s+to)?\s+", "", text, flags=re.IGNORECASE).strip(" ,.")
            if title:
                self._set_pending_clarification(session_id, {"type": "reminder_time", "title": title})
            return self._simple_response(
                session_id,
                "When should I remind you?",
                source="reminder",
            )
        if lower.startswith(("set a timer", "set timer", "start a timer", "start timer")):
            parsed_timer = self._parse_timer_duration(text)
            if parsed_timer is None:
                return self._simple_response(session_id, "How long should I set the timer for?", source="timer")
            seconds, label = parsed_timer
            timer = timer_service.create(session_id=session_id, seconds=seconds, label=label)
            due_at = datetime.fromisoformat(str(timer["due_at"])).astimezone()
            return self._simple_response(
                session_id,
                f"Timer set for {due_at.strftime('%I:%M %p')}.",
                source="timer",
            )
        if lower in {"cancel my timer", "cancel timer", "stop timer", "stop my timer"}:
            cancelled = timer_service.cancel_latest(session_id)
            if not cancelled:
                return self._simple_response(session_id, "You have no active timers to cancel.", source="timer")
            return self._simple_response(session_id, "Timer cancelled.", source="timer")
        if lower in {"how much time is left", "how much time left", "timer status", "show timers", "list timers"}:
            return self._simple_response(session_id, timer_service.summarize(session_id), source="timer")
        calendar_preview = self._parse_calendar_command(text)
        if calendar_preview is not None:
            if calendar_preview["action"] == "list_calendar_events":
                result = action_service.execute("list_calendar_events", calendar_preview.get("target"), dict(calendar_preview.get("params", {})))
                return self._simple_response(session_id, str(result["message"]), source="calendar")
            return self._confirmation_or_execute(session_id, calendar_preview)
        if lower.startswith(("schedule ", "create calendar", "add to calendar")):
            return self._simple_response(
                session_id,
                "I can create that calendar item if you give me a title plus a clear date and time, for example: schedule team sync tomorrow at 9 AM for 30 minutes.",
                source="calendar",
            )
        mail_preview = self._parse_mail_draft_command(text)
        if mail_preview is not None:
            return self._confirmation_or_execute(session_id, mail_preview)
        if lower.startswith(("draft email", "create email draft")):
            return self._simple_response(
                session_id,
                "I can create the Mail draft if you give me the recipient plus a subject, for example: draft email to name@example.com subject Project update body Here's the latest.",
                source="mail",
            )
        if lower in {"what reminders do i have", "list reminders", "show reminders", "my reminders"}:
            return self._simple_response(session_id, reminder_service.summarize_active(), source="reminder")
        if lower.startswith("complete reminder "):
            title = self._strip_prefix(text, "complete reminder ")
            reminder = reminder_service.complete_matching(title)
            if reminder:
                return self._simple_response(session_id, f"Completed reminder: {reminder['title']}.", source="reminder")
            return self._simple_response(session_id, f"I couldn't find an active reminder matching {title}.", source="reminder")
        if lower in {"what app am i in", "what app am i using", "what app is open", "active app"}:
            context = integration_service.active_app_intelligence()
            app_name = str(context.get("app") or "").strip()
            if context.get("ok") and app_name and app_name.lower() != "unknown":
                self._store_interaction_context(session_id, active_app_target=app_name, recent_action="active_app_query")
            return self._simple_response(session_id, str(context.get("summary", "I couldn't tell which app is active.")), source="system")
        if lower in {"what am i doing", "what was i just doing"}:
            context = integration_service.active_app_intelligence()
            previous = self._last_user_command(session_id)
            app_summary = str(context.get("summary") or "I couldn't tell which app is active.")
            if previous:
                app_summary = f"{app_summary} Your last command was: {previous}."
            return self._simple_response(session_id, app_summary, source="system")
        if lower in {"what app is this", "what is this app", "what app is this?"}:
            context = integration_service.active_app_intelligence()
            app_name = str(context.get("app") or "").strip()
            if context.get("ok") and app_name and app_name.lower() != "unknown":
                self._store_interaction_context(session_id, active_app_target=app_name, recent_action="active_app_query")
            return self._simple_response(session_id, str(context.get("summary", "I couldn't tell which app is active.")), source="system")
        if lower in {
            "what am i looking at",
            "what am i viewing",
            "summarize what i'm viewing",
            "summarize what i am viewing",
            "current context",
            "context check",
        }:
            context = await integration_service.contextual_brief()
            stored_vision = self._get_session_vision_context(session_id)
            vision_excerpt = self._vision_context_excerpt(stored_vision)
            if screen_context:
                current_vision_excerpt = self._vision_context_excerpt(screen_context)
                if current_vision_excerpt:
                    return self._simple_response(
                        session_id,
                        f"{context.get('summary', 'Context is currently unavailable.')} Live screen context: {current_vision_excerpt}",
                        source="system",
                    )
            if vision_excerpt:
                return self._simple_response(
                    session_id,
                    f"{context.get('summary', 'Context is currently unavailable.')} Last screen context: {vision_excerpt}",
                    source="system",
                )
            return self._simple_response(session_id, str(context.get("summary", "Context is currently unavailable.")), source="system")
        if lower in {
            "what's playing",
            "what is playing",
            "spotify status",
            "music status",
            "media status",
            "what song is this",
            "what track is this",
            "what song is playing",
            "what track is playing",
        }:
            status = integration_service.spotify_status()
            if status.get("running") and status.get("player_state") != "not_running":
                track = status.get("track") or "Unknown track"
                artist = status.get("artist") or "Unknown artist"
                album = status.get("album") or "Unknown album"
                state = status.get("player_state") or "running"
                return self._simple_response(
                    session_id,
                    f"Spotify is {state}. {track} by {artist}, from {album}.",
                    source="spotify",
                )
            return self._simple_response(
                session_id,
                str(status.get("message", "Spotify is not running.")),
                source="spotify",
            )
        if lower in {
            "what should i do next",
            "next move",
            "suggest next action",
            "recommended action",
            "what do you suggest",
            "what should i do here",
            "what can i do here",
        }:
            report = await integration_service.contextual_brief()
            suggestions = list(report.get("suggestions") or [])
            if screen_context and screen_context.get("ocr_text"):
                suggestions = [
                    "Summarize this screen text.",
                    "Search based on this screen.",
                    "Compare this with another source.",
                    *suggestions,
                ]
            elif self._get_session_vision_context(session_id):
                suggestions = [
                    "Use the last screen context for a follow-up search.",
                    *suggestions,
                ]
            if suggestions:
                joined = "; ".join(f"{index + 1}) {item.rstrip('.')}" for index, item in enumerate(suggestions[:3]))
                return self._simple_response(
                    session_id,
                    f"Based on the current context, here are the best next moves. {joined}",
                    source="system",
                )
            return self._simple_response(
                session_id,
                "I don't have a strong context-specific recommendation right now, but I can give you a fresh operator briefing.",
                source="system",
            )
        if lower == "switch back":
            previous = memory_service.get_preference("previous_app_target")
            if previous:
                preview = action_service.preview("switch_app", previous)
                return self._confirmation_or_execute(session_id, preview)
            return self._simple_response(session_id, "I don't have a previous app target to switch back to yet.", source="system")
        if lower == "close this":
            target = self._contextual_app_target()
            if target:
                preview = action_service.preview("close_app", target)
                return self._confirmation_or_execute(session_id, preview)
            return self._simple_response(session_id, "I can't tell which app to close safely right now.", source="system")
        if lower in {"what is on my screen", "summarize this screen", "read this screen", "what's on my screen"}:
            if screen_context:
                ocr_excerpt = str(screen_context.get("ocr_text") or "").strip()
                if ocr_excerpt:
                    excerpt = ocr_excerpt[:280] + ("…" if len(ocr_excerpt) > 280 else "")
                    return self._simple_response(
                        session_id,
                        f"{screen_context.get('summary', 'Screen context captured.')} Detected text: {excerpt}",
                        source="vision",
                    )
                return self._simple_response(session_id, str(screen_context.get("summary", "Screen context captured.")), source="vision")
            stored_vision = self._get_session_vision_context(session_id)
            stored_excerpt = self._vision_context_excerpt(stored_vision)
            if stored_excerpt:
                return self._simple_response(
                    session_id,
                    f"I don't have a live capture in this turn, but here's the latest screen context I have: {stored_excerpt}",
                    source="vision",
                )
            return self._simple_response(
                session_id,
                "I need a fresh screenshot to inspect the current screen. Use Mission Control → Inspect Screen, or send this request with screenshot context.",
                source="vision",
            )
        if lower == "search this":
            search = await integration_service.search_based_on_current_page()
            if search.get("ok"):
                if chain_context is not None:
                    chain_context["last_search_summary"] = search["summary"]
                    chain_context["last_search_query"] = str(search.get("query") or "")
                self._remember_search_context(session_id, str(search.get("query") or "current page"), str(search.get("summary") or ""))
                return self._simple_response(session_id, str(search["summary"]), source="search")
            return self._simple_response(session_id, "I don't have enough current page context to search this yet.", source="search")
        if lower.startswith("search based on this page for ") or lower.startswith("search based on this for "):
            modifier = (
                self._strip_prefix(text, "search based on this page for ")
                if lower.startswith("search based on this page for ")
                else self._strip_prefix(text, "search based on this for ")
            )
            search = await integration_service.search_based_on_current_page(modifier)
            if chain_context is not None and search.get("ok"):
                chain_context["last_search_summary"] = search["summary"]
                chain_context["last_search_query"] = str(search.get("query") or modifier)
            if search.get("ok"):
                self._remember_search_context(session_id, str(search.get("query") or modifier), str(search.get("summary") or ""))
            return self._simple_response(session_id, str(search["summary"]), source="search")
        if lower in {"search based on this page", "search based on this", "use this page as context", "use this as context", "search from this page"}:
            if screen_context and screen_context.get("ocr_text"):
                base = str(screen_context.get("ocr_text") or "").strip().splitlines()
                seed = " ".join(base[:3]).strip()
                if seed:
                    search = await integration_service.search_web(seed[:180])
                    if chain_context is not None and search.get("ok"):
                        chain_context["last_search_summary"] = search["summary"]
                        chain_context["last_search_query"] = seed[:180]
                    if search.get("ok"):
                        self._remember_search_context(session_id, seed[:180], str(search.get("summary") or ""))
                    return self._simple_response(
                        session_id,
                        f"I used detected screen text as context. {search.get('summary', 'Search unavailable.')}",
                        source="search",
                    )
            stored_vision = self._get_session_vision_context(session_id)
            stored_text = str((stored_vision or {}).get("ocr_text") or "").strip()
            if stored_text:
                seed = " ".join(stored_text.splitlines()[:3]).strip()
                if seed:
                    search = await integration_service.search_web(seed[:180])
                    if chain_context is not None and search.get("ok"):
                        chain_context["last_search_summary"] = search["summary"]
                        chain_context["last_search_query"] = seed[:180]
                    if search.get("ok"):
                        self._remember_search_context(session_id, seed[:180], str(search.get("summary") or ""))
                    return self._simple_response(
                        session_id,
                        f"I used the last captured screen text as context. {search.get('summary', 'Search unavailable.')}",
                        source="search",
                    )
            search = await integration_service.search_based_on_current_page()
            if chain_context is not None and search.get("ok"):
                chain_context["last_search_summary"] = search["summary"]
                chain_context["last_search_query"] = str(search.get("query") or "")
            if search.get("ok"):
                self._remember_search_context(session_id, str(search.get("query") or "current page"), str(search.get("summary") or ""))
            return self._simple_response(session_id, str(search["summary"]), source="search")
        if lower in {"summarize this", "summarize it", "read it"}:
            if chain_context and chain_context.get("last_search_summary"):
                return self._simple_response(session_id, str(chain_context["last_search_summary"]), source="search")
            if screen_context:
                ocr_excerpt = str(screen_context.get("ocr_text") or "").strip()
                if ocr_excerpt:
                    excerpt = ocr_excerpt[:280] + ("…" if len(ocr_excerpt) > 280 else "")
                    return self._simple_response(
                        session_id,
                        f"{screen_context.get('summary', 'Screen context captured.')} Detected text: {excerpt}",
                        source="vision",
                    )
                return self._simple_response(session_id, str(screen_context.get("summary", "Screen context captured.")), source="vision")
            page = await integration_service.summarize_current_page()
            if not page.get("ok"):
                stored_vision = self._get_session_vision_context(session_id)
                stored_excerpt = self._vision_context_excerpt(stored_vision)
                if stored_excerpt:
                    return self._simple_response(session_id, stored_excerpt, source="vision")
                context = await integration_service.contextual_brief()
                return self._simple_response(session_id, str(context.get("summary", page.get("summary", "Context is unavailable."))), source="system")
            return self._simple_response(session_id, str(page["summary"]), source="browser")
        if lower.startswith("compare this with "):
            comparison_target = self._strip_prefix(text, "compare this with ")
            if screen_context and screen_context.get("ocr_text"):
                excerpt = str(screen_context.get("ocr_text") or "").strip()[:140]
                based_search = await integration_service.search_web(f"{excerpt} {comparison_target}".strip())
                comparison_text = (
                    f"Using detected on-screen text as context, I compared it with {comparison_target}. "
                    f"{based_search.get('summary', 'Search unavailable.')}"
                )
            else:
                current_page = await integration_service.summarize_current_page()
                based_search = await integration_service.search_based_on_current_page(comparison_target)
                comparison_text = (
                    f"Current context: {current_page.get('summary', 'Page summary unavailable.')} "
                    f"Comparison search: {based_search.get('summary', 'Search unavailable.')}"
                )
            return self._simple_response(session_id, comparison_text, source="search")
        if lower.startswith("weather"):
            place = self._strip_prefix(text, "weather").replace("in", "", 1).strip() or "Muscat"
            weather = await integration_service.get_weather(place)
            return self._simple_response(session_id, str(weather["summary"]), source="weather")
        if lower.startswith("news"):
            topic = self._strip_prefix(text, "news").replace("about", "", 1).replace("on", "", 1).strip() or "technology"
            news = await integration_service.get_news(topic)
            return self._simple_response(session_id, str(news["summary"]), source="news")
        if lower in {"open google", "go to google"}:
            preview = action_service.preview("open_url", SAFE_SITE_SHORTCUTS["google"])
            return self._confirmation_or_execute(session_id, preview)
        if lower in {"open first result", "click first result", "open the first result"}:
            return self._simple_response(
                session_id,
                "I can open search pages, but safely selecting the first result is not supported yet.",
                source="search",
            )
        if lower.startswith("search youtube for ") or lower.startswith("youtube search for "):
            query = (
                self._strip_prefix(text, "search youtube for ")
                if lower.startswith("search youtube for ")
                else self._strip_prefix(text, "youtube search for ")
            )
            if not query or query.strip().lower() in {"it", "that", "this"}:
                return self._simple_response(session_id, "I need a YouTube search query first.", source="search")
            preview = action_service.preview("open_url", f"https://www.youtube.com/results?search_query={quote_plus(query)}")
            response = self._confirmation_or_execute(session_id, preview)
            if not response.get("confirmation_required"):
                self._remember_search_context(session_id, query, response.get("text"))
            return response
        if lower.startswith("search google for ") or lower.startswith("google search for ") or lower.startswith("search for "):
            if lower.startswith("search google for "):
                query = self._strip_prefix(text, "search google for ")
            elif lower.startswith("google search for "):
                query = self._strip_prefix(text, "google search for ")
            else:
                query = self._strip_prefix(text, "search for ")
            if not query or query.strip().lower() in {"it", "that", "this"}:
                self._set_pending_clarification(session_id, {"type": "search_query"})
                return self._simple_response(session_id, "What should I search for?", source="search")
            recent_url = str((chain_context or {}).get("recent_url") or "").lower()
            if chain_context is not None and "youtube.com" in recent_url:
                preview = action_service.preview("open_url", f"https://www.youtube.com/results?search_query={quote_plus(query)}")
                response = self._confirmation_or_execute(session_id, preview)
                if not response.get("confirmation_required"):
                    self._remember_search_context(session_id, query, response.get("text"))
                return response
            search = await integration_service.search_web(query)
            if chain_context is not None:
                chain_context["last_search_summary"] = search["summary"]
                chain_context["last_search_query"] = query
            if search.get("ok"):
                self._remember_search_context(session_id, query, str(search.get("summary") or ""))
            return self._simple_response(session_id, str(search["summary"]), source="search")
        if lower.startswith("search google ") or lower.startswith("google "):
            query = text.split(" ", 1)[1] if " " in text else ""
            if not query or query.strip().lower() in {"it", "that", "this"}:
                return self._simple_response(session_id, "I need a search query first.", source="search")
            search = await integration_service.search_web(query)
            if chain_context is not None:
                chain_context["last_search_summary"] = search["summary"]
                chain_context["last_search_query"] = query
            if search.get("ok"):
                self._remember_search_context(session_id, query, str(search.get("summary") or ""))
            return self._simple_response(session_id, str(search["summary"]), source="search")
        if lower in {"what page am i on", "what page is this", "what page is open", "current url", "what is the current url"}:
            context = integration_service.page_awareness()
            if context.get("ok"):
                self._store_interaction_context(
                    session_id,
                    recent_action="page_query",
                    recent_page_title=str(context.get("title") or ""),
                    recent_page_url=str(context.get("url") or ""),
                    active_app_target=str(context.get("app") or ""),
                )
                return self._simple_response(
                    session_id,
                    f"You're on {context.get('title') or 'this page'} at {context.get('url')}.",
                    source="browser",
                )
            return self._simple_response(session_id, str(context.get("message", "I couldn't read the current browser page.")), source="browser")
        if lower in {"what website is open", "what website is this", "what tab is this"}:
            context = integration_service.page_awareness()
            if context.get("ok"):
                return self._simple_response(
                    session_id,
                    str(context.get("message", f"Current website is {context.get('url') or 'unavailable'}.")),
                    source="browser",
                )
            return self._simple_response(session_id, str(context.get("message", "I couldn't read the current browser page.")), source="browser")
        if lower in {"read this page", "summarize this page", "read the current page"}:
            page = await integration_service.summarize_current_page()
            return self._simple_response(session_id, str(page["summary"]), source="browser")
        if lower in {"open this in google", "google this page"}:
            based = await integration_service.search_based_on_current_page()
            url = str(based.get("google_url") or "").strip()
            if not url:
                return self._simple_response(session_id, str(based.get("summary", "I couldn't prepare a Google link from this page.")), source="search")
            preview = action_service.preview("open_url", url)
            response = self._confirmation_or_execute(session_id, preview)
            if not response.get("confirmation_required"):
                response["text"] = self._polish_reply(f"{response['text']} {based.get('summary', '')}".strip())
                response.setdefault("metadata", {})["source"] = "search"
                response["metadata"]["search_context"] = based
            return response
        if lower.startswith("switch back to "):
            target = self._normalize_app_target(self._strip_prefix(text, "switch back to "))
            if self._is_ambiguous_app_target(target):
                target = self._resolve_recent_app_reference(session_id)
                if not target:
                    return self._simple_response(session_id, "Which app should I switch to?", source="clarification")
            preview = action_service.preview("switch_app", target)
            return self._confirmation_or_execute(session_id, preview)
        if lower.startswith("go back to "):
            target = self._normalize_app_target(self._strip_prefix(text, "go back to "))
            if self._is_ambiguous_app_target(target):
                target = self._resolve_recent_app_reference(session_id)
                if not target:
                    return self._simple_response(session_id, "Which app should I switch to?", source="clarification")
            preview = action_service.preview("switch_app", target)
            return self._confirmation_or_execute(session_id, preview)
        if lower.startswith("switch to "):
            target = self._normalize_app_target(self._strip_prefix(text, "switch to "))
            if self._is_ambiguous_app_target(target):
                if target.lower() in {"it", "that", "this", "there"}:
                    target = self._resolve_recent_app_reference(session_id)
                if not target:
                    return self._simple_response(session_id, "Which app should I switch to?", source="clarification")
            preview = action_service.preview("switch_app", target)
            return self._confirmation_or_execute(session_id, preview)
        if lower in {"switch to it", "switch to that", "switch there", "switch back to it", "switch back to that"}:
            target = self._resolve_recent_app_reference(session_id)
            if not target:
                return self._simple_response(session_id, "Which app should I switch to?", source="clarification")
            preview = action_service.preview("switch_app", target)
            return self._confirmation_or_execute(session_id, preview)
        if lower.startswith("close app ") or lower.startswith("quit app "):
            target = self._normalize_app_target(
                self._strip_prefix(text, "close app ") if lower.startswith("close app ") else self._strip_prefix(text, "quit app ")
            )
            if self._is_ambiguous_app_target(target):
                target = self._resolve_recent_app_reference(session_id)
                if not target:
                    return self._simple_response(session_id, "Which app should I close?", source="clarification")
            preview = action_service.preview("close_app", target)
            return self._confirmation_or_execute(session_id, preview)
        if lower.startswith("close ") or lower.startswith("quit "):
            target = self._normalize_app_target(
                self._strip_prefix(text, "close ") if lower.startswith("close ") else self._strip_prefix(text, "quit ")
            )
            if self._is_ambiguous_app_target(target):
                target = self._resolve_recent_app_reference(session_id)
                if not target:
                    return self._simple_response(session_id, "Which app should I close?", source="clarification")
            preview = action_service.preview("close_app", target)
            return self._confirmation_or_execute(session_id, preview)
        if lower.startswith("open folder "):
            target = self._strip_prefix(text, "open folder ")
            preview = action_service.preview("open_folder", target)
            return self._confirmation_or_execute(session_id, preview)
        if lower in {"open downloads", "open desktop", "open documents", "open workspace", "open audio"}:
            target = lower.removeprefix("open ").strip()
            preview = action_service.preview("open_folder", target)
            return self._confirmation_or_execute(session_id, preview)
        if lower.startswith("open file "):
            target = self._strip_prefix(text, "open file ")
            preview = action_service.preview("open_file", target)
            return self._confirmation_or_execute(session_id, preview)
        if lower in {"read clipboard", "what is on my clipboard", "clipboard"}:
            result = action_service.execute("clipboard_read")
            return self._simple_response(session_id, str(result.get("text") or "Clipboard is empty."), source="clipboard")
        if lower.startswith("copy ") or lower.startswith("write to clipboard "):
            if lower.startswith("copy "):
                payload = self._strip_prefix(text, "copy ")
            else:
                payload = self._strip_prefix(text, "write to clipboard ")
            preview = action_service.preview("clipboard_write", payload)
            return self._confirmation_or_execute(session_id, preview)
        if lower.startswith("type ") or lower.startswith("type this "):
            payload = self._strip_prefix(text, "type this ") if lower.startswith("type this ") else self._strip_prefix(text, "type ")
            preview = action_service.preview("type_text", payload)
            return self._confirmation_or_execute(session_id, preview)
        if lower in {"volume up", "increase volume", "turn volume up", "turn the volume up"}:
            result = action_service.execute("volume_up")
            return self._simple_response(session_id, str(result["message"]), source="action")
        if lower in {"volume down", "decrease volume", "turn volume down", "turn the volume down"}:
            result = action_service.execute("volume_down")
            return self._simple_response(session_id, str(result["message"]), source="action")
        if lower in {"mute", "mute volume", "mute sound", "mute system audio"}:
            preview = action_service.preview("mute_volume")
            return self._confirmation_or_execute(session_id, preview)
        if lower in {"unmute", "unmute volume", "unmute sound", "unmute system audio"}:
            preview = action_service.preview("unmute_volume")
            return self._confirmation_or_execute(session_id, preview)
        if lower in {"brightness up", "increase brightness", "brightness down", "decrease brightness"}:
            return self._simple_response(
                session_id,
                "Brightness control is not wired into the current safe action layer, so I won't pretend I changed it.",
                source="policy",
            )
        if lower.startswith("set volume to "):
            level_text = "".join(ch for ch in self._strip_prefix(text, "set volume to ") if ch.isdigit())
            level = int(level_text) if level_text else 50
            preview = action_service.preview("set_volume", str(level), {"level": level})
            return self._confirmation_or_execute(session_id, preview)
        if lower in {
            "play",
            "pause",
            "play or pause",
            "toggle playback",
            "play music",
            "play the music",
            "pause music",
            "pause the music",
            "stop music",
            "stop the music",
            "resume music",
            "resume the music",
        }:
            result = action_service.execute("media_play_pause")
            return self._simple_response(session_id, str(result["message"]), source="action")
        if lower in {"pause it", "pause that", "play it", "play that"}:
            target = self._resolve_recent_app_reference(session_id)
            if target and target.lower() == "spotify":
                preview = action_service.preview("spotify_pause" if lower.startswith("pause") else "spotify_play", "Spotify")
                return self._confirmation_or_execute(session_id, preview)
            if not target:
                return self._simple_response(session_id, "What should I pause?", source="clarification")
            return self._simple_response(session_id, f"I can only pause or play known media targets right now. I have {target} as the recent app.", source="clarification")
        if lower in {"next track", "next song", "skip track", "previous track", "previous song"}:
            action_name = "spotify_previous" if lower.startswith("previous") else "spotify_next"
            preview = action_service.preview(action_name, "Spotify")
            return self._confirmation_or_execute(session_id, preview)
        if lower.startswith("open ") or lower.startswith("launch "):
            target = self._normalize_app_target(
                self._strip_prefix(text, "open ") if lower.startswith("open ") else self._strip_prefix(text, "launch ")
            )
            if self._is_ambiguous_app_target(target):
                self._set_pending_clarification(session_id, {"type": "open_target"})
                return self._simple_response(session_id, "Which app or website should I open?", source="clarification")
            app_preview = action_service.preview("open_app", target)
            if app_preview["allowed"]:
                return self._confirmation_or_execute(session_id, app_preview)
            if self._looks_like_explicit_website_target(target):
                site_target = SAFE_SITE_SHORTCUTS.get(target.lower(), target)
                url_preview = action_service.preview("open_url", site_target)
                return self._confirmation_or_execute(session_id, url_preview)
            return self._simple_response(
                session_id,
                f"I can't open {target} as a trusted macOS app yet. If you meant a website, say open the website explicitly.",
                source="policy",
            )
        if lower in {"play spotify", "pause spotify", "next spotify", "previous spotify"}:
            action_map = {
                "play spotify": "spotify_play",
                "pause spotify": "spotify_pause",
                "next spotify": "spotify_next",
                "previous spotify": "spotify_previous",
            }
            preview = action_service.preview(action_map[lower], "Spotify")
            return self._confirmation_or_execute(session_id, preview)
        if lower in {"spotify status", "what is playing on spotify", "is spotify running", "what's playing on spotify"}:
            spotify = integration_service.spotify_status()
            if not spotify.get("enabled"):
                return self._simple_response(session_id, "Spotify integration is disabled.", source="system")
            if not spotify.get("running"):
                return self._simple_response(session_id, "Spotify is not currently running.", source="system")
            if spotify.get("player_state") == "playing":
                return self._simple_response(session_id, f"Spotify is playing {spotify.get('track')} by {spotify.get('artist')}.", source="system")
            return self._simple_response(session_id, f"Spotify is {spotify.get('player_state', 'idle')}.", source="system")
        if lower.startswith("search "):
            query = self._strip_prefix(text, "search ")
            if not query or query.strip().lower() in {"it", "that", "this"}:
                self._set_pending_clarification(session_id, {"type": "search_query"})
                return self._simple_response(session_id, "What should I search for?", source="search")
            if chain_context is not None:
                search = await integration_service.search_web(query)
                chain_context["last_search_summary"] = search["summary"]
                chain_context["last_search_query"] = query
                if search.get("ok"):
                    self._remember_search_context(session_id, query, str(search.get("summary") or ""))
                return self._simple_response(session_id, str(search["summary"]), source="search")
            preview = action_service.preview("open_url", f"https://www.google.com/search?q={query.replace(' ', '+')}")
            response = self._confirmation_or_execute(session_id, preview)
            if not response.get("confirmation_required"):
                self._remember_search_context(session_id, query, response.get("text"))
            return response
        return None

    async def _run_task_chain(
        self,
        text: str,
        session_id: str,
        screen_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        pieces = self._split_chain_candidates(text)
        if len(pieces) <= 1 or len(pieces) > 4:
            return None

        results: list[str] = []
        chain_context: dict[str, Any] = {}
        step_status: list[dict[str, str]] = []
        for index, part in enumerate(pieces, start=1):
            routed = await self._route_single_command(part, session_id, chain_context, screen_context)
            if routed is None:
                return None
            if routed.get("confirmation_required"):
                if results:
                    routed["text"] = f"I've handled: {'; '.join(results)}. Next step needs confirmation. {routed['text']}"
                return routed
            source = str(routed.get("metadata", {}).get("source", ""))
            if routed.get("follow_up") or source in {"clarification", "policy"}:
                if results:
                    routed["text"] = f"I've handled: {'; '.join(results)}. {routed['text']}"
                return routed
            result_text = str(routed["text"])
            results.append(result_text)

            action_result = routed.get("metadata", {}).get("result")
            action_status = str((action_result or {}).get("status", ""))
            step_status.append({"step": str(index), "command": part, "source": source or "unknown", "status": action_status or "ok"})

            if action_status in {"failed", "attempted_unverified"}:
                completed = f"Completed steps: {'; '.join(results[:-1])}. " if len(results) > 1 else ""
                fail_text = (
                    f"{completed}Chain stopped at step {index} ({part}): {result_text}"
                )
                return {
                    "session_id": session_id,
                    "text": self._polish_reply(fail_text),
                    "follow_up": False,
                    "confirmation_required": False,
                    "confirmation_id": None,
                    "action_preview": None,
                    "memory_updated": False,
                    "metadata": {
                        "source": "chain",
                        "chain_status": f"stopped_{action_status}",
                        "steps": step_status,
                    },
                }

            if action_status == "verified":
                action_name = str((action_result or {}).get("app", "") or "")
                if action_name:
                    chain_context["recent_app"] = action_name
                action_url = str((action_result or {}).get("url", "") or "")
                if action_url:
                    chain_context["recent_url"] = action_url

        return {
            "session_id": session_id,
            "text": self._polish_reply(" ".join(results)),
            "follow_up": False,
            "confirmation_required": False,
            "confirmation_id": None,
            "action_preview": None,
            "memory_updated": False,
            "metadata": {"source": "chain", "chain_status": "completed", "steps": step_status},
        }

    def _split_chain_candidates(self, text: str) -> list[str]:
        raw = text.strip()
        for separator in [r"\band then\b", r"\bthen\b", r";", r",", r"\band\b"]:
            if re.search(separator, raw, flags=re.IGNORECASE):
                pieces = [part.strip(" ,.") for part in re.split(separator, raw, flags=re.IGNORECASE) if part.strip(" ,.")]
                if len(pieces) > 1 and all(self._looks_like_command(part) for part in pieces):
                    return pieces
        return [raw]

    def _looks_like_command(self, text: str) -> bool:
        lower = text.lower().strip()
        verbs = (
            "open ",
            "launch ",
            "switch ",
            "close ",
            "quit ",
            "search ",
            "google ",
            "use ",
            "compare ",
            "read ",
            "summarize ",
            "what ",
            "what's ",
            "what is ",
            "how ",
            "why ",
            "where ",
            "when ",
            "show ",
            "list ",
            "remind ",
            "set ",
            "play ",
            "pause ",
            "next ",
            "previous ",
            "skip ",
            "mute",
            "unmute",
            "copy ",
            "type ",
            "volume ",
            "brightness ",
            "wake word",
            "status ",
            "system ",
            "check ",
            "tell me ",
            "start ",
            "take ",
            "note:",
            "delete note ",
        )
        return lower.startswith(verbs) or lower in {
            "switch back",
            "close this",
            "search this",
            "summarize this",
            "summarize it",
            "read it",
            "jarvis report",
            "what am i looking at",
            "what app is this",
            "what website is open",
            "what tab is this",
            "how's my mac doing",
            "why is my mac slow",
        }

    def _is_system_status_query(self, lower: str) -> bool:
        normalized = lower.strip(" ?.!")
        exact = {
            "what's the cpu load",
            "what is the cpu load",
            "what is my cpu usage",
            "what's my cpu usage",
            "how much ram am i using",
            "what's my memory usage",
            "what is my memory usage",
            "what's my system status",
            "what is my system status",
            "how's my mac doing",
            "how is my mac doing",
            "why is my mac slow",
            "why is my mac running slow",
            "what's using my mac",
            "what is using my mac",
            "is my cpu high",
            "is my ram high",
            "is my battery low",
            "my battery",
            "battery status",
            "how much storage do i have",
            "how much disk space do i have",
        }
        if normalized in exact:
            return True
        cpu_query = "cpu" in normalized and any(term in normalized for term in ("load", "usage", "using"))
        memory_query = any(term in normalized for term in ("ram", "memory")) and any(term in normalized for term in ("usage", "using", "left", "free"))
        battery_query = "battery" in normalized and any(term in normalized for term in ("low", "status", "left", "percent"))
        storage_query = any(term in normalized for term in ("storage", "disk space", "disk usage"))
        process_query = "using my mac" in normalized or "what's using" in normalized or "what is using" in normalized
        system_query = "system status" in normalized or "mac doing" in normalized or "mac slow" in normalized
        return cpu_query or memory_query or battery_query or storage_query or process_query or system_query

    def _normalize_user_text(self, text: str) -> str:
        cleaned = " ".join(text.split())
        wake_word = get_settings().wake_word.strip().lower()
        lower = cleaned.lower()
        if wake_word and lower.startswith(f"{wake_word} "):
            cleaned = cleaned[len(wake_word) :].strip(" ,.") or wake_word.title()
            lower = cleaned.lower()
        if wake_word and lower == wake_word:
            return "Ready."
        cleaned = re.sub(r"^(please|hey jarvis|hi jarvis|hello jarvis)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^(can you|could you|would you|will you)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^(for me\s+)?please\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+please$", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip(" ,.?!")

    def _parse_reminder(self, text: str, session_id: str) -> dict[str, Any] | None:
        lowered = text.lower().strip()
        if "after school" in lowered:
            return self._simple_response(
                session_id,
                "I need a specific time for after school. For example: remind me at 4 PM to check homework.",
                source="reminder",
            )

        match = re.match(r"remind me (?:to )?(.+?) in (\d+)\s*(minute|minutes|hour|hours)", lowered)
        if match:
            title = match.group(1).strip()
            amount = int(match.group(2))
            unit = match.group(3)
            return self._create_relative_reminder(session_id, title, amount, unit)

        match = re.match(r"remind me in (\d+)\s*(minute|minutes|hour|hours) to (.+)", lowered)
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            title = match.group(3).strip()
            return self._create_relative_reminder(session_id, title, amount, unit)

        match = re.match(r"remind me tomorrow to (.+)", lowered)
        if match:
            return self._create_named_time_reminder(session_id, match.group(1).strip(), "tomorrow")

        match = re.match(r"remind me (?:to )?(.+?) tomorrow", lowered)
        if match:
            return self._create_named_time_reminder(session_id, match.group(1).strip(), "tomorrow")

        match = re.match(r"remind me tonight to (.+)", lowered)
        if match:
            return self._create_named_time_reminder(session_id, match.group(1).strip(), "tonight")

        match = re.match(r"remind me (?:to )?(.+?) tonight", lowered)
        if match:
            return self._create_named_time_reminder(session_id, match.group(1).strip(), "tonight")
        return None

    def _create_reminder_from_time_answer(self, session_id: str, title: str, answer: str) -> dict[str, Any] | None:
        lowered = answer.lower().strip()

        match = re.match(r"^(?:in\s+)?(\d+)\s*(minute|minutes|hour|hours)$", lowered)
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            return self._create_relative_reminder(session_id, title, amount, unit)

        if lowered in {"tomorrow", "tonight"}:
            return self._create_named_time_reminder(session_id, title, lowered)

        time_text = self._strip_prefix(answer, "at ") if lowered.startswith("at ") else answer
        parsed_time = self._parse_time_fragment(time_text)
        if parsed_time:
            hour, minute = parsed_time
            due_local = datetime.now().astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0)
            if due_local <= datetime.now().astimezone():
                due_local += timedelta(days=1)
            reminder = reminder_service.create(title=title, due_at=due_local, session_id=session_id)
            return self._simple_response(
                session_id,
                f"I'll remind you to {title} at {reminder['due_at'].astimezone().strftime('%I:%M %p')}.",
                source="reminder",
            )

        return None

    def _create_relative_reminder(self, session_id: str, title: str, amount: int, unit: str) -> dict[str, Any]:
        delta = timedelta(hours=amount) if unit.startswith("hour") else timedelta(minutes=amount)
        reminder = reminder_service.create(
            title=title,
            due_at=datetime.now(UTC) + delta,
            session_id=session_id,
        )
        return self._simple_response(
            session_id,
            f"I'll remind you to {title} at {reminder['due_at'].astimezone().strftime('%I:%M %p')}.",
            source="reminder",
        )

    def _create_named_time_reminder(self, session_id: str, title: str, phrase: str) -> dict[str, Any]:
        now = datetime.now().astimezone()
        if phrase == "tomorrow":
            due_local = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        else:
            due_local = now.replace(hour=20, minute=0, second=0, microsecond=0)
            if due_local <= now:
                due_local += timedelta(days=1)
        reminder = reminder_service.create(title=title, due_at=due_local, session_id=session_id)
        return self._simple_response(
            session_id,
            f"I'll remind you to {title} {phrase} at {reminder['due_at'].astimezone().strftime('%I:%M %p')}.",
            source="reminder",
        )

    def _action_response(
        self,
        session_id: str,
        preview: dict[str, Any],
        result: dict[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "text": self._polish_reply(str(result.get("message", "Action finished."))),
            "follow_up": False,
            "confirmation_required": False,
            "confirmation_id": None,
            "action_preview": preview,
            "memory_updated": False,
            "metadata": {
                "source": source,
                "result": result,
                "action_status": result.get("status"),
                "action_success": bool(result.get("success")),
                "action_verified": bool(result.get("verified")),
            },
        }

    def _confirmation_or_execute(self, session_id: str, preview: dict[str, Any]) -> dict[str, Any]:
        if not preview["allowed"]:
            return self._simple_response(session_id, "That action is blocked by the current safety policy.", source="policy")
        if preview["requires_confirmation"]:
            pending = confirmation_service.create(preview)
            return {
                "session_id": session_id,
                "text": f"Authorization required. Prepared action: {preview['description']}",
                "follow_up": False,
                "confirmation_required": True,
                "confirmation_id": pending["confirmation_id"],
                "action_preview": preview,
                "memory_updated": False,
                "metadata": {"source": "confirmation", "pending_confirmation_id": pending["confirmation_id"]},
            }
        result = action_service.execute(str(preview["action"]), preview.get("target"), dict(preview.get("params", {})))
        self._remember_app_target_if_needed(preview, result)
        self._remember_interaction_result(session_id, preview, result)
        return self._action_response(session_id, preview, result, source="action")

    def confirm_action(self, confirmation_id: str) -> dict[str, Any]:
        pending = confirmation_service.confirm(confirmation_id)
        if not pending:
            return {"ok": False, "status": "not_found", "message": "Confirmation not found.", "result": None}
        payload = dict(pending["payload"])
        result = action_service.execute(str(payload["action"]), payload.get("target"), dict(payload.get("params", {})))
        self._remember_app_target_if_needed(payload, result)
        status = "confirmed"
        if not result.get("success"):
            status = "confirmed_unverified" if result.get("attempted") else "confirmed_failed"
        return {
            "ok": bool(result.get("success")),
            "status": status,
            "message": result.get("message", "Confirmed."),
            "result": result,
        }

    def cancel_action(self, confirmation_id: str) -> dict[str, Any]:
        payload = confirmation_service.cancel(confirmation_id)
        if not payload:
            return {"ok": False, "status": "not_found", "message": "Confirmation not found.", "result": None}
        return {"ok": True, "status": "cancelled", "message": "Action cancelled.", "result": None}

    def _summarize_recent_history(self, session_id: str) -> str:
        messages = memory_service.get_recent_messages(session_id, limit=6)
        pairs = [f"{item['role']}: {item['content']}" for item in messages]
        return " | ".join(pairs)

    def _persist_assistant_response(self, session_id: str, text: str) -> None:
        memory_service.add_message(session_id, "assistant", text)
        summary = self._summarize_recent_history(session_id)
        memory_service.update_summary(session_id, summary)

    def _simple_response(self, session_id: str, text: str, *, memory_updated: bool = False, source: str) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "text": self._polish_reply(text),
            "follow_up": self.infer_follow_up(text),
            "confirmation_required": False,
            "confirmation_id": None,
            "action_preview": None,
            "memory_updated": memory_updated,
            "metadata": {"source": source},
        }

    def _remember_app_target_if_needed(self, preview: dict[str, Any], result: dict[str, Any]) -> None:
        if not result.get("success"):
            return
        if preview.get("action") not in {"open_app", "switch_app"} or not preview.get("target"):
            return
        current = memory_service.get_preference("recent_app_target")
        target = str(preview["target"])
        if current and current != target:
            memory_service.store_memory("previous_app_target", current, "preference")
        memory_service.store_memory("recent_app_target", target, "preference")

    def _remember_interaction_result(self, session_id: str, preview: dict[str, Any], result: dict[str, Any]) -> None:
        action = str(preview.get("action") or "")
        target = str(preview.get("target") or "").strip()
        self._store_interaction_context(
            session_id,
            recent_action=action,
            last_action_text=str(result.get("message") or "").strip(),
            last_action_status=str(result.get("status") or "").strip(),
        )
        if not result.get("success"):
            return
        if action in {"open_app", "switch_app"} and target:
            self._remember_app_context(session_id, target, action=action)
            return
        if action.startswith("spotify_"):
            self._remember_app_context(session_id, "Spotify", action=action)
            return
        if action == "open_url" and target:
            self._store_interaction_context(session_id, recent_action=action, recent_page_url=target)

    def _contextual_app_target(self) -> str | None:
        context = integration_service.active_application()
        app_name = str(context.get("app") or "").strip()
        if app_name and app_name.lower() != "jarvis":
            return app_name
        return memory_service.get_preference("recent_app_target")


assistant_service = AssistantService()
