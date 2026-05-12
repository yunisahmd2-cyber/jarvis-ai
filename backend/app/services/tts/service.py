from __future__ import annotations

import asyncio
import subprocess
import time
import uuid
from pathlib import Path

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.services.memory.service import memory_service

try:
    import edge_tts  # type: ignore
except Exception:  # pragma: no cover
    edge_tts = None


logger = get_logger(__name__)


class TtsService:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, str, str], str] = {}
        self._synthesis_lock = asyncio.Lock()

    async def synthesize(self, text: str, voice_name: str | None = None) -> dict[str, str | None]:
        started = time.perf_counter()
        text = " ".join(text.split())
        if not text:
            return {"audio_url": None, "provider": get_settings().tts_provider}

        settings = get_settings()
        provider, resolved_voice = self._resolve_voice_path(voice_name)
        cache_key = (provider, resolved_voice, text)
        cached = self._cache.get(cache_key)
        if cached:
            path = get_settings().audio_path / Path(cached).name
            if path.exists():
                logger.info(
                    "timing stage=tts provider=%s duration_ms=%.1f chars=%s cache=hit",
                    provider,
                    (time.perf_counter() - started) * 1000,
                    len(text),
                )
                return {"audio_url": cached, "provider": provider}
            self._cache.pop(cache_key, None)

        async with self._synthesis_lock:
            cached = self._cache.get(cache_key)
            if cached:
                path = get_settings().audio_path / Path(cached).name
                if path.exists():
                    logger.info(
                        "timing stage=tts provider=%s duration_ms=%.1f chars=%s cache=hit",
                        provider,
                        (time.perf_counter() - started) * 1000,
                        len(text),
                    )
                    return {"audio_url": cached, "provider": provider}
                self._cache.pop(cache_key, None)

            if provider == "edge":
                audio_url = await self._with_edge(text, resolved_voice)
            elif provider == "piper":
                audio_url = self._with_piper(text)
            else:
                audio_url = self._with_macsay(text, resolved_voice)

            if len(text) <= 120:
                self._cache[cache_key] = audio_url
        logger.info(
            "timing stage=tts provider=%s duration_ms=%.1f chars=%s cache=miss",
            provider,
            (time.perf_counter() - started) * 1000,
            len(text),
        )
        return {"audio_url": audio_url, "provider": provider}

    def _resolve_voice_path(self, requested_voice_name: str | None) -> tuple[str, str]:
        settings = get_settings()
        preferred_mode = (
            memory_service.get_preference("voice_mode", "realistic") or "realistic"
        ).strip().lower()

        if preferred_mode == "piper":
            return "piper", requested_voice_name or settings.default_voice_name
        if preferred_mode == "fast":
            return "edge", requested_voice_name or settings.fast_edge_voice_name
        if preferred_mode == "macsay":
            return "macsay", requested_voice_name or settings.default_voice_name

        return "edge", requested_voice_name or settings.edge_voice_name

    async def _with_edge(self, text: str, voice_name: str) -> str:
        if edge_tts is None:
            raise RuntimeError("edge-tts is not installed for realistic voice mode")
        path = get_settings().audio_path / f"{uuid.uuid4().hex}.mp3"
        communicate = edge_tts.Communicate(text=text, voice=voice_name)
        await communicate.save(str(path))
        return self._audio_url(path)

    def _with_piper(self, text: str) -> str:
        settings = get_settings()
        path = settings.audio_path / f"{uuid.uuid4().hex}.wav"
        proc = subprocess.Popen(
            ["python3", "-m", "piper", "--model", settings.piper_model_path, "--output_file", str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert proc.stdin is not None
        proc.stdin.write(text)
        proc.stdin.close()
        proc.wait(timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.read() if proc.stderr else "Piper synthesis failed")
        return self._audio_url(path)

    def _with_macsay(self, text: str, voice_name: str) -> str:
        path = get_settings().audio_path / f"{uuid.uuid4().hex}.aiff"
        subprocess.run(["say", "-v", voice_name, "-o", str(path), text], check=True, capture_output=True, text=True)
        return self._audio_url(path)

    def _audio_url(self, path: Path) -> str:
        return f"{get_settings().frontend_backend_url.rstrip('/')}/audio/{path.name}"


tts_service = TtsService()
