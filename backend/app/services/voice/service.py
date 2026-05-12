from __future__ import annotations

import contextlib
import threading
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
import soundfile as sf

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger


logger = get_logger(__name__)

try:
    from faster_whisper import WhisperModel  # type: ignore
except Exception:  # pragma: no cover
    WhisperModel = None


class VoiceService:
    def __init__(self) -> None:
        self._models: dict[str, Any] = {}
        self._recording_lock = threading.Lock()

    def _resolve_input_device(self) -> int | None:
        settings = get_settings()
        devices = sd.query_devices()
        input_devices = [
            (index, device)
            for index, device in enumerate(devices)
            if int(device.get("max_input_channels", 0) or 0) > 0
        ]
        if not input_devices:
            raise RuntimeError("No microphone input device is available.")

        configured = (settings.audio_input_device or "").strip()
        if configured:
            normalized = configured.lower()
            for index, device in input_devices:
                name = str(device.get("name", ""))
                if normalized == str(index) or normalized in name.lower():
                    return index
            raise RuntimeError(
                f"Configured AUDIO_INPUT_DEVICE '{settings.audio_input_device}' was not found."
            )

        preferred_names = ("MacBook", "Built-in Microphone", "Built-in", "Microphone")
        for preferred in preferred_names:
            for index, device in input_devices:
                if preferred.lower() in str(device.get("name", "")).lower():
                    return index

        default_device = sd.default.device
        default_input = None
        if isinstance(default_device, (list, tuple)) and default_device:
            default_input = default_device[0]
        elif isinstance(default_device, int):
            default_input = default_device

        if isinstance(default_input, int) and default_input >= 0:
            try:
                device = devices[default_input]
                if int(device.get("max_input_channels", 0) or 0) > 0:
                    return default_input
            except Exception:
                pass

        return int(input_devices[0][0])

    def _current_model_name(self) -> str:
        settings = get_settings()
        mode = "advanced"
        try:
            from backend.app.services.memory.service import memory_service

            mode = memory_service.get_power_mode()
        except Exception:
            mode = "basic"

        if mode == "basic":
            return settings.whisper_basic_model_name
        return settings.whisper_advanced_model_name or settings.whisper_model_name

    def _ensure_model(self) -> Any:
        settings = get_settings()
        if settings.stt_provider != "faster-whisper":
            raise RuntimeError(f"Unsupported STT provider: {settings.stt_provider}")
        if WhisperModel is None:
            raise RuntimeError("faster-whisper is not installed")
        model_name = self._current_model_name()
        if model_name not in self._models:
            self._models[model_name] = WhisperModel(model_name, compute_type=settings.whisper_compute_type)
        return self._models[model_name]

    def _transcribe_options(self) -> dict[str, Any]:
        settings = get_settings()
        options: dict[str, Any] = {
            "vad_filter": True,
            "condition_on_previous_text": False,
        }
        mode = "basic"
        try:
            from backend.app.services.memory.service import memory_service

            mode = memory_service.get_power_mode()
        except Exception:
            mode = "basic"

        default_language = (settings.stt_default_language or "en").strip().lower()
        auto_detect = settings.stt_auto_detect_language and mode == "advanced"
        if not auto_detect and default_language:
            options["language"] = default_language
        return options

    def transcribe_file(self, audio_path: Path) -> str:
        started = time.perf_counter()
        model = self._ensure_model()
        segments, _info = model.transcribe(str(audio_path), **self._transcribe_options())
        text = " ".join(segment.text for segment in segments)
        cleaned = " ".join(text.split())
        logger.info(
            "timing stage=stt duration_ms=%.1f chars=%s",
            (time.perf_counter() - started) * 1000,
            len(cleaned),
        )
        return cleaned

    def record_audio_until_silence(self) -> Path | None:
        if not self._recording_lock.acquire(blocking=False):
            raise RuntimeError("Microphone recording is already active.")
        try:
            return self._record_audio_until_silence_locked()
        finally:
            self._recording_lock.release()

    def _record_audio_until_silence_locked(self) -> Path | None:
        settings = get_settings()
        mode = "basic"
        try:
            from backend.app.services.memory.service import memory_service

            mode = memory_service.get_power_mode()
        except Exception:
            mode = "basic"

        samplerate = 16000
        channels = 1
        # Smaller chunks reduce end-of-speech latency without adding a background loop.
        chunk_seconds = 0.08
        chunk_samples = int(samplerate * chunk_seconds)
        max_seconds = settings.speech_max_seconds
        if mode == "basic":
            max_seconds = min(max_seconds, 8.5)
        else:
            max_seconds = max(max_seconds, 9.5)
        total_chunks_allowed = max(1, int(max_seconds / chunk_seconds))
        speech_chunks_needed = max(1, int(settings.speech_min_speech_seconds / chunk_seconds))

        frames: list[np.ndarray] = []
        heard_speech = False
        silent_chunks = 0
        speech_chunks = 0
        smoothed_rms = settings.speech_silence_threshold
        device = self._resolve_input_device()

        with sd.InputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="float32",
            device=device,
        ) as stream:
            for _ in range(total_chunks_allowed):
                data, overflow = stream.read(chunk_samples)
                if overflow:
                    pass
                frames.append(np.copy(data))

                rms = float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0
                smoothed_rms = (smoothed_rms * 0.82) + (rms * 0.18)
                adaptive_threshold = max(
                    settings.speech_silence_threshold,
                    min(smoothed_rms * 0.45, settings.speech_silence_threshold * 3.0),
                )
                if rms > adaptive_threshold:
                    heard_speech = True
                    speech_chunks += 1
                    silent_chunks = 0
                elif heard_speech:
                    silent_chunks += 1

                dynamic_silence_seconds = settings.speech_silence_seconds
                if speech_chunks >= max(3, speech_chunks_needed * 2):
                    dynamic_silence_seconds *= 0.9
                if speech_chunks <= speech_chunks_needed + 1 and heard_speech:
                    dynamic_silence_seconds *= 0.95
                # Keep a short pause allowance so natural phrasing is not clipped.
                if mode == "advanced":
                    dynamic_silence_seconds = max(0.58, dynamic_silence_seconds)
                else:
                    dynamic_silence_seconds = max(0.52, dynamic_silence_seconds)
                silence_chunks_needed = max(1, int(dynamic_silence_seconds / chunk_seconds))
                if heard_speech and speech_chunks >= speech_chunks_needed and silent_chunks >= silence_chunks_needed:
                    break

        if not frames:
            return None

        audio = np.concatenate(frames, axis=0)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            temp_path = Path(tmp.name)
        sf.write(str(temp_path), audio, samplerate)
        return temp_path

    def record_until_silence(self) -> str:
        temp_path = self.record_audio_until_silence()
        if temp_path is None:
            return ""
        try:
            return self.transcribe_file(temp_path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temp_path.unlink()


voice_service = VoiceService()
