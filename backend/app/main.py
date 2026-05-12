from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.api.routes.actions import router as actions_router
from backend.app.api.routes.assistant import router as assistant_router
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.integrations import router as integrations_router
from backend.app.api.routes.memory import router as memory_router
from backend.app.api.routes.reminders import router as reminders_router
from backend.app.api.routes.vision import router as vision_router
from backend.app.api.routes.voice import router as voice_router
from backend.app.core.config import get_settings
from backend.app.core.logging import configure_logging, get_logger
from backend.app.repositories.sqlite import initialize_database
from backend.app.services.assistant.service import assistant_service
from backend.app.services.memory.service import memory_service
from backend.app.services.session.service import session_service
from backend.app.services.voice.interrupts import interrupt_registry
from backend.app.services.voice.service import voice_service
from backend.app.services.voice.wakeword import wake_word_service


configure_logging()
logger = get_logger(__name__)
settings = get_settings()


def _safe_voice_error_response(
    *,
    session_id: str,
    heard_text: str,
    message: str,
    error_type: str,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "text": message,
        "heard": heard_text,
        "audio": None,
        "audio_url": None,
        "follow_up": False,
        "confirmation_required": False,
        "confirmation_id": None,
        "action_preview": None,
        "memory_updated": False,
        "interrupted": False,
        "metadata": {"source": "error", "error_type": error_type},
    }


def _acknowledgement_text(raw: str, heard_text: str | None = None) -> str:
    candidate = (heard_text or raw or "").strip().lower()
    if candidate.startswith(("open ", "switch ", "close ", "quit ", "play ", "pause ")):
        return "Working on it."
    if candidate.startswith(("brief", "status", "what ", "search ", "summarize ", "read ")):
        return "One moment."
    return "Yes."

@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    memory_service.import_legacy_files()
    logger.info("Jarvis backend started with config: %s", settings.safe_summary())
    yield


app = FastAPI(title="Jarvis Backend", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/audio", StaticFiles(directory=str(settings.audio_path)), name="audio")

app.include_router(health_router)
app.include_router(assistant_router)
app.include_router(voice_router)
app.include_router(actions_router)
app.include_router(memory_router)
app.include_router(reminders_router)
app.include_router(vision_router)
app.include_router(integrations_router)

@app.get("/status")
async def status() -> dict[str, Any]:
    return {
        "ok": True,
        "config": settings.safe_summary(),
        "wake_word": wake_word_service.status(),
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    session = session_service.start_session("websocket")
    session_id = str(session["session_id"])
    try:
        while True:
            raw = await websocket.receive_text()
            heard_text = raw
            temp_audio_path = None
            try:
                if raw == "VOICE_INPUT":
                    try:
                        temp_audio_path = await asyncio.to_thread(voice_service.record_audio_until_silence)
                    except Exception as exc:
                        await websocket.send_json(
                            _safe_voice_error_response(
                                session_id=session_id,
                                heard_text="",
                                message=f"I could not access the microphone: {exc}",
                                error_type="microphone",
                            )
                        )
                        continue
                else:
                    with contextlib_suppress_json():
                        payload = json.loads(raw)
                        if isinstance(payload, dict):
                            if payload.get("type") == "VOICE_INPUT":
                                temp_audio_path = await asyncio.to_thread(voice_service.record_audio_until_silence)
                            else:
                                heard_text = str(payload.get("text", heard_text))
                            session_id = str(payload.get("session_id", session_id))

                if temp_audio_path is not None:
                    await websocket.send_json(
                        {
                            "event": "ack",
                            "session_id": session_id,
                            "heard": None,
                            "text": _acknowledgement_text(raw),
                            "mode": memory_service.get_power_mode(),
                        }
                    )
                    try:
                        heard_text = await asyncio.to_thread(voice_service.transcribe_file, temp_audio_path)
                    finally:
                        temp_audio_path.unlink(missing_ok=True)
                elif raw == "VOICE_INPUT" or heard_text != raw:
                    await websocket.send_json(
                        {
                            "event": "ack",
                            "session_id": session_id,
                            "heard": heard_text,
                            "text": _acknowledgement_text(raw, heard_text),
                            "mode": memory_service.get_power_mode(),
                        }
                    )

                response = await asyncio.wait_for(
                    assistant_service.respond(
                        text=heard_text or "I did not catch that.",
                        session_id=session_id,
                        include_audio=True,
                    ),
                    timeout=settings.assistant_response_timeout_seconds,
                )
                response["heard"] = heard_text
                response["interrupted"] = interrupt_registry.consume(session_id)
                response["audio"] = response.get("audio_url")
                await websocket.send_json(response)
            except asyncio.TimeoutError:
                logger.warning("Websocket assistant response timed out for session %s", session_id)
                await websocket.send_json(
                    _safe_voice_error_response(
                        session_id=session_id,
                        heard_text=heard_text,
                        message="I'm taking too long to respond. Please try again.",
                        error_type="timeout",
                    )
                )
            except Exception:
                logger.exception("Websocket assistant response failed for session %s", session_id)
                await websocket.send_json(
                    _safe_voice_error_response(
                        session_id=session_id,
                        heard_text=heard_text,
                        message="I hit a backend error while responding. Please try again.",
                        error_type="backend",
                    )
                )
    except WebSocketDisconnect:
        logger.info("Websocket disconnected")


class contextlib_suppress_json:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is json.JSONDecodeError
