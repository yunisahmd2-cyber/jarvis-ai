from __future__ import annotations

import base64
import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException

from backend.app.core.logging import get_logger
from backend.app.models.schemas import (
    AssistantResponse,
    SessionResponse,
    SessionStartRequest,
    VoiceInterruptRequest,
    VoiceInterruptResponse,
    VoiceRespondRequest,
    VoiceTranscribeRequest,
    VoiceTtsRequest,
    VoiceTtsResponse,
)
from backend.app.services.assistant.service import assistant_service
from backend.app.services.session.service import session_service
from backend.app.services.tts.service import tts_service
from backend.app.services.voice.interrupts import interrupt_registry
from backend.app.services.voice.service import voice_service
from backend.app.services.voice.wakeword import wake_word_service
from backend.app.core.config import get_settings
from backend.app.models.schemas import WakeWordStatusResponse, WakeWordToggleRequest


router = APIRouter(prefix="/voice", tags=["voice"])
settings = get_settings()
logger = get_logger(__name__)


def _voice_error_response(session_id: str | None, heard: str, message: str, error_type: str) -> AssistantResponse:
    return AssistantResponse(
        session_id=session_id or "",
        text=message,
        audio_url=None,
        follow_up=False,
        confirmation_required=False,
        confirmation_id=None,
        action_preview=None,
        memory_updated=False,
        metadata={
            "source": "error",
            "error_type": error_type,
            "heard": heard,
        },
    )


@router.post("/transcribe")
async def transcribe(request: VoiceTranscribeRequest) -> dict[str, str]:
    suffix = request.file_suffix if request.file_suffix.startswith(".") else f".{request.file_suffix}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        temp_path = Path(tmp.name)
        temp_path.write_bytes(base64.b64decode(request.audio_base64))
    try:
        text = await asyncio.to_thread(voice_service.transcribe_file, temp_path)
    except Exception as exc:  # pragma: no cover
        logger.exception("Voice transcribe failed")
        raise HTTPException(status_code=500, detail="Transcription failed. Please try again.") from exc
    finally:
        temp_path.unlink(missing_ok=True)
    return {"text": text}


@router.post("/respond", response_model=AssistantResponse)
async def voice_respond(request: VoiceRespondRequest | None = None) -> AssistantResponse:
    session_id = request.session_id if request else None
    heard = ""
    try:
        temp_path = await asyncio.to_thread(voice_service.record_audio_until_silence)
        if temp_path is None:
            heard = ""
        else:
            try:
                heard = await asyncio.to_thread(voice_service.transcribe_file, temp_path)
            finally:
                temp_path.unlink(missing_ok=True)
        data = await asyncio.wait_for(
            assistant_service.respond(
                text=heard or "I did not catch that.",
                session_id=session_id,
                include_audio=True,
            ),
            timeout=settings.assistant_response_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        logger.warning("Voice respond timed out for session %s", session_id)
        return _voice_error_response(
            session_id,
            heard,
            "I'm taking too long to respond. Please try again.",
            "timeout",
        )
    except RuntimeError as exc:
        if "recording is already active" in str(exc).lower():
            return _voice_error_response(
                session_id,
                heard,
                "I'm already handling voice input. Please wait a moment.",
                "microphone_busy",
            )
        logger.exception("Voice respond failed for session %s", session_id)
        return _voice_error_response(
            session_id,
            heard,
            "I hit a voice pipeline error. Please try again.",
            "voice_pipeline",
        )
    except Exception:
        logger.exception("Voice respond failed for session %s", session_id)
        return _voice_error_response(
            session_id,
            heard,
            "I hit a voice pipeline error. Please try again.",
            "voice_pipeline",
        )

    data.setdefault("metadata", {})["heard"] = heard
    return AssistantResponse(**data)


@router.post("/tts", response_model=VoiceTtsResponse)
async def tts(request: VoiceTtsRequest) -> VoiceTtsResponse:
    result = await tts_service.synthesize(request.text, request.voice_name)
    return VoiceTtsResponse(audio_url=result["audio_url"], provider=str(result["provider"]))


@router.post("/interrupt", response_model=VoiceInterruptResponse)
async def interrupt(request: VoiceInterruptRequest) -> VoiceInterruptResponse:
    if request.session_id:
        interrupt_registry.interrupt(request.session_id)
    return VoiceInterruptResponse(interrupted=True, session_id=request.session_id)


@router.post("/session/start", response_model=SessionResponse)
async def start_session(request: SessionStartRequest) -> SessionResponse:
    data = session_service.start_session(request.session_name)
    return SessionResponse(**data)


@router.post("/session/end", response_model=SessionResponse)
async def end_session(request: VoiceInterruptRequest) -> SessionResponse:
    if not request.session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    data = session_service.end_session(request.session_id)
    return SessionResponse(**data)


@router.get("/wake-word/status", response_model=WakeWordStatusResponse)
async def wake_word_status() -> WakeWordStatusResponse:
    return WakeWordStatusResponse(**wake_word_service.status())


@router.post("/wake-word/toggle", response_model=WakeWordStatusResponse)
async def toggle_wake_word(request: WakeWordToggleRequest) -> WakeWordStatusResponse:
    return WakeWordStatusResponse(**wake_word_service.set_enabled(request.enabled))
