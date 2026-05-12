from __future__ import annotations

import uuid
from datetime import datetime

from backend.app.repositories.sqlite import get_connection, row_to_dict, utcnow


class SessionService:
    def start_session(self, name: str | None = None) -> dict[str, object]:
        session_id = str(uuid.uuid4())
        created_at = utcnow()
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO sessions (id, name, created_at, ended_at) VALUES (?, ?, ?, ?)",
                (session_id, name, created_at, None),
            )
        return {
            "session_id": session_id,
            "status": "active",
            "created_at": datetime.fromisoformat(created_at),
            "ended_at": None,
        }

    def ensure_session(self, session_id: str | None) -> dict[str, object]:
        if session_id:
            existing = self.get_session(session_id)
            if existing:
                return existing
        return self.start_session()

    def get_session(self, session_id: str) -> dict[str, object] | None:
        with get_connection() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        data = row_to_dict(row)
        data["session_id"] = data.pop("id")
        data["status"] = "ended" if data["ended_at"] else "active"
        data["created_at"] = datetime.fromisoformat(str(data["created_at"]))
        data["ended_at"] = datetime.fromisoformat(str(data["ended_at"])) if data["ended_at"] else None
        return data

    def end_session(self, session_id: str) -> dict[str, object]:
        ended_at = utcnow()
        with get_connection() as connection:
            connection.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (ended_at, session_id))
        session = self.get_session(session_id)
        if session is None:
            return {
                "session_id": session_id,
                "status": "ended",
                "created_at": datetime.fromisoformat(ended_at),
                "ended_at": datetime.fromisoformat(ended_at),
            }
        session["status"] = "ended"
        return session


session_service = SessionService()
