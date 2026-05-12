from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger


logger = get_logger(__name__)


class OllamaService:
    def _candidate_base_urls(self, configured: str) -> list[str]:
        values: list[str] = []

        def add(url: str) -> None:
            cleaned = url.rstrip("/")
            if cleaned and cleaned not in values:
                values.append(cleaned)

        add(configured)
        parsed = urlsplit(configured)
        if parsed.hostname == "127.0.0.1":
            add(urlunsplit((parsed.scheme, f"localhost:{parsed.port or 11434}", parsed.path, parsed.query, parsed.fragment)))
        elif parsed.hostname == "localhost":
            add(urlunsplit((parsed.scheme, f"127.0.0.1:{parsed.port or 11434}", parsed.path, parsed.query, parsed.fragment)))
        return values

    async def _ensure_ollama_available(self, base_url: str) -> None:
        if shutil.which("ollama") is None:
            return
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            return
        await asyncio.sleep(1.2)

    async def generate(self, *, prompt: str, system_prompt: str, history: list[dict[str, object]], mode: str = "basic") -> str:
        settings = get_settings()
        started = time.perf_counter()
        history_limit = min(settings.session_summary_message_limit, 4) if mode == "basic" else settings.session_summary_message_limit
        history_text = "\n".join(f"{item['role']}: {item['content']}" for item in history[-history_limit:])
        prompt_word_count = len(str(prompt).split())
        prompt_lower = str(prompt).strip().lower()
        action_like = prompt_lower.startswith(
            (
                "open ",
                "close ",
                "quit ",
                "switch ",
                "play ",
                "pause ",
                "next ",
                "previous ",
                "search ",
                "what app",
                "what page",
                "read ",
                "summarize ",
                "copy ",
                "type ",
                "set volume",
            )
        )
        if mode == "advanced":
            if action_like:
                num_predict = 96
            elif prompt_word_count <= 6:
                num_predict = 112
            elif prompt_word_count <= 20:
                num_predict = 156
            else:
                num_predict = 220
            temperature = 0.5
            num_ctx = 4096
        else:
            if action_like:
                num_predict = 40
            elif prompt_word_count <= 6:
                num_predict = 48
            elif prompt_word_count <= 20:
                num_predict = 76
            else:
                num_predict = 108
            temperature = 0.4 if prompt_word_count <= 6 or action_like else 0.47
            num_ctx = 2048
        payload = {
            "model": settings.ollama_model,
            "prompt": f"{system_prompt}\n\nConversation context:\n{history_text}\n\nUser:\n{prompt}\n\nAssistant:",
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
                "top_p": 0.9,
                "repeat_penalty": 1.08,
                "stop": ["\nUser:", "\nuser:", "\nAssistant:", "\nassistant:"],
            },
        }

        boot_attempted = False
        last_error: Exception | None = None
        for base_url in self._candidate_base_urls(settings.ollama_base_url):
            for _attempt in range(2):
                try:
                    async with httpx.AsyncClient(timeout=settings.assistant_response_timeout_seconds) as client:
                        response = await client.post(f"{base_url}/api/generate", json=payload)
                        response.raise_for_status()
                    data = response.json()
                    text = " ".join(str(data.get("response", "")).split())
                    logger.info(
                        "timing stage=llm provider=ollama mode=%s duration_ms=%.1f prompt_words=%s response_chars=%s",
                        mode,
                        (time.perf_counter() - started) * 1000,
                        prompt_word_count,
                        len(text),
                    )
                    return text
                except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                    last_error = exc
                    if not boot_attempted:
                        boot_attempted = True
                        await self._ensure_ollama_available(base_url)
                        continue
                except httpx.HTTPError as exc:
                    last_error = exc
                    break

        if last_error is not None:
            logger.info(
                "timing stage=llm provider=ollama mode=%s duration_ms=%.1f status=unreachable",
                mode,
                (time.perf_counter() - started) * 1000,
            )
            return "I can't reach the local Ollama service right now. Please wait a moment and try again."
        logger.info(
            "timing stage=llm provider=ollama mode=%s duration_ms=%.1f status=unreachable",
            mode,
            (time.perf_counter() - started) * 1000,
        )
        return "I can't reach the local Ollama service right now. Please wait a moment and try again."


ollama_service = OllamaService()
