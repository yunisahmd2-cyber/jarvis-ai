from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.services.memory.service import memory_service


class TimerService:
    """Small request-driven timer store.

    Timers are intentionally passive: Jarvis can create, cancel, and report them
    without running a background loop in Basic Mode.
    """

    def _key(self, session_id: str, timer_id: str) -> str:
        return f"session:{session_id}:timer:{timer_id}"

    def create(self, *, session_id: str, seconds: int, label: str | None = None) -> dict[str, Any]:
        timer_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        due_at = now + timedelta(seconds=max(1, seconds))
        payload = {
            "id": timer_id,
            "session_id": session_id,
            "label": (label or "timer").strip() or "timer",
            "created_at": now.isoformat(),
            "due_at": due_at.isoformat(),
            "cancelled": False,
        }
        memory_service.store_memory(self._key(session_id, timer_id), json.dumps(payload), "timer")
        return payload

    def active(self, session_id: str | None = None) -> list[dict[str, Any]]:
        timers: list[dict[str, Any]] = []
        for item in memory_service.list_by_category("timer", limit=100):
            try:
                payload = json.loads(str(item.get("value") or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or payload.get("cancelled"):
                continue
            if session_id and payload.get("session_id") != session_id:
                continue
            timers.append(payload)
        timers.sort(key=lambda item: str(item.get("due_at") or ""))
        return timers

    def cancel_latest(self, session_id: str) -> dict[str, Any] | None:
        timers = self.active(session_id)
        if not timers:
            return None
        latest = timers[-1]
        latest["cancelled"] = True
        memory_service.store_memory(self._key(session_id, str(latest["id"])), json.dumps(latest), "timer")
        return latest

    def summarize(self, session_id: str) -> str:
        timers = self.active(session_id)
        if not timers:
            return "You have no active timers in this session."
        now = datetime.now(UTC)
        lines: list[str] = []
        for timer in timers[:3]:
            due_at = datetime.fromisoformat(str(timer["due_at"]))
            remaining = max(0, int((due_at - now).total_seconds()))
            label = str(timer.get("label") or "timer")
            if remaining >= 60:
                minutes = remaining // 60
                seconds = remaining % 60
                remaining_text = f"{minutes} minute{'s' if minutes != 1 else ''}"
                if seconds:
                    remaining_text += f" {seconds} second{'s' if seconds != 1 else ''}"
            else:
                remaining_text = f"{remaining} second{'s' if remaining != 1 else ''}"
            lines.append(f"{label}: {remaining_text} remaining")
        return "Active timers: " + "; ".join(lines) + "."


timer_service = TimerService()
