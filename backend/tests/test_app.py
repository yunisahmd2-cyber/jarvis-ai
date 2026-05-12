from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings, reset_settings_cache
from backend.app.services.actions.service import action_service
from backend.app.services.integrations.service import integration_service
from backend.app.services.llm.ollama import ollama_service
from backend.app.services.memory.service import memory_service
from backend.app.services.reminders.service import reminder_service
from backend.app.services.tts.service import tts_service
from backend.app.services.voice.service import voice_service


def test_health_route(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["model"] == "llama3.1:8b"


def test_config_rejects_non_llama_model():
    try:
        Settings(ollama_model="mistral")
    except ValidationError:
        return
    raise AssertionError("Expected a validation error for a non-llama model")


def test_voice_silence_default_is_low_latency():
    assert Settings().speech_silence_seconds == 0.62


def test_stt_defaults_to_english_language_hint(monkeypatch):
    class FakeSegment:
        def __init__(self, text: str):
            self.text = text

    captured: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, path: str, **kwargs):
            captured["path"] = path
            captured["kwargs"] = kwargs
            return [FakeSegment("open Spotify")], {"language": "en"}

    monkeypatch.setattr("backend.app.services.voice.service.voice_service._ensure_model", lambda: FakeModel())
    monkeypatch.setattr("backend.app.services.memory.service.memory_service.get_power_mode", lambda: "basic")

    text = voice_service.transcribe_file(Path("/tmp/test.wav"))
    assert text == "open Spotify"
    kwargs = captured["kwargs"]
    assert kwargs["language"] == "en"
    assert kwargs["vad_filter"] is True
    assert kwargs["condition_on_previous_text"] is False


def test_stt_can_use_auto_detect_only_in_advanced_mode(monkeypatch):
    monkeypatch.setenv("STT_AUTO_DETECT_LANGUAGE", "true")
    reset_settings_cache()

    class FakeSegment:
        def __init__(self, text: str):
            self.text = text

    captured: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, path: str, **kwargs):
            captured["kwargs"] = kwargs
            return [FakeSegment("open Safari")], {"language": "en"}

    monkeypatch.setattr("backend.app.services.voice.service.voice_service._ensure_model", lambda: FakeModel())
    monkeypatch.setattr("backend.app.services.memory.service.memory_service.get_power_mode", lambda: "advanced")

    voice_service.transcribe_file(Path("/tmp/test.wav"))
    kwargs = captured["kwargs"]
    assert "language" not in kwargs

    monkeypatch.delenv("STT_AUTO_DETECT_LANGUAGE", raising=False)
    reset_settings_cache()


def test_voice_recording_lock_rejects_overlap():
    acquired = voice_service._recording_lock.acquire(blocking=False)
    assert acquired is True
    try:
        with pytest.raises(RuntimeError, match="already active"):
            voice_service.record_audio_until_silence()
    finally:
        voice_service._recording_lock.release()


def test_voice_recording_lock_releases_after_failure(monkeypatch):
    def fail_recording():
        raise RuntimeError("device vanished")

    monkeypatch.setattr(voice_service, "_record_audio_until_silence_locked", fail_recording)
    with pytest.raises(RuntimeError, match="device vanished"):
        voice_service.record_audio_until_silence()

    acquired = voice_service._recording_lock.acquire(blocking=False)
    assert acquired is True
    voice_service._recording_lock.release()


def test_edge_tts_path_uses_free_edge_provider_without_network(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class FakeCommunicate:
        def __init__(self, *, text: str, voice: str):
            captured["text"] = text
            captured["voice"] = voice

        async def save(self, path: str) -> None:
            captured["path"] = path
            Path(path).write_bytes(b"fake mp3")

    class FakeEdgeTts:
        Communicate = FakeCommunicate

    monkeypatch.setenv("AUDIO_DIR", str(tmp_path))
    monkeypatch.setattr("backend.app.services.tts.service.edge_tts", FakeEdgeTts)
    monkeypatch.setattr("backend.app.services.tts.service.memory_service.get_preference", lambda *args, **kwargs: "realistic")
    reset_settings_cache()

    result = asyncio.run(tts_service.synthesize("Opening Spotify."))

    assert result["provider"] == "edge"
    assert result["audio_url"].endswith(".mp3")
    assert captured["voice"] == Settings().edge_voice_name
    assert Path(str(captured["path"])).exists()
    reset_settings_cache()


def test_ollama_generate_returns_friendly_message_when_unreachable(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr("backend.app.services.llm.ollama.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("backend.app.services.llm.ollama.shutil.which", lambda command: None)

    import asyncio

    text = asyncio.run(
        ollama_service.generate(
            prompt="hello",
            system_prompt="You are Jarvis.",
            history=[],
            mode="basic",
        )
    )
    assert "can't reach the local ollama service" in text.lower()


def test_ollama_basic_mode_keeps_prompt_context_small(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": "brief local reply"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            captured["url"] = url
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr("backend.app.services.llm.ollama.httpx.AsyncClient", FakeAsyncClient)
    history = [{"role": "user", "content": f"message {index}"} for index in range(10)]

    text = asyncio.run(
        ollama_service.generate(
            prompt="hello",
            system_prompt="You are Jarvis.",
            history=history,
            mode="basic",
        )
    )

    payload = captured["payload"]
    assert text == "brief local reply"
    assert payload["options"]["num_ctx"] == 2048
    assert "message 5" not in payload["prompt"]
    assert "message 6" in payload["prompt"]


def test_memory_routes(client):
    store_response = client.post("/memory/store", json={"key": "favorite_editor", "value": "VS Code", "category": "preference"})
    assert store_response.status_code == 200

    search_response = client.get("/memory/search", params={"q": "editor"})
    assert search_response.status_code == 200
    results = search_response.json()["results"]
    assert any(item["key"] == "favorite_editor" for item in results)


def test_missing_runtime_seed_json_files_are_safe(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_SEED_FILE", str(tmp_path / "missing-memory.json"))
    monkeypatch.setenv("NOTES_SEED_FILE", str(tmp_path / "missing-notes.json"))
    monkeypatch.setenv("STATUS_SEED_FILE", str(tmp_path / "missing-status.json"))
    from backend.app.core.config import reset_settings_cache

    reset_settings_cache()
    memory_service.import_legacy_files()

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    reset_settings_cache()


def test_action_policy_blocks_unknown_apps():
    preview = action_service.preview("open_app", "Notion")
    assert preview["allowed"] is False
    assert preview["requires_confirmation"] is False


def test_action_policy_allows_common_aliases():
    preview = action_service.preview("open_app", "chrome")
    assert preview["allowed"] is True


def test_action_policy_allows_discovered_installed_apps(monkeypatch):
    monkeypatch.setattr(action_service, "installed_apps", {"chatgpt": "ChatGPT"})
    assert action_service._resolve_allowed_app("the ChatGPT") == "ChatGPT"
    preview = action_service.preview("open_app", "ChatGPT")
    assert preview["allowed"] is True


def test_open_app_reports_unverified_launch(client, monkeypatch):
    monkeypatch.setattr("backend.app.services.actions.service.action_service._is_app_installed", lambda app: True)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._run", lambda command: None)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._run_applescript", lambda script: {"ok": True})
    monkeypatch.setattr("backend.app.services.actions.service.action_service._is_app_running", lambda app: False)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._frontmost_application", lambda: "Jarvis")
    monkeypatch.setattr("backend.app.services.actions.service.action_service._wait_for", lambda predicate, timeout, interval=0.2: False)

    result = action_service.execute("open_app", "Spotify")
    assert result["success"] is False
    assert result["attempted"] is True
    assert result["status"] == "attempted_unverified"
    assert "could not confirm" in result["message"].lower()


def test_open_app_reports_verified_launch(monkeypatch):
    monkeypatch.setattr("backend.app.services.actions.service.action_service._is_app_installed", lambda app: True)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._run", lambda command: None)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._run_applescript", lambda script: {"ok": True})
    monkeypatch.setattr("backend.app.services.actions.service.action_service._is_app_running", lambda app: True)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._frontmost_application", lambda: "Spotify")
    monkeypatch.setattr("backend.app.services.actions.service.action_service._wait_for", lambda predicate, timeout, interval=0.2: True)

    result = action_service.execute("open_app", "spotify")
    assert result["success"] is True
    assert result["verified"] is True
    assert result["status"] == "verified"
    assert result["app"] == "Spotify"
    assert result["message"] in {"Spotify is open.", "Spotify is now frontmost.", "Spotify is already open."}


def test_open_app_refreshes_installed_app_discovery(monkeypatch):
    monkeypatch.setattr(action_service, "installed_apps", {})
    monkeypatch.setattr(
        "backend.app.services.actions.service.action_service._discover_installed_apps",
        lambda: {"spotify": "Spotify"},
    )
    monkeypatch.setattr("backend.app.services.actions.service.action_service._is_app_installed", lambda app: True)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._run", lambda command: None)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._run_applescript", lambda script: {"ok": True})
    monkeypatch.setattr("backend.app.services.actions.service.action_service._is_app_running", lambda app: True)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._frontmost_application", lambda: "Spotify")
    monkeypatch.setattr("backend.app.services.actions.service.action_service._wait_for", lambda predicate, timeout, interval=0.2: True)

    result = action_service.execute("open_app", "Spotify")
    assert result["status"] == "verified"
    assert result["app"] == "Spotify"


def test_open_app_uses_native_open_then_activate_sequence(monkeypatch):
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr("backend.app.services.actions.service.action_service._is_app_installed", lambda app: True)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._is_app_running", lambda app: True)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._frontmost_application", lambda: "Spotify")
    monkeypatch.setattr("backend.app.services.actions.service.action_service._wait_for", lambda predicate, timeout, interval=0.2: True)

    def fake_run(command):
        calls.append(("run", command))
        return None

    def fake_activate(app_name):
        calls.append(("activate", app_name))
        return True

    def fake_bring_to_front(app_name, *, attempts=2, timeout=2.0):
        calls.append(("bring_to_front", app_name))
        return True

    states = iter([False, True, True])
    monkeypatch.setattr("backend.app.services.actions.service.action_service._run", fake_run)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._activate_application", fake_activate)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._bring_app_to_front", fake_bring_to_front)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._is_app_running", lambda app: next(states, True))

    result = action_service.execute("open_app", "Spotify")
    assert result["status"] == "verified"
    assert ("run", ["open", "-a", "Spotify"]) in calls
    assert ("activate", "Spotify") in calls
    assert ("bring_to_front", "Spotify") in calls


def test_open_app_reports_running_but_not_frontmost(monkeypatch):
    monkeypatch.setattr("backend.app.services.actions.service.action_service._is_app_installed", lambda app: True)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._run", lambda command: None)
    monkeypatch.setattr("backend.app.services.actions.service.action_service._run_applescript", lambda script: {"ok": True})
    states = iter([False, True])
    monkeypatch.setattr("backend.app.services.actions.service.action_service._is_app_running", lambda app: next(states, True))
    monkeypatch.setattr("backend.app.services.actions.service.action_service._frontmost_application", lambda: "Jarvis")
    monkeypatch.setattr("backend.app.services.actions.service.action_service._wait_for", lambda predicate, timeout, interval=0.2: False)

    result = action_service.execute("open_app", "Spotify")
    assert result["success"] is False
    assert result["attempted"] is True
    assert result["status"] == "attempted_unverified"
    assert "came to the front" in result["message"]


def test_app_running_check_handles_osascript_failure(monkeypatch):
    def raise_called_process_error(command):
        raise __import__("subprocess").CalledProcessError(returncode=1, cmd=command)

    monkeypatch.setattr("backend.app.services.actions.service.action_service._run", raise_called_process_error)
    assert action_service._is_app_running("Spotify") is False
    assert action_service._frontmost_application() is None


def test_clipboard_write_verifies_contents(monkeypatch):
    class FakeProc:
        def __init__(self, stdout: str):
            self.stdout = stdout

    monkeypatch.setattr("backend.app.services.actions.service.action_service._run_applescript", lambda script: {"ok": True})
    monkeypatch.setattr("backend.app.services.actions.service.action_service._run", lambda command: FakeProc("hello"))

    result = action_service.execute("clipboard_write", "hello")
    assert result["success"] is True
    assert result["verified"] is True


def test_confirmation_flow(client):
    response = client.post("/assistant/respond", json={"text": "close app safari", "session_id": None, "include_audio": False})
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is True
    assert data["confirmation_id"]

    cancel_response = client.post("/assistant/cancel", json={"confirmation_id": data["confirmation_id"]})
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"


def test_confirm_action_reports_failed_status(client, monkeypatch):
    response = client.post("/assistant/respond", json={"text": "close app safari", "session_id": None, "include_audio": False})
    confirmation_id = response.json()["confirmation_id"]

    monkeypatch.setattr(
        "backend.app.services.assistant.service.action_service.execute",
        lambda action, target=None, params=None: {
            "ok": False,
            "success": False,
            "verified": False,
            "status": "failed",
            "attempted": False,
            "message": "I could not open Safari.",
        },
    )
    confirm_response = client.post("/assistant/confirm", json={"confirmation_id": confirmation_id})
    assert confirm_response.status_code == 200
    data = confirm_response.json()
    assert data["status"] == "confirmed_failed"
    assert data["result"]["ok"] is False
    assert data["result"]["result"]["status"] == "failed"


def test_open_command_routes_to_native_app_preview(client):
    response = client.post("/assistant/respond", json={"text": "open Spotify", "session_id": None, "include_audio": False})
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["metadata"]["source"] == "action"
    assert data["action_preview"]["action"] == "open_app"
    assert data["action_preview"]["target"] == "Spotify"


def test_open_unknown_app_does_not_fall_back_to_url(client):
    response = client.post("/assistant/respond", json={"text": "open madeupapp", "session_id": None, "include_audio": False})
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert "website" in data["text"].lower()


def test_vision_route_returns_metadata(client):
    response = client.post(
        "/vision/analyze",
        json={
            "session_id": "test-session",
            "screenshot_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9sXn8S8AAAAASUVORK5CYII=",
            "prompt": "Inspect this screenshot",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "metadata" in data
    assert "saved_path" in data["metadata"]
    assert "analysis_level" in data["metadata"]
    assert "ocr_state" in data["metadata"]


def test_vision_route_persists_session_context(client):
    session_id = "session-vision-test"
    response = client.post(
        "/vision/analyze",
        json={
            "session_id": session_id,
            "screenshot_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9sXn8S8AAAAASUVORK5CYII=",
            "prompt": "Inspect this screenshot",
        },
    )
    assert response.status_code == 200
    stored = memory_service.get_memory(f"session:{session_id}:last_vision_context")
    assert stored is not None
    assert "summary" in str(stored["value"])


def test_transcribe_route_hides_internal_error_details(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.api.routes.voice.voice_service.transcribe_file",
        lambda _path: (_ for _ in ()).throw(RuntimeError("internal device stack trace")),
    )
    response = client.post(
        "/voice/transcribe",
        json={
            "audio_base64": base64.b64encode(b"test").decode("ascii"),
            "file_suffix": ".wav",
        },
    )
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "transcription failed" in detail.lower()
    assert "stack trace" not in detail.lower()


def test_spotify_status_route(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.spotify_status",
        lambda: {
            "enabled": True,
            "available": True,
            "running": True,
            "player_state": "playing",
            "track": "Song",
            "artist": "Artist",
            "album": "Album",
            "position_seconds": 12.5,
            "message": "Spotify is running.",
        },
    )
    response = client.get("/integrations/spotify/status")
    assert response.status_code == 200
    data = response.json()
    assert data["running"] is True
    assert data["track"] == "Song"


def test_calendar_create_route(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.productivity.service.productivity_service.create_calendar_event",
        lambda **kwargs: {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": "Calendar event created.",
            "event_id": "event-1",
            "title": kwargs["title"],
            "calendar_name": "Home",
            "starts_at": kwargs["starts_at"].isoformat(),
            "ends_at": kwargs["ends_at"].isoformat(),
            "recurrence": kwargs.get("recurrence"),
        },
    )
    response = client.post(
        "/integrations/calendar/events",
        json={
            "title": "Daily standup",
            "starts_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "ends_at": (datetime.now(UTC) + timedelta(days=1, minutes=30)).isoformat(),
            "recurrence": "FREQ=DAILY;INTERVAL=1",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"] == "verified"
    assert data["title"] == "Daily standup"


def test_calendar_upcoming_route(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.productivity.service.productivity_service.upcoming_calendar_events",
        lambda **kwargs: {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": "Upcoming calendar events: Standup on Friday.",
            "events": [{"event_id": "1", "title": "Standup", "calendar_name": "Home"}],
            "calendar_name": "Home",
        },
    )
    response = client.get("/integrations/calendar/events")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["events"][0]["title"] == "Standup"


def test_mail_draft_route(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.productivity.service.productivity_service.create_mail_draft",
        lambda **kwargs: {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": "Mail draft created.",
            "mail_id": "draft-1",
            "to": kwargs["to"],
            "subject": kwargs["subject"],
            "cc": kwargs.get("cc", []),
        },
    )
    response = client.post(
        "/integrations/mail/drafts",
        json={"to": "test@example.com", "subject": "Meeting", "body": "Can we meet tomorrow?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["to"] == "test@example.com"


def test_system_report_route(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.system_report",
        lambda: {
            "ok": True,
            "power_mode": "basic",
            "wake_word": {"effective_enabled": False},
            "active_app": {"ok": True, "app": "Safari"},
            "browser": {"ok": True, "title": "OpenAI"},
            "spotify": {"running": False},
            "suggestions": ["Summarize the page."],
            "summary": "Status report at 10:00 AM. Power mode is basic.",
        },
    )
    response = client.get("/integrations/system/report")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "Power mode is basic" in data["summary"]
    assert data["suggestions"] == ["Summarize the page."]


def test_system_status_route(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.system_status",
        lambda: {
            "ok": True,
            "status": "verified",
            "cpu": {"usage_percent": 12.5},
            "memory": {"used_percent": 55.0},
            "disk": {"used_percent": 70.0},
            "summary": "CPU usage is 12.5%. RAM usage is 55.0%; disk usage is 70.0%.",
        },
    )
    response = client.get("/integrations/system/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "verified"
    assert "CPU usage is 12.5%" in data["summary"]


def test_system_status_includes_simple_interpretation(monkeypatch):
    class FakeDiskUsage:
        total = 100
        used = 92
        free = 8

    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service._read_cpu_usage",
        lambda: {"usage_percent": 91.0, "load_1m": 7.2, "cpu_count": 8},
    )
    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service._read_memory_usage",
        lambda: {"used_percent": 88.0, "total_bytes": 100, "used_bytes": 88, "free_bytes": 12},
    )
    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service._read_battery",
        lambda: {"percent": 12, "charging": False},
    )
    monkeypatch.setattr("backend.app.services.integrations.service.shutil.disk_usage", lambda _path: FakeDiskUsage())

    status = integration_service.system_status()
    assert status["status"] == "verified"
    assert "CPU load is high" in status["interpretation"]
    assert "RAM usage is high" in status["interpretation"]
    assert "Disk usage is nearly full" in status["interpretation"]
    assert "Battery is low" in status["interpretation"]
    assert "exact process cause" in status["summary"]


def test_system_capabilities_route(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.capability_report",
        lambda: {
            "ok": True,
            "platform": "Darwin",
            "allowed_apps": ["Safari", "Spotify"],
            "allowed_folders": ["downloads"],
            "capabilities": {"desktop_actions": True},
            "wake_word": {"effective_enabled": False},
            "summary": "Safe desktop control is available.",
        },
    )
    response = client.get("/integrations/system/capabilities")
    assert response.status_code == 200
    data = response.json()
    assert data["platform"] == "Darwin"
    assert data["capabilities"]["desktop_actions"] is True


def test_mode_profile_route(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.mode_profile",
        lambda: {
            "mode": "basic",
            "summary": "Basic mode keeps Jarvis thermally lighter.",
            "features": {
                "llm_budget": "lower",
                "page_summary_depth": "compact",
            },
        },
    )
    response = client.get("/integrations/system/mode-profile")
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "basic"
    assert data["features"]["llm_budget"] == "lower"


def test_system_briefing_route(client, monkeypatch):
    async def fake_briefing():
        return {
            "ok": True,
            "summary": "Power mode is basic. The active application is Safari.",
            "power_mode": "basic",
            "active_app": {"ok": True, "app": "Safari"},
            "weather": {"ok": True, "summary": "Warm."},
            "news": {"ok": True, "summary": "Quiet."},
            "reminders": "You have no active reminders.",
            "spotify": {"running": False},
        }

    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.daily_briefing",
        fake_briefing,
    )
    response = client.get("/integrations/system/briefing")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "power mode is basic" in data["summary"].lower()


def test_operator_briefing_route(client, monkeypatch):
    async def fake_operator_briefing():
        return {
            "ok": True,
            "summary": "Operator briefing at 10:00 AM. Safari is active.",
            "power_mode": "advanced",
            "active_app": {"ok": True, "app": "Safari"},
            "browser": {"ok": True, "title": "OpenAI"},
            "page_summary": {"ok": True, "summary": "A concise page summary."},
            "weather": {"ok": True, "summary": "Clear."},
            "news": {"ok": True, "summary": "Busy."},
            "reminders": "You have no active reminders.",
            "spotify": {"running": False, "message": "Spotify is not running."},
            "suggestions": ["Summarize this page.", "Search Google for related context."],
        }

    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.operator_briefing",
        fake_operator_briefing,
    )
    response = client.get("/integrations/system/operator-briefing")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["power_mode"] == "advanced"
    assert len(data["suggestions"]) == 2


def test_browser_page_summary_route(client, monkeypatch):
    async def fake_summary():
        return {
            "ok": True,
            "summary": "The current page explains the topic clearly and highlights the main takeaways.",
            "context": {"title": "Example"},
        }

    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.summarize_current_page",
        fake_summary,
    )
    response = client.get("/integrations/browser/page-summary")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "highlights the main takeaways" in data["summary"].lower()


def test_browser_awareness_route(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.page_awareness",
        lambda: {
            "ok": True,
            "app": "Safari",
            "url": "https://openai.com",
            "title": "OpenAI",
            "domain": "openai.com",
            "message": "You are in Safari on OpenAI (openai.com).",
        },
    )
    response = client.get("/integrations/browser/awareness")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["domain"] == "openai.com"


def test_search_based_on_current_page_falls_back_to_active_app(monkeypatch):
    monkeypatch.setattr(
        integration_service,
        "browser_context",
        lambda: {"ok": False, "app": "Finder", "url": None, "title": None, "message": "The frontmost app is not a supported browser."},
    )
    monkeypatch.setattr(
        integration_service,
        "active_application",
        lambda: {"ok": True, "app": "Finder", "message": "The active application is Finder."},
    )

    async def fake_search(query: str):
        return {
            "ok": True,
            "summary": f"Search results for {query}.",
            "results": [],
            "google_url": "https://www.google.com/search?q=finder",
        }

    monkeypatch.setattr(integration_service, "search_web", fake_search)

    result = asyncio.run(integration_service.search_based_on_current_page("project notes"))
    assert result["ok"] is True
    assert result["query"] == "project notes Finder"
    assert "active app context" in result["summary"]


def test_active_app_intelligence_route(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.active_app_intelligence",
        lambda: {
            "ok": True,
            "app": "Safari",
            "active_app": {"ok": True, "app": "Safari", "message": "The active application is Safari."},
            "browser": {"ok": True, "title": "OpenAI"},
            "spotify": {"running": False},
            "suggestions": ["Summarize this page."],
            "summary": "The active application is Safari. Suggested next actions: Summarize this page.",
        },
    )
    response = client.get("/integrations/system/active-app/intelligence")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "suggested next actions" in data["summary"].lower()


def test_active_app_intelligence_basic_mode_skips_extra_context(client, monkeypatch):
    monkeypatch.setattr(
        integration_service,
        "active_application",
        lambda: {"ok": True, "app": "Finder", "message": "The active application is Finder."},
    )
    monkeypatch.setattr(
        integration_service,
        "browser_context",
        lambda: (_ for _ in ()).throw(AssertionError("browser context should be skipped in basic mode")),
    )
    monkeypatch.setattr(
        integration_service,
        "spotify_status",
        lambda: (_ for _ in ()).throw(AssertionError("spotify status should be skipped in basic mode")),
    )

    data = integration_service.active_app_intelligence()

    assert data["ok"] is True
    assert data["app"] == "Finder"
    assert "skipped in Basic Mode" in data["browser"]["message"]


def test_context_brief_route(client, monkeypatch):
    async def fake_context():
        return {
            "ok": True,
            "active_app": {"ok": True, "app": "Safari"},
            "browser": {"ok": True, "title": "OpenAI", "url": "https://openai.com"},
            "page_summary": {"ok": True, "summary": "OpenAI overview."},
            "spotify": {"running": False},
            "suggestions": ["Summarize this page."],
            "summary": "The active application is Safari. Current tab: OpenAI.",
        }

    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.contextual_brief",
        fake_context,
    )
    response = client.get("/integrations/system/context")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "current tab" in data["summary"].lower()


def test_daily_briefing_gracefully_handles_integration_failures(monkeypatch):
    async def fail_weather(_place: str):
        raise httpx.ConnectError("network down")

    async def fail_news(_topic: str):
        raise RuntimeError("rss parser crashed")

    monkeypatch.setattr(integration_service, "get_weather", fail_weather)
    monkeypatch.setattr(integration_service, "get_news", fail_news)
    monkeypatch.setattr(
        integration_service,
        "active_application",
        lambda: {"ok": False, "app": None, "message": "Active application context unavailable."},
    )
    monkeypatch.setattr(integration_service, "spotify_status", lambda: {"running": False, "message": "Spotify is not running."})
    monkeypatch.setattr(
        "backend.app.services.productivity.service.productivity_service.upcoming_calendar_events",
        lambda **kwargs: {"ok": False, "message": "Calendar access is unavailable."},
    )
    monkeypatch.setattr(
        "backend.app.services.reminders.service.reminder_service.summarize_active",
        lambda: "You have no active reminders.",
    )

    result = asyncio.run(integration_service.daily_briefing())
    assert result["ok"] is True
    assert result["weather"]["ok"] is False
    assert "temporarily unavailable" in result["weather"]["summary"].lower()
    assert result["news"]["ok"] is False
    assert "temporarily unavailable" in result["news"]["summary"].lower()


def test_weather_uses_free_open_meteo_without_api_key(monkeypatch):
    calls: list[tuple[str, dict[str, object] | None]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, params: dict[str, object] | None = None):
            calls.append((url, params))
            if "geocoding-api.open-meteo.com" in url:
                return FakeResponse(
                    {
                        "results": [
                            {
                                "name": "Muscat",
                                "country": "Oman",
                                "latitude": 23.588,
                                "longitude": 58.3829,
                            }
                        ]
                    }
                )
            return FakeResponse(
                {
                    "current": {"temperature_2m": 31.0, "wind_speed_10m": 12.0},
                    "daily": {"temperature_2m_min": [27.0], "temperature_2m_max": [34.0]},
                }
            )

    monkeypatch.delenv("WEATHER_API_KEY", raising=False)
    monkeypatch.setattr("backend.app.services.integrations.service.httpx.AsyncClient", FakeAsyncClient)

    result = asyncio.run(integration_service.get_weather("Muscat"))

    assert result["ok"] is True
    assert "Muscat, Oman" in result["summary"]
    assert len(calls) == 2
    assert all("api_key" not in (params or {}) for _, params in calls)


def test_news_is_disabled_in_local_only_mode():
    result = asyncio.run(integration_service.get_news("technology"))
    assert result["ok"] is False
    assert "disabled in local-only mode" in result["summary"]
    assert result["headlines"] == []


def test_operator_briefing_gracefully_handles_page_summary_failure(monkeypatch):
    async def fail_page_summary():
        raise RuntimeError("page summary failed")

    async def fallback_weather(_place: str):
        return {"ok": False, "summary": "Live weather is temporarily unavailable.", "raw": None}

    async def fallback_news(_topic: str):
        return {"ok": False, "summary": "Live headlines are temporarily unavailable.", "headlines": []}

    monkeypatch.setattr(integration_service, "summarize_current_page", fail_page_summary)
    monkeypatch.setattr(
        integration_service,
        "active_application",
        lambda: {"ok": True, "app": "Safari", "message": "The active application is Safari."},
    )
    monkeypatch.setattr(
        integration_service,
        "browser_context",
        lambda: {"ok": True, "app": "Safari", "title": "OpenAI", "url": "https://openai.com", "message": "Browser context retrieved."},
    )
    monkeypatch.setattr(
        integration_service,
        "spotify_status",
        lambda: {"running": False, "message": "Spotify is not running.", "player_state": "not_running"},
    )
    monkeypatch.setattr(
        "backend.app.services.productivity.service.productivity_service.upcoming_calendar_events",
        lambda **kwargs: {"ok": False, "message": "Calendar access is unavailable."},
    )
    monkeypatch.setattr(
        "backend.app.services.reminders.service.reminder_service.summarize_active",
        lambda: "You have no active reminders.",
    )
    monkeypatch.setattr(integration_service, "get_weather", fallback_weather)
    monkeypatch.setattr(integration_service, "get_news", fallback_news)

    result = asyncio.run(integration_service.operator_briefing())
    assert result["ok"] is True
    assert result["page_summary"]["ok"] is False
    assert "couldn't summarize" in result["page_summary"]["summary"].lower()


def test_reminder_routes(client):
    due_at = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    create_response = client.post(
        "/reminders",
        json={"title": "stand up", "due_at": due_at, "session_id": "test-session"},
    )
    assert create_response.status_code == 200
    reminder = create_response.json()
    assert reminder["title"] == "stand up"

    list_response = client.get("/reminders")
    assert list_response.status_code == 200
    reminders = list_response.json()
    assert any(item["id"] == reminder["id"] for item in reminders)


def test_wake_word_status_defaults_to_basic_paused(client):
    response = client.get("/voice/wake-word/status")
    assert response.status_code == 200
    data = response.json()
    assert data["power_mode"] == "basic"
    assert data["effective_enabled"] is False
    assert data["listener_active"] is False


def test_wake_word_toggle_persists_desired_state(client):
    enable_response = client.post("/voice/wake-word/toggle", json={"enabled": True})
    assert enable_response.status_code == 200
    enabled = enable_response.json()
    assert enabled["desired_enabled"] is True
    assert enabled["effective_enabled"] is False

    disable_response = client.post("/voice/wake-word/toggle", json={"enabled": False})
    assert disable_response.status_code == 200
    disabled = disable_response.json()
    assert disabled["desired_enabled"] is False


def test_voice_respond_returns_graceful_error_response(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.api.routes.voice.voice_service.record_audio_until_silence",
        lambda: (_ for _ in ()).throw(RuntimeError("microphone stream failed")),
    )
    response = client.post("/voice/respond", json={"session_id": "test-session"})
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "test-session"
    assert data["audio_url"] is None
    assert data["metadata"]["source"] == "error"
    assert data["metadata"]["error_type"] == "voice_pipeline"
    assert "voice pipeline error" in data["text"].lower()


def test_voice_respond_reports_busy_microphone(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.api.routes.voice.voice_service.record_audio_until_silence",
        lambda: (_ for _ in ()).throw(RuntimeError("Microphone recording is already active.")),
    )
    response = client.post("/voice/respond", json={"session_id": "test-session"})
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["error_type"] == "microphone_busy"
    assert "already handling voice input" in data["text"].lower()


def test_assistant_basic_behavior(client, monkeypatch):
    async def fake_generate(*, prompt, system_prompt, history, mode):
        assert "concise local macOS desktop assistant" in system_prompt
        return "Jarvis reply"

    monkeypatch.setattr("backend.app.services.llm.ollama.ollama_service.generate", fake_generate)
    response = client.post("/assistant/respond", json={"text": "hello there", "session_id": None, "include_audio": False})
    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "Jarvis reply"
    assert data["session_id"]
    assert data["metadata"]["timing_ms"]["total"] >= 0


def test_assistant_audio_response_still_persists_memory(client, monkeypatch):
    async def fake_generate(*, prompt, system_prompt, history, mode):
        return "Jarvis audio reply"

    async def fake_synthesize(text, voice_name=None):
        assert text == "Jarvis audio reply"
        return {"audio_url": "http://127.0.0.1:8000/audio/test.mp3", "provider": "test"}

    monkeypatch.setattr("backend.app.services.llm.ollama.ollama_service.generate", fake_generate)
    monkeypatch.setattr("backend.app.services.assistant.service.tts_service.synthesize", fake_synthesize)

    response = client.post("/assistant/respond", json={"text": "hello there", "session_id": None, "include_audio": True})
    assert response.status_code == 200
    data = response.json()
    assert data["audio_url"] == "http://127.0.0.1:8000/audio/test.mp3"
    assert data["metadata"]["tts_provider"] == "test"

    messages = memory_service.get_recent_messages(data["session_id"], limit=4)
    assert any(item["role"] == "assistant" and item["content"] == "Jarvis audio reply" for item in messages)


def test_assistant_chain_behavior(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "what mode are you in and then wake word status", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "chain"
    assert "mode" in data["text"].lower()


def test_assistant_search_based_on_this_alias(client, monkeypatch):
    async def fake_search(_modifier=None):
        return {
            "ok": True,
            "query": "openai.com",
            "summary": "Using the current page as context, I found relevant references.",
            "google_url": "https://www.google.com/search?q=openai",
            "results": [],
        }

    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.search_based_on_current_page",
        fake_search,
    )

    response = client.post(
        "/assistant/respond",
        json={"text": "search based on this", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "search"
    assert "current page as context" in data["text"].lower()


def test_assistant_context_suggestions_command(client, monkeypatch):
    async def fake_context():
        return {
            "ok": True,
            "summary": "The active application is Safari. Suggested next actions: Summarize this page; Search based on this page; Open this in Google.",
            "suggestions": ["Summarize this page.", "Search based on this page.", "Open this in Google."],
        }

    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.contextual_brief",
        fake_context,
    )

    response = client.post(
        "/assistant/respond",
        json={"text": "what should i do here", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "system"
    assert "best next moves" in data["text"].lower()


def test_assistant_context_includes_stored_vision(client, monkeypatch):
    session_response = client.post("/voice/session/start", json={"session_name": "test"})
    assert session_response.status_code == 200
    session_id = session_response.json()["session_id"]

    memory_service.store_memory(
        f"session:{session_id}:last_vision_context",
        '{"summary":"Last screen showed a browser article.","ocr_text":"OpenAI release notes"}',
        "session_context",
    )

    async def fake_context():
        return {
            "ok": True,
            "summary": "The active application is Safari.",
            "suggestions": ["Summarize this page."],
        }

    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.contextual_brief",
        fake_context,
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "what am i looking at", "session_id": session_id, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert "last screen context" in data["text"].lower()


def test_assistant_chain_stops_on_failed_action(client, monkeypatch):
    calls = {"count": 0}

    def fake_execute(action, target=None, params=None):
        calls["count"] += 1
        return {
            "ok": False,
            "success": False,
            "verified": False,
            "status": "failed",
            "attempted": False,
            "message": "I could not open Spotify.",
        }

    monkeypatch.setattr("backend.app.services.assistant.service.action_service.execute", fake_execute)
    response = client.post(
        "/assistant/respond",
        json={
            "text": "open spotify, search weather in muscat, summarize it",
            "session_id": None,
            "include_audio": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "chain"
    assert data["metadata"]["chain_status"] == "stopped_failed"
    assert "chain stopped at step 1" in data["text"].lower()
    assert calls["count"] == 1


def test_assistant_screen_command_without_screenshot_context(client):
    response = client.post(
        "/assistant/respond",
        json={
            "text": "what is on my screen",
            "session_id": None,
            "include_audio": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "vision"
    assert "mission control" in data["text"].lower()


def test_assistant_screen_command_with_screenshot_context(client):
    response = client.post(
        "/assistant/respond",
        json={
            "text": "what is on my screen",
            "session_id": None,
            "include_audio": False,
            "include_screen_context": True,
            "screenshot_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9sXn8S8AAAAASUVORK5CYII=",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "vision"
    assert "screenshot" in data["text"].lower()


def test_assistant_status_report_command(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.system_report",
        lambda: {
            "ok": True,
            "power_mode": "basic",
            "wake_word": {"effective_enabled": False},
            "active_app": {"ok": True, "app": "Finder"},
            "browser": {"ok": False},
            "spotify": {"running": False},
            "summary": "Status report at 11:00 AM. Power mode is basic. The active application is Finder.",
        },
    )
    response = client.post("/assistant/respond", json={"text": "status report", "session_id": None, "include_audio": False})
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "system"
    assert "power mode is basic" in data["text"].lower()


def test_assistant_briefing_command(client, monkeypatch):
    async def fake_briefing():
        return {
            "ok": True,
            "summary": "Power mode is advanced. The active application is Finder.",
            "power_mode": "advanced",
            "active_app": {"ok": True, "app": "Finder"},
            "weather": {"ok": True, "summary": "Clear."},
            "news": {"ok": True, "summary": "Steady."},
            "reminders": "You have no active reminders.",
            "spotify": {"running": False},
        }

    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.daily_briefing",
        fake_briefing,
    )
    response = client.post("/assistant/respond", json={"text": "brief me", "session_id": None, "include_audio": False})
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "system"
    assert "power mode is advanced" in data["text"].lower()


def test_assistant_operator_briefing_command(client, monkeypatch):
    async def fake_operator_briefing():
        return {
            "ok": True,
            "summary": "Operator briefing at 11:00 AM. Safari is active. Spotify is not running.",
            "power_mode": "advanced",
            "active_app": {"ok": True, "app": "Safari"},
            "browser": {"ok": True, "title": "OpenAI"},
            "page_summary": {"ok": True, "summary": "A concise page summary."},
            "weather": {"ok": True, "summary": "Clear."},
            "news": {"ok": True, "summary": "Steady."},
            "reminders": "You have no active reminders.",
            "spotify": {"running": False, "message": "Spotify is not running."},
            "suggestions": ["Summarize this page."],
        }

    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.operator_briefing",
        fake_operator_briefing,
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "operator briefing", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "system"
    assert "operator briefing" in data["text"].lower()


def test_assistant_whats_playing_command(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.spotify_status",
        lambda: {
            "enabled": True,
            "available": True,
            "running": True,
            "player_state": "playing",
            "track": "Jarvis Theme",
            "artist": "Test Artist",
            "album": "Test Album",
            "message": "Spotify is running.",
        },
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "what's playing", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "spotify"
    assert "jarvis theme" in data["text"].lower()


def test_assistant_next_move_command(client, monkeypatch):
    async def fake_contextual_brief():
        return {
            "ok": True,
            "active_app": {"ok": True, "app": "Safari"},
            "browser": {"ok": True, "title": "OpenAI"},
            "page_summary": {"ok": True, "summary": "OpenAI homepage summary."},
            "spotify": {"running": False},
            "suggestions": ["Summarize the current page.", "Search Google for related context."],
            "summary": "The active application is Safari. Suggested next actions: Summarize the current page; Search Google for related context.",
        }

    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.contextual_brief",
        fake_contextual_brief,
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "what should i do next", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "system"
    assert "best next moves" in data["text"].lower()
    assert "summarize the current page" in data["text"].lower()


def test_assistant_system_sweep_alias(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.system_report",
        lambda: {
            "ok": True,
            "power_mode": "basic",
            "wake_word": {"effective_enabled": False},
            "active_app": {"ok": True, "app": "Finder"},
            "browser": {"ok": False},
            "spotify": {"running": False},
            "suggestions": [],
            "summary": "Status report at 11:00 AM. Power mode is basic.",
        },
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "system sweep", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "system"
    assert "power mode is basic" in data["text"].lower()


@pytest.mark.parametrize(
    "prompt",
    [
        "what's the CPU load?",
        "what is my CPU usage?",
        "how much RAM am I using?",
        "what's my memory usage?",
        "what's my system status?",
        "how's my Mac doing?",
    ],
)
def test_assistant_system_status_questions(client, monkeypatch, prompt):
    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.system_status",
        lambda: {
            "ok": True,
            "status": "verified",
            "cpu": {"usage_percent": 18.0},
            "memory": {"used_percent": 62.0},
            "disk": {"used_percent": 71.0},
            "summary": "CPU usage is 18.0%. RAM usage is 62.0%; disk usage is 71.0%.",
        },
    )
    response = client.post(
        "/assistant/respond",
        json={"text": prompt, "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["metadata"]["source"] == "system_status"
    assert data["metadata"]["status"] == "verified"
    if "ram" in prompt.lower() or "memory" in prompt.lower():
        assert "ram usage is 62.0%" in data["text"].lower()
    elif "cpu" in prompt.lower():
        assert "cpu usage is 18.0%" in data["text"].lower()
    else:
        assert "cpu usage is 18.0%" in data["text"].lower()
        assert "ram usage is 62.0%" in data["text"].lower()


def test_assistant_chain_summarize_it_uses_search_context(client, monkeypatch):
    async def fake_search(query):
        return {"ok": True, "summary": f"Summary for {query}.", "results": []}

    monkeypatch.setattr(
        "backend.app.services.integrations.service.integration_service.search_web",
        fake_search,
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "search google for arc browser and then summarize it", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "chain"
    assert "summary for arc browser" in data["text"].lower()


def test_assistant_open_downloads_shortcut(client):
    response = client.post("/assistant/respond", json={"text": "open downloads", "session_id": None, "include_audio": False})
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "open_folder"


def test_assistant_open_app_store_polite_command(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "Can you open the App Store?", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "open_app"
    assert data["action_preview"]["target"] == "App Store"


@pytest.mark.parametrize(
    ("prompt", "action", "target", "confirmation_required"),
    [
        ("can you open Spotify", "open_app", "Spotify", False),
        ("please open Spotify", "open_app", "Spotify", False),
        ("can you launch Safari", "open_app", "Safari", False),
        ("could you switch to Chrome", "switch_app", "Google Chrome", False),
        ("can you close Notes", "close_app", "Notes", True),
    ],
)
def test_assistant_natural_app_command_phrasing(client, monkeypatch, prompt, action, target, confirmation_required):
    monkeypatch.setattr(
        "backend.app.services.assistant.service.action_service.execute",
        lambda action, target=None, params=None: {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": f"{target} handled.",
            "app": target,
        },
    )
    response = client.post(
        "/assistant/respond",
        json={"text": prompt, "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is confirmation_required
    assert data["action_preview"]["action"] == action
    assert data["action_preview"]["target"] == target


def test_assistant_open_my_browser_uses_default_browser(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.assistant.service.assistant_service._default_browser_target",
        lambda: "Google Chrome",
    )
    monkeypatch.setattr(
        "backend.app.services.assistant.service.action_service.execute",
        lambda action, target=None, params=None: {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": f"{target} opened.",
            "app": target,
        },
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "open my browser", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "open_app"
    assert data["action_preview"]["target"] == "Google Chrome"


@pytest.mark.parametrize(
    "prompt",
    ["what app am I using?", "what app am I in?"],
)
def test_assistant_active_app_natural_questions(client, monkeypatch, prompt):
    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.active_app_intelligence",
        lambda: {"ok": True, "summary": "You are using Finder."},
    )
    response = client.post(
        "/assistant/respond",
        json={"text": prompt, "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "system"
    assert "using finder" in data["text"].lower()


def test_assistant_current_page_natural_question(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.page_awareness",
        lambda: {"ok": True, "title": "Docs", "url": "https://example.com/docs", "message": "Current page is Docs."},
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "what page is open?", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "browser"
    assert "example.com/docs" in data["text"]


def test_assistant_looking_at_natural_question(client, monkeypatch):
    async def fake_contextual_brief():
        return {"ok": True, "summary": "You are viewing a code editor.", "suggestions": []}

    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.contextual_brief",
        fake_contextual_brief,
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "what am I looking at?", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "system"
    assert "code editor" in data["text"].lower()


def test_assistant_youtube_search_opens_safe_url(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.assistant.service.action_service.execute",
        lambda action, target=None, params=None: {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": "The requested page is open.",
            "url": target,
        },
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "search YouTube for lo-fi music", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "open_url"
    assert data["action_preview"]["target"] == "https://www.youtube.com/results?search_query=lo-fi+music"


def test_assistant_google_search_natural_phrase(client, monkeypatch):
    async def fake_search(query):
        return {"ok": True, "summary": f"Search results for {query}.", "results": []}

    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.search_web",
        fake_search,
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "search Google for Python decorators", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["metadata"]["source"] == "search"
    assert "python decorators" in data["text"].lower()


def test_assistant_follow_up_pause_it_uses_recent_spotify_context(client, monkeypatch):
    calls = []

    def fake_execute(action, target=None, params=None):
        calls.append((action, target))
        return {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": f"{action} handled.",
            "app": target or "Spotify",
        }

    monkeypatch.setattr("backend.app.services.assistant.service.action_service.execute", fake_execute)
    first = client.post(
        "/assistant/respond",
        json={"text": "open Spotify", "session_id": None, "include_audio": False},
    )
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    second = client.post(
        "/assistant/respond",
        json={"text": "pause it", "session_id": session_id, "include_audio": False},
    )
    assert second.status_code == 200
    data = second.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "spotify_pause"
    assert data["action_preview"]["target"] == "Spotify"
    assert ("spotify_pause", "Spotify") in calls


def test_assistant_follow_up_close_it_uses_active_app_and_requires_confirmation(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.active_app_intelligence",
        lambda: {"ok": True, "app": "Notes", "summary": "The active application is Notes."},
    )
    first = client.post(
        "/assistant/respond",
        json={"text": "what app am I using?", "session_id": None, "include_audio": False},
    )
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    second = client.post(
        "/assistant/respond",
        json={"text": "close it", "session_id": session_id, "include_audio": False},
    )
    assert second.status_code == 200
    data = second.json()
    assert data["confirmation_required"] is True
    assert data["action_preview"]["action"] == "close_app"
    assert data["action_preview"]["target"] == "Notes"


def test_assistant_follow_up_switch_to_it_uses_recent_app_context(client, monkeypatch):
    def fake_execute(action, target=None, params=None):
        return {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": f"{target} handled.",
            "app": target,
        }

    monkeypatch.setattr("backend.app.services.assistant.service.action_service.execute", fake_execute)
    first = client.post(
        "/assistant/respond",
        json={"text": "open Chrome", "session_id": None, "include_audio": False},
    )
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    second = client.post(
        "/assistant/respond",
        json={"text": "switch to it", "session_id": session_id, "include_audio": False},
    )
    assert second.status_code == 200
    data = second.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "switch_app"
    assert data["action_preview"]["target"] == "Google Chrome"


def test_assistant_chain_open_chrome_and_search_youtube(client, monkeypatch):
    def fake_execute(action, target=None, params=None):
        return {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": f"{target} handled.",
            "app": target if action.endswith("_app") else None,
            "url": target if action == "open_url" else None,
        }

    monkeypatch.setattr("backend.app.services.assistant.service.action_service.execute", fake_execute)
    response = client.post(
        "/assistant/respond",
        json={"text": "open Chrome and search YouTube for lo-fi music", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "chain"
    assert data["metadata"]["chain_status"] == "completed"
    assert [step["source"] for step in data["metadata"]["steps"]] == ["action", "action"]


def test_assistant_chain_open_spotify_and_pause_it(client, monkeypatch):
    calls = []

    def fake_execute(action, target=None, params=None):
        calls.append((action, target))
        return {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": f"{action} handled.",
            "app": target or "Spotify",
        }

    monkeypatch.setattr("backend.app.services.assistant.service.action_service.execute", fake_execute)
    response = client.post(
        "/assistant/respond",
        json={"text": "open Spotify and pause it", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "chain"
    assert ("open_app", "Spotify") in calls
    assert ("spotify_pause", "Spotify") in calls


def test_assistant_chain_open_safari_then_search_google(client, monkeypatch):
    def fake_execute(action, target=None, params=None):
        return {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": f"{target} opened.",
            "app": target,
        }

    async def fake_search(query):
        return {"ok": True, "summary": f"Search results for {query}.", "results": []}

    monkeypatch.setattr("backend.app.services.assistant.service.action_service.execute", fake_execute)
    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.search_web",
        fake_search,
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "open Safari then search Google for homework tips", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "chain"
    assert "homework tips" in data["text"].lower()


def test_assistant_chain_active_app_and_cpu_status(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.active_app_intelligence",
        lambda: {"ok": True, "app": "Finder", "summary": "The active application is Finder."},
    )
    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.system_status",
        lambda: {
            "ok": True,
            "status": "verified",
            "cpu": {"usage_percent": 21.0},
            "memory": {"used_percent": 50.0},
            "summary": "CPU usage is 21.0%. RAM usage is 50.0%.",
        },
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "what app am I using and what's my CPU load?", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "chain"
    assert "active application is finder" in data["text"].lower()
    assert "cpu usage is 21.0%" in data["text"].lower()


def test_assistant_chain_stops_on_clarification(client, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("later chain step should not run after clarification")

    monkeypatch.setattr("backend.app.services.assistant.service.action_service.execute", fail_if_called)
    response = client.post(
        "/assistant/respond",
        json={"text": "open something and search Google for homework tips", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "clarification"
    assert "which app or website" in data["text"].lower()


def test_assistant_open_something_asks_clarification(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "open something", "session_id": "clarify-open", "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["metadata"]["source"] == "clarification"
    assert "which app or website" in data["text"].lower()


def test_assistant_open_clarification_reply_opens_app(client, monkeypatch):
    def fake_execute(action, target=None, params=None):
        return {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": f"{target} opened.",
            "app": target,
        }

    monkeypatch.setattr("backend.app.services.assistant.service.action_service.execute", fake_execute)
    first = client.post(
        "/assistant/respond",
        json={"text": "open something", "session_id": None, "include_audio": False},
    )
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    second = client.post(
        "/assistant/respond",
        json={"text": "Spotify", "session_id": session_id, "include_audio": False},
    )
    assert second.status_code == 200
    data = second.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "open_app"
    assert data["action_preview"]["target"] == "Spotify"


def test_assistant_search_for_it_asks_clarification(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "search for it", "session_id": "clarify-search", "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["metadata"]["source"] == "search"
    assert "what should i search for" in data["text"].lower()


def test_assistant_search_clarification_reply_searches(client, monkeypatch):
    async def fake_search(query):
        return {"ok": True, "summary": f"Search results for {query}.", "results": []}

    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.search_web",
        fake_search,
    )
    first = client.post(
        "/assistant/respond",
        json={"text": "search for it", "session_id": None, "include_audio": False},
    )
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    second = client.post(
        "/assistant/respond",
        json={"text": "Python decorators", "session_id": session_id, "include_audio": False},
    )
    assert second.status_code == 200
    data = second.json()
    assert data["metadata"]["source"] == "search"
    assert "python decorators" in data["text"].lower()


def test_assistant_cancel_clears_pending_clarification(client):
    first = client.post(
        "/assistant/respond",
        json={"text": "open something", "session_id": None, "include_audio": False},
    )
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    second = client.post(
        "/assistant/respond",
        json={"text": "never mind", "session_id": session_id, "include_audio": False},
    )
    assert second.status_code == 200
    data = second.json()
    assert data["metadata"]["source"] == "clarification"
    assert "cancelled" in data["text"].lower()


def test_assistant_why_mac_slow_uses_system_status(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.assistant.service.integration_service.system_status",
        lambda: {
            "ok": True,
            "status": "verified",
            "summary": "CPU usage is 91.0%. RAM usage is 88.0%; disk usage is 92.0%. CPU load is high. I cannot identify the exact process cause from this check alone.",
        },
    )
    response = client.post(
        "/assistant/respond",
        json={"text": "why is my Mac slow?", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "system_status"
    assert "exact process cause" in data["text"].lower()


def test_assistant_mute_volume_routes_safe_action(client, monkeypatch):
    def fake_execute(action, target=None, params=None):
        return {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": "System audio is muted.",
            "muted": True,
        }

    monkeypatch.setattr("backend.app.services.assistant.service.action_service.execute", fake_execute)
    response = client.post(
        "/assistant/respond",
        json={"text": "mute volume", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "mute_volume"
    assert data["metadata"]["action_status"] == "verified"


def test_assistant_next_track_routes_spotify_control(client, monkeypatch):
    def fake_execute(action, target=None, params=None):
        return {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": "Spotify moved to the next track.",
            "app": "Spotify",
        }

    monkeypatch.setattr("backend.app.services.assistant.service.action_service.execute", fake_execute)
    response = client.post(
        "/assistant/respond",
        json={"text": "next track", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "spotify_next"
    assert data["action_preview"]["target"] == "Spotify"


def test_assistant_brightness_control_stays_honest(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "brightness up", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["action_preview"] is None
    assert data["metadata"]["source"] == "policy"
    assert "not wired" in data["text"].lower()


def test_assistant_open_youtube_then_search_uses_youtube_url(client, monkeypatch):
    opened: list[str] = []

    def fake_execute(action, target=None, params=None):
        opened.append(str(target))
        return {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": "The requested page is open.",
            "url": target,
        }

    monkeypatch.setattr("backend.app.services.assistant.service.action_service.execute", fake_execute)
    response = client.post(
        "/assistant/respond",
        json={"text": "open YouTube and search for study music", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "chain"
    assert "https://www.youtube.com/results?search_query=study+music" in opened


def test_assistant_open_first_result_not_supported(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "open first result", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["action_preview"] is None
    assert data["metadata"]["source"] == "search"
    assert "not supported yet" in data["text"].lower()


def test_assistant_switch_back_to_named_app(client, monkeypatch):
    def fake_execute(action, target=None, params=None):
        return {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": f"{target} is now frontmost.",
            "app": target,
        }

    monkeypatch.setattr("backend.app.services.assistant.service.action_service.execute", fake_execute)
    response = client.post(
        "/assistant/respond",
        json={"text": "switch back to Spotify", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "switch_app"
    assert data["action_preview"]["target"] == "Spotify"


def test_assistant_style_preference_commands(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "be more concise", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "preference"
    assert data["memory_updated"] is True
    assert memory_service.get_preference("response_style") == "concise"


def test_assistant_command_history_answers_last_question(client):
    first = client.post(
        "/assistant/respond",
        json={"text": "what mode are you in", "session_id": None, "include_audio": False},
    )
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    second = client.post(
        "/assistant/respond",
        json={"text": "what did I just ask?", "session_id": session_id, "include_audio": False},
    )
    assert second.status_code == 200
    data = second.json()
    assert data["metadata"]["source"] == "history"
    assert "what mode are you in" in data["text"].lower()


def test_assistant_run_that_again_reroutes_safe_command(client, monkeypatch):
    calls: list[tuple[str, str | None]] = []

    def fake_execute(action, target=None, params=None):
        calls.append((action, target))
        return {
            "ok": True,
            "success": True,
            "verified": True,
            "status": "verified",
            "attempted": True,
            "message": f"{target} opened.",
            "app": target,
        }

    monkeypatch.setattr("backend.app.services.assistant.service.action_service.execute", fake_execute)
    first = client.post(
        "/assistant/respond",
        json={"text": "open Spotify", "session_id": None, "include_audio": False},
    )
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    second = client.post(
        "/assistant/respond",
        json={"text": "run that again", "session_id": session_id, "include_audio": False},
    )
    assert second.status_code == 200
    assert calls == [("open_app", "Spotify"), ("open_app", "Spotify")]


def test_assistant_reminder_without_time_asks_when(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "remind me to study", "session_id": "clarify-reminder", "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["metadata"]["source"] == "reminder"
    assert "when should i remind you" in data["text"].lower()
    assert reminder_service.list_active() == []


def test_assistant_reminder_clarification_reply_creates_reminder(client):
    first = client.post(
        "/assistant/respond",
        json={"text": "remind me to study", "session_id": None, "include_audio": False},
    )
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    second = client.post(
        "/assistant/respond",
        json={"text": "in 20 minutes", "session_id": session_id, "include_audio": False},
    )
    assert second.status_code == 200
    data = second.json()
    assert data["metadata"]["source"] == "reminder"
    assert "study" in data["text"].lower()
    reminders = reminder_service.list_active()
    assert any(item["title"] == "study" for item in reminders)


def test_assistant_reminder_relative_natural_order(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "remind me in 10 minutes to drink water", "session_id": "test-session", "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "reminder"
    reminders = reminder_service.list_active()
    assert any(item["title"] == "drink water" for item in reminders)


def test_assistant_reminder_tomorrow_natural_order(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "remind me tomorrow to check homework", "session_id": "test-session", "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "reminder"
    reminders = reminder_service.list_active()
    reminder = next(item for item in reminders if item["title"] == "check homework")
    assert reminder["due_at"].astimezone().hour == 9


def test_assistant_reminder_after_school_asks_for_time(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "remind me after school to check homework", "session_id": "test-session", "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["source"] == "reminder"
    assert "specific time" in data["text"].lower()
    assert reminder_service.list_active() == []


def test_assistant_calendar_command_executes_without_confirmation(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "schedule Daily standup tomorrow at 9 am for 30 minutes", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "create_calendar_event"
    assert data["action_preview"]["target"] == "Daily standup"


def test_assistant_mail_draft_executes_without_confirmation(client):
    response = client.post(
        "/assistant/respond",
        json={
            "text": "draft email to test@example.com subject Meeting tomorrow body Can we do 3 PM?",
            "session_id": None,
            "include_audio": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "create_mail_draft"
    assert data["action_preview"]["params"]["to"] == "test@example.com"


def test_assistant_calendar_command_without_time_stays_truthful(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "schedule standup", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert "clear date and time" in data["text"].lower()


def test_assistant_uppercase_open_does_not_raise(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "Open Spotify", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    assert "text" in response.json()


def test_assistant_polite_open_terminal_routes_to_app(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "Would you open Terminal for me?", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "open_app"
    assert data["action_preview"]["target"] == "Terminal"


def test_safe_open_google_does_not_require_confirmation(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "open Google", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is False
    assert data["action_preview"]["action"] == "open_url"


def test_close_app_still_requires_confirmation(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "close app terminal", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is True


def test_clipboard_write_still_requires_confirmation(client):
    response = client.post(
        "/assistant/respond",
        json={"text": "copy hello world", "session_id": None, "include_audio": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_required"] is True


def test_websocket_text_flow(client, monkeypatch):
    async def fake_generate(*, prompt, system_prompt, history, mode):
        return "Websocket reply"

    monkeypatch.setattr("backend.app.services.llm.ollama.ollama_service.generate", fake_generate)
    with client.websocket_connect("/ws") as websocket:
        websocket.send_text('{"text":"hello websocket"}')
        ack = websocket.receive_json()
        data = websocket.receive_json()
    assert ack["event"] == "ack"
    assert data["text"] == "Websocket reply"
    assert data["heard"] == "hello websocket"


def test_websocket_recovers_from_backend_failure(client, monkeypatch):
    async def fake_respond(*, text, session_id, include_audio):
        raise RuntimeError("ollama offline")

    monkeypatch.setattr("backend.app.main.assistant_service.respond", fake_respond)
    with client.websocket_connect("/ws") as websocket:
        websocket.send_text('{"text":"hello websocket"}')
        ack = websocket.receive_json()
        data = websocket.receive_json()
    assert ack["event"] == "ack"
    assert data["metadata"]["source"] == "error"
    assert data["metadata"]["error_type"] == "backend"
    assert data["audio"] is None
    assert "backend error" in data["text"].lower()


def test_backend_import_path():
    import backend.app.main as main

    assert main.app.title == "Jarvis Backend"
