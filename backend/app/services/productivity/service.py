from __future__ import annotations

import platform
import subprocess
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.core.logging import get_logger


logger = get_logger(__name__)

SPECIAL_CALENDARS = {"Birthdays", "My birthday", "Siri Suggestions", "Scheduled Reminders"}


class ProductivityService:
    def _run(self, command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        logger.info("Running productivity command: %s", command)
        return subprocess.run(command, check=True, capture_output=True, text=True, input=input_text)

    def _run_osascript(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        if platform.system() != "Darwin":
            raise RuntimeError("Calendar and Mail controls are currently supported only on macOS")
        return self._run(["osascript", "-", *args], input_text=script)

    def _success(self, message: str, **extra: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": message,
            **extra,
        }

    def _attempted(self, message: str, **extra: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "success": False,
            "verified": False,
            "status": "attempted_unverified",
            "attempted": True,
            "message": message,
            **extra,
        }

    def _failure(self, message: str, **extra: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "success": False,
            "verified": False,
            "status": "failed",
            "attempted": False,
            "message": message,
            **extra,
        }

    def _wait_for(self, predicate: Any, *, timeout: float, interval: float = 0.2) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()

    def list_calendars(self) -> dict[str, Any]:
        try:
            proc = self._run_osascript(
                """
                tell application "Calendar"
                  set calendarNames to name of every calendar
                  set AppleScript's text item delimiters to linefeed
                  set outputText to calendarNames as text
                  set AppleScript's text item delimiters to ""
                  return outputText
                end tell
                """
            )
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            return self._failure("I could not read your Calendar calendars.", error=str(exc))

        calendars = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        preferred = self._pick_default_calendar(calendars)
        return self._success(
            "Calendar access is available.",
            calendars=calendars,
            default_calendar=preferred,
        )

    def create_calendar_event(
        self,
        *,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        calendar_name: str | None = None,
        notes: str | None = None,
        location: str | None = None,
        recurrence: str | None = None,
    ) -> dict[str, Any]:
        title = title.strip()
        if not title:
            return self._failure("I need an event title before I can create a calendar item.")
        if ends_at <= starts_at:
            return self._failure("The event end time must be after the start time.")

        calendar_result = self.list_calendars()
        if not calendar_result.get("success"):
            return calendar_result

        resolved_calendar = self._resolve_calendar_name(calendar_name, list(calendar_result.get("calendars") or []))
        if not resolved_calendar:
            return self._failure("I could not find the requested calendar.", requested_calendar=calendar_name)

        start_local = starts_at.astimezone()
        end_local = ends_at.astimezone()
        script = """
        on run argv
          set calendarName to item 1 of argv
          set eventSummary to item 2 of argv
          set startText to item 3 of argv
          set endText to item 4 of argv
          set notesText to item 5 of argv
          set locationText to item 6 of argv
          set recurrenceText to item 7 of argv
          set startDate to date startText
          set endDate to date endText

          tell application "Calendar"
            if not (exists calendar calendarName) then
              return "ERROR|calendar_missing"
            end if
            tell calendar calendarName
              set newEvent to make new event with properties {summary:eventSummary, start date:startDate, end date:endDate}
              if notesText is not "" then set description of newEvent to notesText
              if locationText is not "" then set location of newEvent to locationText
              if recurrenceText is not "" then set recurrence of newEvent to recurrenceText
              return "OK|" & (uid of newEvent)
            end tell
          end tell
        end run
        """

        try:
            proc = self._run_osascript(
                script,
                resolved_calendar,
                title,
                self._to_applescript_date(start_local),
                self._to_applescript_date(end_local),
                notes or "",
                location or "",
                recurrence or "",
            )
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            return self._failure("I could not create the calendar event.", error=str(exc), calendar_name=resolved_calendar)

        output = proc.stdout.strip()
        if not output.startswith("OK|"):
            return self._failure("I could not create the calendar event.", raw_output=output, calendar_name=resolved_calendar)

        event_id = output.split("|", 1)[1].strip()
        verified = self._wait_for(lambda: self._calendar_event_exists(resolved_calendar, event_id), timeout=2.0)
        payload = {
            "event_id": event_id,
            "title": title,
            "calendar_name": resolved_calendar,
            "starts_at": start_local.astimezone(UTC).isoformat(),
            "ends_at": end_local.astimezone(UTC).isoformat(),
            "recurrence": recurrence,
            "notes": notes,
            "location": location,
        }
        if verified:
            return self._success(
                f"Calendar event created: {title} on {start_local.strftime('%A at %I:%M %p')}.",
                **payload,
            )
        return self._attempted(
            f"I created {title} in Calendar, but I could not confirm the event saved correctly.",
            **payload,
        )

    def upcoming_calendar_events(
        self,
        *,
        calendar_name: str | None = None,
        days: int = 7,
        limit: int = 8,
    ) -> dict[str, Any]:
        calendar_result = self.list_calendars()
        if not calendar_result.get("success"):
            return calendar_result
        resolved_calendar = self._resolve_calendar_name(calendar_name, list(calendar_result.get("calendars") or []))
        if calendar_name and not resolved_calendar:
            return self._failure("I could not find the requested calendar.", requested_calendar=calendar_name)

        start_local = datetime.now().astimezone()
        end_local = start_local + timedelta(days=max(1, days))
        script = """
        on run argv
          set calendarName to item 1 of argv
          set startText to item 2 of argv
          set endText to item 3 of argv
          set limitText to item 4 of argv
          set startDate to date startText
          set endDate to date endText
          set maxItems to limitText as integer
          set outputLines to {}

          tell application "Calendar"
            if calendarName is "" then
              set selectedCalendars to every calendar
            else if exists calendar calendarName then
              set selectedCalendars to {calendar calendarName}
            else
              return "ERROR|calendar_missing"
            end if

            repeat with cal in selectedCalendars
              set eventList to (every event of cal whose start date ≥ startDate and start date ≤ endDate)
              repeat with ev in eventList
                set end of outputLines to ((uid of ev) & tab & (summary of ev) & tab & ((start date of ev) as string) & tab & ((end date of ev) as string) & tab & (name of cal))
                if (count of outputLines) ≥ maxItems then exit repeat
              end repeat
              if (count of outputLines) ≥ maxItems then exit repeat
            end repeat
          end tell

          set AppleScript's text item delimiters to linefeed
          set outputText to outputLines as text
          set AppleScript's text item delimiters to ""
          return outputText
        end run
        """

        try:
            proc = self._run_osascript(
                script,
                resolved_calendar or "",
                self._to_applescript_date(start_local),
                self._to_applescript_date(end_local),
                str(limit),
            )
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            return self._failure("I could not read your upcoming calendar events.", error=str(exc))

        if proc.stdout.strip().startswith("ERROR|"):
            return self._failure("I could not find the requested calendar.", requested_calendar=calendar_name)

        events = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 5:
                continue
            events.append(
                {
                    "event_id": parts[0].strip(),
                    "title": parts[1].strip(),
                    "starts_at": parts[2].strip(),
                    "ends_at": parts[3].strip(),
                    "calendar_name": parts[4].strip(),
                }
            )

        if not events:
            target_calendar = resolved_calendar or "your calendars"
            return self._success(
                f"You have no upcoming events in {target_calendar}.",
                events=[],
                calendar_name=resolved_calendar,
            )

        summary = "; ".join(
            f"{item['title']} on {item['starts_at']}" for item in events[: min(3, len(events))]
        )
        return self._success(
            f"Upcoming calendar events: {summary}.",
            events=events,
            calendar_name=resolved_calendar,
        )

    def create_mail_draft(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        cc: list[str] | None = None,
    ) -> dict[str, Any]:
        to = to.strip()
        subject = subject.strip() or "(No subject)"
        body = body.rstrip()
        if "@" not in to:
            return self._failure("I need a valid recipient email address before I can draft the email.")

        existing_count = self._mail_draft_count(to, subject)
        script = """
        on splitText(sourceText, delimiterText)
          if sourceText is "" then return {}
          set AppleScript's text item delimiters to delimiterText
          set pieces to every text item of sourceText
          set AppleScript's text item delimiters to ""
          return pieces
        end splitText

        on run argv
          set toAddress to item 1 of argv
          set subjectText to item 2 of argv
          set bodyText to item 3 of argv
          set ccText to item 4 of argv

          tell application "Mail"
            set draftMessage to make new outgoing message with properties {subject:subjectText, content:bodyText & return & return, visible:false}
            tell draftMessage
              make new to recipient at end of to recipients with properties {address:toAddress}
              if ccText is not "" then
                set ccAddresses to my splitText(ccText, "|||")
                repeat with ccAddress in ccAddresses
                  if (ccAddress as text) is not "" then
                    make new cc recipient at end of cc recipients with properties {address:(ccAddress as text)}
                  end if
                end repeat
              end if
              save
              return "OK|" & (id as string)
            end tell
          end tell
        end run
        """

        try:
            proc = self._run_osascript(
                script,
                to,
                subject,
                body,
                "|||".join(cc or []),
            )
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            return self._failure("I could not create the Mail draft.", error=str(exc), to=to, subject=subject)

        output = proc.stdout.strip()
        if not output.startswith("OK|"):
            return self._failure("I could not create the Mail draft.", raw_output=output, to=to, subject=subject)

        message_id = output.split("|", 1)[1].strip()
        verified = self._wait_for(lambda: self._mail_draft_count(to, subject) > existing_count, timeout=2.0)
        payload = {
            "mail_id": message_id,
            "to": to,
            "subject": subject,
            "cc": cc or [],
        }
        if verified:
            return self._success(f"Mail draft created for {to}.", **payload)
        return self._attempted(
            f"I created a Mail draft for {to}, but I could not confirm that it saved correctly.",
            **payload,
        )

    def _calendar_event_exists(self, calendar_name: str, event_id: str) -> bool:
        try:
            proc = self._run_osascript(
                """
                on run argv
                  set calendarName to item 1 of argv
                  set eventId to item 2 of argv
                  tell application "Calendar"
                    if not (exists calendar calendarName) then return "false"
                    tell calendar calendarName
                      return (count of (every event whose uid is eventId)) as string
                    end tell
                  end tell
                end run
                """,
                calendar_name,
                event_id,
            )
        except (RuntimeError, subprocess.CalledProcessError):
            return False
        return proc.stdout.strip() not in {"", "0", "false"}

    def _mail_draft_count(self, recipient: str, subject: str) -> int:
        try:
            proc = self._run_osascript(
                """
                on run argv
                  set subjectText to item 2 of argv
                  tell application "Mail"
                    return (count of (every outgoing message whose subject is subjectText)) as string
                  end tell
                end run
                """,
                recipient,
                subject,
            )
        except (RuntimeError, subprocess.CalledProcessError):
            return 0
        text = proc.stdout.strip()
        return int(text) if text.isdigit() else 0

    def _pick_default_calendar(self, calendars: list[str]) -> str | None:
        if not calendars:
            return None
        for preferred in ("Home", "Work"):
            for item in calendars:
                if item.lower() == preferred.lower():
                    return item
        for item in calendars:
            if item not in SPECIAL_CALENDARS:
                return item
        return calendars[0]

    def _resolve_calendar_name(self, requested: str | None, calendars: list[str]) -> str | None:
        if not calendars:
            return None
        if requested:
            normalized = requested.strip().lower()
            for item in calendars:
                if item.lower() == normalized:
                    return item
        return self._pick_default_calendar(calendars)

    def _to_applescript_date(self, value: datetime) -> str:
        local = value.astimezone()
        return local.strftime("%A, %B %d, %Y at %I:%M:%S %p")


productivity_service = ProductivityService()
