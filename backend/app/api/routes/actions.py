from __future__ import annotations

from fastapi import APIRouter

from backend.app.models.schemas import ActionPreviewRequest, ActionPreviewResponse, ConfirmationDecisionRequest, ConfirmationDecisionResponse
from backend.app.services.actions.service import action_service
from backend.app.services.assistant.service import assistant_service
from backend.app.services.confirmations.service import confirmation_service


router = APIRouter(prefix="/actions", tags=["actions"])


@router.post("/preview", response_model=ActionPreviewResponse)
async def preview_action(request: ActionPreviewRequest) -> ActionPreviewResponse:
    preview = action_service.preview(request.action, request.target, request.params)
    confirmation_id = None
    if preview["allowed"] and preview["requires_confirmation"]:
        pending = confirmation_service.create(preview)
        confirmation_id = str(pending["confirmation_id"])
    return ActionPreviewResponse(
        allowed=bool(preview["allowed"]),
        requires_confirmation=bool(preview["requires_confirmation"]),
        action=str(preview["action"]),
        preview=preview,
        confirmation_id=confirmation_id,
    )


@router.post("/confirm", response_model=ConfirmationDecisionResponse)
async def confirm_action(request: ConfirmationDecisionRequest) -> ConfirmationDecisionResponse:
    result = assistant_service.confirm_action(request.confirmation_id)
    return ConfirmationDecisionResponse(
        confirmation_id=request.confirmation_id,
        status=result.get("status", "confirmed"),
        result=result,
    )


@router.post("/cancel", response_model=ConfirmationDecisionResponse)
async def cancel_action(request: ConfirmationDecisionRequest) -> ConfirmationDecisionResponse:
    result = assistant_service.cancel_action(request.confirmation_id)
    return ConfirmationDecisionResponse(
        confirmation_id=request.confirmation_id,
        status=result.get("status", "cancelled"),
        result=result,
    )
