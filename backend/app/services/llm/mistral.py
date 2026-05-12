from __future__ import annotations

import time
from typing import Any

import httpx

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger


logger = get_logger(__name__)


class MistralService:
    """Small HTTP client for Mistral chat completions.

    The API key is read from environment-backed settings only. It is never
    logged, returned to the frontend, or stored in committed files.
    """

    async def generate(self, *, prompt: str, system_prompt: str, history: list[dict[str, object]], mode: str = "basic") -> str:
        settings = get_settings()
        api_key = settings.mistral_api_key.strip()
        if not api_key:
            raise RuntimeError("Mistral API key is not configured")

        started = time.perf_counter()
        history_limit = min(settings.session_summary_message_limit, 4) if mode == "basic" else settings.session_summary_message_limit
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for item in history[-history_limit:]:
            role = str(item.get("role", "user")).strip().lower()
            if role not in {"user", "assistant", "system"}:
                role = "user"
            content = str(item.get("content", "")).strip()
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": prompt})

        prompt_word_count = len(str(prompt).split())
        max_tokens = self._max_tokens(prompt_word_count, mode)
        payload: dict[str, Any] = {
            "model": settings.mistral_model,
            "messages": messages,
            "temperature": 0.35 if mode == "basic" else 0.45,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=settings.assistant_response_timeout_seconds) as client:
            response = await client.post(settings.mistral_base_url.rstrip("/") + "/chat/completions", json=payload, headers=headers)
            response.raise_for_status()

        data = response.json()
        text = self._extract_text(data)
        logger.info(
            "timing stage=llm provider=mistral mode=%s duration_ms=%.1f prompt_words=%s response_chars=%s",
            mode,
            (time.perf_counter() - started) * 1000,
            prompt_word_count,
            len(text),
        )
        return text

    def _max_tokens(self, prompt_word_count: int, mode: str) -> int:
        if mode == "advanced":
            if prompt_word_count <= 6:
                return 120
            if prompt_word_count <= 20:
                return 180
            return 260
        if prompt_word_count <= 6:
            return 56
        if prompt_word_count <= 20:
            return 90
        return 128

    def _extract_text(self, data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content", "")
        if isinstance(content, str):
            return " ".join(content.split())
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return " ".join(" ".join(parts).split())
        return ""


mistral_service = MistralService()
