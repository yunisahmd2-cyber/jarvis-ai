from __future__ import annotations

from fastapi import APIRouter

from backend.app.models.schemas import ReminderCreateRequest, ReminderResponse
from backend.app.services.reminders.service import reminder_service


router = APIRouter(prefix="/reminders", tags=["reminders"])


@router.post("", response_model=ReminderResponse)
async def create_reminder(request: ReminderCreateRequest) -> ReminderResponse:
    data = reminder_service.create(
        title=request.title,
        due_at=request.due_at,
        session_id=request.session_id,
    )
    return ReminderResponse(**data)


@router.get("", response_model=list[ReminderResponse])
async def list_reminders() -> list[ReminderResponse]:
    return [ReminderResponse(**item) for item in reminder_service.list_active()]


@router.get("/due", response_model=list[ReminderResponse])
async def list_due_reminders() -> list[ReminderResponse]:
    return [ReminderResponse(**item) for item in reminder_service.due()]


@router.post("/{reminder_id}/complete", response_model=ReminderResponse | None)
async def complete_reminder(reminder_id: str) -> ReminderResponse | None:
    item = reminder_service.complete(reminder_id)
    if item is None:
        return None
    return ReminderResponse(**item)
