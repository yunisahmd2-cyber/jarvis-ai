from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


ROOT_DIR = Path(__file__).resolve().parents[3]
BACKEND_DIR = ROOT_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseModel):
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    cors_origins_raw: str = Field(default="http://localhost:5173,http://127.0.0.1:5173,tauri://localhost")
    database_url: str = f"sqlite:///{(DATA_DIR / 'jarvis.db').as_posix()}"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1:8b"
    stt_provider: str = "faster-whisper"
    tts_provider: str = "edge"
    default_voice_name: str = "Daniel"
    require_confirmation_for_risky_actions: bool = True
    allowed_apps_raw: str = (
        "Safari,Spotify,Finder,Terminal,Notes,TextEdit,Music,Messages,Google Chrome,Arc,App Store,"
        "Calculator,Preview,Visual Studio Code,Mail,Calendar,Reminders,System Settings,"
        "Photos,Slack,Discord,zoom.us"
    )
    spotify_enabled: bool = True
    ocr_enabled: bool = False
    screen_analysis_enabled: bool = False
    piper_model_path: str = str(ROOT_DIR / "piper_voices" / "en_GB-alan-medium.onnx")
    edge_voice_name: str = "en-GB-RyanNeural"
    fast_edge_voice_name: str = "en-US-GuyNeural"
    whisper_model_name: str = "base"
    whisper_basic_model_name: str = "tiny"
    whisper_advanced_model_name: str = "base"
    whisper_compute_type: str = "int8"
    stt_default_language: str = "en"
    stt_auto_detect_language: bool = False
    audio_input_device: str | None = None
    audio_dir: str = str(ROOT_DIR / "audio")
    workspace_dir: str = str(ROOT_DIR / "workspace")
    memory_seed_file: str = str(ROOT_DIR / "memory.json")
    notes_seed_file: str = str(ROOT_DIR / "notes.json")
    status_seed_file: str = str(ROOT_DIR / "status.json")
    action_audit_log: str = str(DATA_DIR / "action_audit.log")
    frontend_backend_url: str = "http://127.0.0.1:8000"
    session_summary_message_limit: int = 12
    assistant_response_timeout_seconds: float = 25.0
    speech_max_seconds: float = 8.5
    speech_silence_seconds: float = 0.62
    speech_min_speech_seconds: float = 0.24
    speech_silence_threshold: float = 0.009
    wake_word: str = "jarvis"
    reminder_poll_seconds: int = 20
    wake_word_load_threshold_ratio: float = 0.8

    @field_validator("ollama_model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        model = value.strip()
        if not model:
            raise ValueError("OLLAMA_MODEL must not be empty")
        if "llama" not in model.lower():
            raise ValueError("OLLAMA_MODEL must remain a llama-family model for this project")
        return model

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("sqlite:///"):
            raise ValueError("DATABASE_URL currently supports only sqlite:/// paths")
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins_raw.split(",") if item.strip()]

    @property
    def allowed_apps(self) -> list[str]:
        return [item.strip() for item in self.allowed_apps_raw.split(",") if item.strip()]

    @property
    def sqlite_path(self) -> Path:
        return Path(self.database_url.removeprefix("sqlite:///"))

    @property
    def audio_path(self) -> Path:
        path = Path(self.audio_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def workspace_path(self) -> Path:
        path = Path(self.workspace_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def safe_summary(self) -> dict[str, object]:
        return {
            "app_env": self.app_env,
            "app_host": self.app_host,
            "app_port": self.app_port,
            "database_url": self.database_url,
            "ollama_base_url": self.ollama_base_url,
            "ollama_model": self.ollama_model,
            "stt_provider": self.stt_provider,
            "tts_provider": self.tts_provider,
            "default_voice_name": self.default_voice_name,
            "audio_input_device": self.audio_input_device,
            "stt_default_language": self.stt_default_language,
            "stt_auto_detect_language": self.stt_auto_detect_language,
            "require_confirmation_for_risky_actions": self.require_confirmation_for_risky_actions,
            "allowed_apps": self.allowed_apps,
            "spotify_enabled": self.spotify_enabled,
            "ocr_enabled": self.ocr_enabled,
            "screen_analysis_enabled": self.screen_analysis_enabled,
        }

    def to_json(self) -> str:
        return json.dumps(self.safe_summary(), indent=2)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env = _load_env_files()
    values = {
        "app_env": env.get("APP_ENV", "development"),
        "app_host": env.get("APP_HOST", "127.0.0.1"),
        "app_port": int(env.get("APP_PORT", "8000")),
        "cors_origins_raw": env.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,tauri://localhost"),
        "database_url": env.get("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'jarvis.db').as_posix()}"),
        "ollama_base_url": env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        "ollama_model": env.get("OLLAMA_MODEL", "llama3.1:8b"),
        "stt_provider": env.get("STT_PROVIDER", "faster-whisper"),
        "tts_provider": env.get("TTS_PROVIDER", "edge"),
        "default_voice_name": env.get("DEFAULT_VOICE_NAME", "Daniel"),
        "require_confirmation_for_risky_actions": _to_bool(env.get("REQUIRE_CONFIRMATION_FOR_RISKY_ACTIONS"), True),
        "allowed_apps_raw": env.get(
            "ALLOWED_APPS",
            (
                "Safari,Spotify,Finder,Terminal,Notes,TextEdit,Music,Messages,Google Chrome,Arc,App Store,"
                "Calculator,Preview,Visual Studio Code,Mail,Calendar,Reminders,System Settings,"
                "Photos,Slack,Discord,zoom.us"
            ),
        ),
        "spotify_enabled": _to_bool(env.get("SPOTIFY_ENABLED"), True),
        "ocr_enabled": _to_bool(env.get("OCR_ENABLED"), False),
        "screen_analysis_enabled": _to_bool(env.get("SCREEN_ANALYSIS_ENABLED"), False),
        "piper_model_path": env.get("PIPER_MODEL_PATH", str(ROOT_DIR / "piper_voices" / "en_GB-alan-medium.onnx")),
        "edge_voice_name": env.get("EDGE_VOICE_NAME", "en-GB-RyanNeural"),
        "fast_edge_voice_name": env.get("FAST_EDGE_VOICE_NAME", "en-US-GuyNeural"),
        "whisper_model_name": env.get("WHISPER_MODEL_NAME", "base"),
        "whisper_basic_model_name": env.get("WHISPER_BASIC_MODEL_NAME", "tiny"),
        "whisper_advanced_model_name": env.get("WHISPER_ADVANCED_MODEL_NAME", "base"),
        "whisper_compute_type": env.get("WHISPER_COMPUTE_TYPE", "int8"),
        "stt_default_language": env.get("STT_DEFAULT_LANGUAGE", "en"),
        "stt_auto_detect_language": _to_bool(env.get("STT_AUTO_DETECT_LANGUAGE"), False),
        "audio_input_device": env.get("AUDIO_INPUT_DEVICE"),
        "audio_dir": env.get("AUDIO_DIR", str(ROOT_DIR / "audio")),
        "workspace_dir": env.get("WORKSPACE_DIR", str(ROOT_DIR / "workspace")),
        "memory_seed_file": env.get("MEMORY_SEED_FILE", str(ROOT_DIR / "memory.json")),
        "notes_seed_file": env.get("NOTES_SEED_FILE", str(ROOT_DIR / "notes.json")),
        "status_seed_file": env.get("STATUS_SEED_FILE", str(ROOT_DIR / "status.json")),
        "action_audit_log": env.get("ACTION_AUDIT_LOG", str(DATA_DIR / "action_audit.log")),
        "frontend_backend_url": env.get("FRONTEND_BACKEND_URL", "http://127.0.0.1:8000"),
        "session_summary_message_limit": int(env.get("SESSION_SUMMARY_MESSAGE_LIMIT", "12")),
        "assistant_response_timeout_seconds": float(env.get("ASSISTANT_RESPONSE_TIMEOUT_SECONDS", "25.0")),
        "speech_max_seconds": float(env.get("SPEECH_MAX_SECONDS", "8.5")),
        "speech_silence_seconds": float(env.get("SPEECH_SILENCE_SECONDS", "0.62")),
        "speech_min_speech_seconds": float(env.get("SPEECH_MIN_SPEECH_SECONDS", "0.24")),
        "speech_silence_threshold": float(env.get("SPEECH_SILENCE_THRESHOLD", "0.009")),
        "wake_word": env.get("WAKE_WORD", "jarvis"),
        "reminder_poll_seconds": int(env.get("REMINDER_POLL_SECONDS", "20")),
        "wake_word_load_threshold_ratio": float(env.get("WAKE_WORD_LOAD_THRESHOLD_RATIO", "0.8")),
    }
    return Settings(**values)


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def _load_env_files() -> dict[str, str]:
    env = dict(os.environ)
    for path in (ROOT_DIR / ".env", BACKEND_DIR / ".env"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            env.setdefault(key.strip(), value.strip())
    return env


def _to_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
