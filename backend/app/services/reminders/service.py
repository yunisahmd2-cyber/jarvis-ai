from __future__ import annotations

import uuid
from datetime import UTC, datetime

from backend.app.repositories.sqlite import get_connection, row_to_dict, utcnow


class ReminderService:
    def create(self, *, title: str, due_at: datetime, session_id: str | None = None) -> dict[str, object]:
        reminder_id = str(uuid.uuid4())
        created_at = utcnow()
        due_iso = due_at.astimezone(UTC).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO reminders (id, session_id, title, due_at, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (reminder_id, session_id, title.strip(), due_iso, created_at, None),
            )
        return {
            "id": reminder_id,
            "title": title.strip(),
            "due_at": datetime.fromisoformat(due_iso),
            "created_at": datetime.fromisoformat(created_at),
            "completed_at": None,
            "session_id": session_id,
        }

    def list_active(self) -> list[dict[str, object]]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, title, due_at, created_at, completed_at
                FROM reminders
                WHERE completed_at IS NULL
                ORDER BY due_at ASC
                """
            ).fetchall()
        return [self._normalize(row_to_dict(row)) for row in rows]

    def due(self, now: datetime | None = None) -> list[dict[str, object]]:
        active = self.list_active()
        threshold = (now or datetime.now(UTC)).astimezone(UTC)
        return [item for item in active if item["due_at"] <= threshold]

    def complete(self, reminder_id: str) -> dict[str, object] | None:
        completed_at = utcnow()
        with get_connection() as connection:
            connection.execute(
                "UPDATE reminders SET completed_at = ? WHERE id = ?",
                (completed_at, reminder_id),
            )
            row = connection.execute(
                """
                SELECT id, session_id, title, due_at, created_at, completed_at
                FROM reminders
                WHERE id = ?
                """,
                (reminder_id,),
            ).fetchone()
        if row is None:
            return None
        return self._normalize(row_to_dict(row))

    def complete_matching(self, query: str) -> dict[str, object] | None:
        fragment = query.strip().lower()
        if not fragment:
            return None
        for item in self.list_active():
            if fragment in str(item["title"]).lower():
                return self.complete(str(item["id"]))
        return None

    def summarize_active(self) -> str:
        active = self.list_active()
        if not active:
            return "You have no active reminders."
        preview = []
        for item in active[:3]:
            due = item["due_at"].astimezone().strftime("%I:%M %p")
            preview.append(f"{item['title']} at {due}")
        if len(active) > 3:
            return f"You have {len(active)} active reminders. Next up: {'; '.join(preview)}."
        return f"Active reminders: {'; '.join(preview)}."

    def _normalize(self, item: dict[str, object]) -> dict[str, object]:
        item["due_at"] = datetime.fromisoformat(str(item["due_at"]))
        item["created_at"] = datetime.fromisoformat(str(item["created_at"]))
        item["completed_at"] = (
            datetime.fromisoformat(str(item["completed_at"])) if item.get("completed_at") else None
        )
        return item


reminder_service = ReminderService()
