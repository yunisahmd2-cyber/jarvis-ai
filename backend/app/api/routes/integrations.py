from __future__ import annotations

from fastapi import APIRouter, Query

from backend.app.core.config import get_settings
from backend.app.models.schemas import CalendarEventActionResponse, CalendarEventCreateRequest, MailDraftActionResponse, MailDraftCreateRequest
from backend.app.services.integrations.service import integration_service
from backend.app.services.productivity.service import productivity_service


router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/spotify")
async def spotify_status() -> dict[str, object]:
    settings = get_settings()
    return {
        "enabled": settings.spotify_enabled,
        "provider": "applescript",
        "status_endpoint": "/integrations/spotify/status",
    }


@router.get("/spotify/status")
async def spotify_status_read() -> dict[str, object]:
    settings = get_settings()
    if not settings.spotify_enabled:
        return {
            "enabled": False,
            "available": False,
            "running": False,
            "player_state": "disabled",
            "track": None,
            "artist": None,
            "album": None,
            "position_seconds": None,
            "message": "Spotify integration is disabled.",
        }
    return integration_service.spotify_status()


@router.get("/weather")
async def weather(place: str = Query(default="Muscat")) -> dict[str, object]:
    return await integration_service.get_weather(place)


@router.get("/news")
async def news(topic: str = Query(default="technology")) -> dict[str, object]:
    return await integration_service.get_news(topic)


@router.get("/search")
async def search(query: str = Query(default="")) -> dict[str, object]:
    return await integration_service.search_web(query)


@router.get("/browser/context")
async def browser_context() -> dict[str, object]:
    return integration_service.browser_context()


@router.get("/browser/awareness")
async def browser_awareness() -> dict[str, object]:
    return integration_service.page_awareness()


@router.get("/browser/page-summary")
async def browser_page_summary() -> dict[str, object]:
    return await integration_service.summarize_current_page()


@router.get("/system/active-app")
async def active_app() -> dict[str, object]:
    return integration_service.active_application()


@router.get("/system/active-app/intelligence")
async def active_app_intelligence() -> dict[str, object]:
    return integration_service.active_app_intelligence()


@router.get("/system/context")
async def context_brief() -> dict[str, object]:
    return await integration_service.contextual_brief()


@router.get("/system/report")
async def system_report() -> dict[str, object]:
    return integration_service.system_report()


@router.get("/system/status")
async def system_status() -> dict[str, object]:
    return integration_service.system_status()


@router.get("/system/capabilities")
async def system_capabilities() -> dict[str, object]:
    return integration_service.capability_report()


@router.get("/system/mode-profile")
async def system_mode_profile() -> dict[str, object]:
    return integration_service.mode_profile()


@router.get("/system/briefing")
async def system_briefing() -> dict[str, object]:
    return await integration_service.daily_briefing()


@router.get("/system/operator-briefing")
async def operator_briefing() -> dict[str, object]:
    return await integration_service.operator_briefing()


@router.get("/calendar/calendars")
async def list_calendars() -> dict[str, object]:
    return productivity_service.list_calendars()


@router.get("/calendar/events")
async def upcoming_calendar_events(
    days: int = Query(default=7, ge=1, le=30),
    limit: int = Query(default=8, ge=1, le=20),
    calendar_name: str | None = Query(default=None),
) -> CalendarEventActionResponse:
    return CalendarEventActionResponse(
        **productivity_service.upcoming_calendar_events(
            calendar_name=calendar_name,
            days=days,
            limit=limit,
        )
    )


@router.post("/calendar/events")
async def create_calendar_event(request: CalendarEventCreateRequest) -> CalendarEventActionResponse:
    return CalendarEventActionResponse(
        **productivity_service.create_calendar_event(
            title=request.title,
            starts_at=request.starts_at,
            ends_at=request.ends_at,
            calendar_name=request.calendar_name,
            notes=request.notes,
            location=request.location,
            recurrence=request.recurrence,
        )
    )


@router.post("/mail/drafts")
async def create_mail_draft(request: MailDraftCreateRequest) -> MailDraftActionResponse:
    return MailDraftActionResponse(
        **productivity_service.create_mail_draft(
            to=request.to,
            subject=request.subject,
            body=request.body,
            cc=request.cc,
        )
    )
