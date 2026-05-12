from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from backend.app.core.config import get_settings
from backend.app.models.schemas import HealthResponse


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(time=datetime.now(UTC), version="1.0.0", model=settings.active_llm_model)
