from __future__ import annotations

from fastapi import APIRouter

from backend.app.models.schemas import AssistantResponse, ConfirmationDecisionRequest, ConfirmationDecisionResponse, TextAssistantRequest
from backend.app.services.assistant.service import assistant_service


router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/respond", response_model=AssistantResponse)
async def respond(request: TextAssistantRequest) -> AssistantResponse:
    data = await assistant_service.respond(
        text=request.text,
        session_id=request.session_id,
        include_audio=request.include_audio,
        screenshot_base64=request.screenshot_base64,
        include_screen_context=request.include_screen_context,
    )
    return AssistantResponse(**data)


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
