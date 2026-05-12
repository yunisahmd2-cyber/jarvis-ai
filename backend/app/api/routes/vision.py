from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter

from backend.app.models.schemas import ScreenshotAnalysisRequest, ScreenshotAnalysisResponse
from backend.app.services.memory.service import memory_service
from backend.app.services.vision.service import vision_service


router = APIRouter(prefix="/vision", tags=["vision"])


@router.post("/analyze", response_model=ScreenshotAnalysisResponse)
async def analyze(request: ScreenshotAnalysisRequest) -> ScreenshotAnalysisResponse:
    data = vision_service.analyze_screenshot(request.screenshot_base64, request.prompt)
    if request.session_id:
        context_payload = {
            "summary": data.get("summary"),
            "ocr_text": (data.get("ocr_text") or "")[:1500],
            "metadata": {
                "analysis_level": data.get("metadata", {}).get("analysis_level"),
                "ocr_state": data.get("metadata", {}).get("ocr_state"),
                "dimensions": data.get("metadata", {}).get("dimensions"),
                "mode": data.get("metadata", {}).get("mode"),
                "captured_at": datetime.now(UTC).isoformat(),
            },
        }
        memory_service.store_memory(
            key=f"session:{request.session_id}:last_vision_context",
            value=json.dumps(context_payload, ensure_ascii=False),
            category="session_context",
        )
    return ScreenshotAnalysisResponse(session_id=request.session_id, **data)
