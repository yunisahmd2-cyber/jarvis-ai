from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    ok: bool = True
    service: str = "jarvis-backend"
    time: datetime
    version: str
    model: str


class SessionStartRequest(BaseModel):
    session_name: str | None = None


class SessionResponse(BaseModel):
    session_id: str
    status: Literal["active", "ended"]
    created_at: datetime
    ended_at: datetime | None = None


class TextAssistantRequest(BaseModel):
    text: str
    session_id: str | None = None
    include_audio: bool = False
    include_screen_context: bool = False
    screenshot_base64: str | None = None


class AssistantResponse(BaseModel):
    session_id: str
    text: str
    audio_url: str | None = None
    follow_up: bool = False
    confirmation_required: bool = False
    confirmation_id: str | None = None
    action_preview: dict[str, Any] | None = None
    memory_updated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class VoiceTtsRequest(BaseModel):
    text: str
    voice_name: str | None = None


class VoiceTranscribeRequest(BaseModel):
    audio_base64: str
    file_suffix: str = ".wav"


class VoiceRespondRequest(BaseModel):
    session_id: str | None = None


class VoiceTtsResponse(BaseModel):
    audio_url: str | None = None
    provider: str


class VoiceInterruptRequest(BaseModel):
    session_id: str | None = None


class VoiceInterruptResponse(BaseModel):
    interrupted: bool
    session_id: str | None = None


class MemoryStoreRequest(BaseModel):
    key: str
    value: str
    category: str = "preference"


class MemoryItem(BaseModel):
    key: str
    value: str
    category: str
    updated_at: datetime


class MemorySearchResponse(BaseModel):
    results: list[MemoryItem]


class PreferenceUpdateRequest(BaseModel):
    key: str
    value: str


class ActionPreviewRequest(BaseModel):
    action: str
    target: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None


class ActionPreviewResponse(BaseModel):
    allowed: bool
    requires_confirmation: bool
    action: str
    preview: dict[str, Any]
    confirmation_id: str | None = None


class ConfirmationDecisionRequest(BaseModel):
    confirmation_id: str


class ConfirmationDecisionResponse(BaseModel):
    confirmation_id: str
    status: Literal["confirmed", "confirmed_unverified", "confirmed_failed", "cancelled", "not_found"]
    result: dict[str, Any] | None = None


class ScreenshotAnalysisRequest(BaseModel):
    session_id: str | None = None
    screenshot_base64: str
    prompt: str | None = None


class ScreenshotAnalysisResponse(BaseModel):
    session_id: str | None = None
    summary: str
    ocr_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReminderCreateRequest(BaseModel):
    title: str
    due_at: datetime
    session_id: str | None = None


class ReminderResponse(BaseModel):
    id: str
    title: str
    due_at: datetime
    created_at: datetime
    completed_at: datetime | None = None
    session_id: str | None = None


class WakeWordStatusResponse(BaseModel):
    wake_word: str
    desired_enabled: bool
    effective_enabled: bool
    power_mode: str
    listener_active: bool
    load_paused: bool
    reason: str


class WakeWordToggleRequest(BaseModel):
    enabled: bool


class CalendarEventCreateRequest(BaseModel):
    title: str
    starts_at: datetime
    ends_at: datetime
    calendar_name: str | None = None
    notes: str | None = None
    location: str | None = None
    recurrence: str | None = None


class CalendarEventActionResponse(BaseModel):
    ok: bool
    success: bool
    verified: bool
    status: str
    attempted: bool
    message: str
    event_id: str | None = None
    title: str | None = None
    calendar_name: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    recurrence: str | None = None
    notes: str | None = None
    location: str | None = None
    events: list[dict[str, Any]] | None = None


class MailDraftCreateRequest(BaseModel):
    to: str
    subject: str
    body: str
    cc: list[str] = Field(default_factory=list)


class MailDraftActionResponse(BaseModel):
    ok: bool
    success: bool
    verified: bool
    status: str
    attempted: bool
    message: str
    mail_id: str | None = None
    to: str | None = None
    subject: str | None = None
    cc: list[str] = Field(default_factory=list)
