from __future__ import annotations

from backend.app.services.actions.service import action_service


def is_action_allowed(action: str, target: str | None) -> bool:
    return action_service.is_allowed(action, target)


def requires_confirmation(action: str) -> bool:
    return action_service.requires_confirmation(action)
