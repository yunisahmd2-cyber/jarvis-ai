from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from backend.app.core.config import get_settings
from backend.app.repositories.sqlite import get_connection, row_to_dict, utcnow


class MemoryService:
    def import_legacy_files(self) -> None:
        settings = get_settings()
        seed_path = Path(settings.memory_seed_file)
        notes_path = Path(settings.notes_seed_file)

        with get_connection() as connection:
            existing_messages = connection.execute("SELECT COUNT(*) AS count FROM session_messages").fetchone()["count"]
            existing_memories = connection.execute("SELECT COUNT(*) AS count FROM memories").fetchone()["count"]

        if existing_messages or existing_memories:
            return

        if seed_path.exists():
            try:
                data = json.loads(seed_path.read_text())
            except Exception:
                data = {}

            for key in ("model_name", "power_mode", "voice_mode", "personality", "location"):
                value = data.get(key)
                if value:
                    self.store_memory(key=key, value=str(value), category="preference")

            for item in data.get("context_window", []):
                role = str(item.get("role", "user"))
                content = str(item.get("content", "")).strip()
                if content:
                    self.add_message("legacy-import", role, content)

        if notes_path.exists():
            try:
                notes = json.loads(notes_path.read_text())
            except Exception:
                notes = []
            if isinstance(notes, list):
                for idx, note in enumerate(notes):
                    self.store_memory(key=f"legacy_note_{idx}", value=str(note), category="note")

    def add_message(self, session_id: str, role: str, content: str) -> None:
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO session_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, utcnow()),
            )

    def get_recent_messages(self, session_id: str, limit: int = 12) -> list[dict[str, object]]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT role, content, created_at
                FROM session_messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return list(reversed([row_to_dict(row) for row in rows]))

    def store_memory(self, key: str, value: str, category: str = "preference") -> dict[str, object]:
        updated_at = utcnow()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO memories (key, value, category, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    category = excluded.category,
                    updated_at = excluded.updated_at
                """,
                (key, value, category, updated_at),
            )
        return {
            "key": key,
            "value": value,
            "category": category,
            "updated_at": datetime.fromisoformat(updated_at),
        }

    def search_memory(self, query: str) -> list[dict[str, object]]:
        q = f"%{query.strip()}%"
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT key, value, category, updated_at
                FROM memories
                WHERE key LIKE ? OR value LIKE ? OR category LIKE ?
                ORDER BY updated_at DESC
                LIMIT 20
                """,
                (q, q, q),
            ).fetchall()
        results = [row_to_dict(row) for row in rows]
        for item in results:
            item["updated_at"] = datetime.fromisoformat(str(item["updated_at"]))
        return results

    def list_preferences(self) -> list[dict[str, object]]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT key, value, category, updated_at
                FROM memories
                WHERE category = 'preference'
                ORDER BY key ASC
                """
            ).fetchall()
        results = [row_to_dict(row) for row in rows]
        for item in results:
            item["updated_at"] = datetime.fromisoformat(str(item["updated_at"]))
        return results

    def get_memory(self, key: str) -> dict[str, object] | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT key, value, category, updated_at
                FROM memories
                WHERE key = ?
                """,
                (key,),
            ).fetchone()
        if row is None:
            return None
        item = row_to_dict(row)
        item["updated_at"] = datetime.fromisoformat(str(item["updated_at"]))
        return item

    def get_preference(self, key: str, default: str | None = None) -> str | None:
        item = self.get_memory(key)
        if item is None:
            return default
        return str(item["value"])

    def get_power_mode(self) -> str:
        mode = (self.get_preference("power_mode", "basic") or "basic").strip().lower()
        if mode not in {"basic", "advanced"}:
            return "basic"
        return mode

    def set_power_mode(self, mode: str) -> dict[str, object]:
        normalized = mode.strip().lower()
        if normalized not in {"basic", "advanced"}:
            raise ValueError("power mode must be 'basic' or 'advanced'")
        return self.store_memory("power_mode", normalized, "preference")

    def get_wake_word_desired_enabled(self) -> bool:
        value = (self.get_preference("wake_word_enabled", "false") or "false").strip().lower()
        return value in {"1", "true", "yes", "on"}

    def set_wake_word_desired_enabled(self, enabled: bool) -> dict[str, object]:
        return self.store_memory("wake_word_enabled", "true" if enabled else "false", "preference")

    def update_summary(self, session_id: str, summary: str) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO session_summaries (session_id, summary, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    summary = excluded.summary,
                    updated_at = excluded.updated_at
                """,
                (session_id, summary, utcnow()),
            )

    def get_summary(self, session_id: str) -> str | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT summary FROM session_summaries WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["summary"])


memory_service = MemoryService()
