from __future__ import annotations

import uuid
from datetime import UTC, datetime


class ConfirmationService:
    def __init__(self) -> None:
        self._pending: dict[str, dict[str, object]] = {}

    def create(self, action_payload: dict[str, object]) -> dict[str, object]:
        confirmation_id = str(uuid.uuid4())
        payload = {
            "confirmation_id": confirmation_id,
            "status": "pending",
            "created_at": datetime.now(UTC),
            "payload": action_payload,
        }
        self._pending[confirmation_id] = payload
        return payload

    def get(self, confirmation_id: str) -> dict[str, object] | None:
        return self._pending.get(confirmation_id)

    def confirm(self, confirmation_id: str) -> dict[str, object] | None:
        payload = self._pending.pop(confirmation_id, None)
        if payload:
            payload["status"] = "confirmed"
        return payload

    def cancel(self, confirmation_id: str) -> dict[str, object] | None:
        payload = self._pending.pop(confirmation_id, None)
        if payload:
            payload["status"] = "cancelled"
        return payload


confirmation_service = ConfirmationService()
