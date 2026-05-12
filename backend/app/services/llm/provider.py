from __future__ import annotations

from dataclasses import dataclass

import httpx

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.services.llm.mistral import mistral_service
from backend.app.services.llm.ollama import ollama_service


logger = get_logger(__name__)


@dataclass(frozen=True)
class LLMResult:
    text: str
    source: str
    fallback_reason: str | None = None


class LLMProviderService:
    """Routes assistant text generation through the configured primary provider.

    Ollama remains the local-first path. Mistral can be used as a fallback when
    the local model is unavailable or returns no usable text.
    """

    async def generate(self, *, prompt: str, system_prompt: str, history: list[dict[str, object]], mode: str = "basic") -> LLMResult:
        settings = get_settings()
        primary = settings.llm_primary_provider.strip().lower()
        if primary == "mistral":
            fallback_reason: str | None = None
            try:
                text = await mistral_service.generate(prompt=prompt, system_prompt=system_prompt, history=history, mode=mode)
                if text.strip():
                    return LLMResult(text=text, source="mistral")
                fallback_reason = "empty_mistral_response"
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                fallback_reason = exc.__class__.__name__
                logger.warning("Mistral primary generation failed; falling back to Ollama: %s", exc.__class__.__name__)

            fallback_text = await ollama_service.generate(prompt=prompt, system_prompt=system_prompt, history=history, mode=mode)
            return LLMResult(text=fallback_text, source="ollama", fallback_reason=fallback_reason)

        text = await ollama_service.generate(prompt=prompt, system_prompt=system_prompt, history=history, mode=mode)
        if text.strip() and not self._looks_like_ollama_failure(text):
            return LLMResult(text=text, source="ollama")

        fallback_reason = "empty_ollama_response" if not text.strip() else "ollama_unreachable"
        if not settings.mistral_api_key.strip():
            return LLMResult(text=text, source="ollama", fallback_reason=fallback_reason)

        try:
            fallback_text = await mistral_service.generate(prompt=prompt, system_prompt=system_prompt, history=history, mode=mode)
            if fallback_text.strip():
                return LLMResult(text=fallback_text, source="mistral", fallback_reason=fallback_reason)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.warning("Mistral fallback generation failed after Ollama issue: %s", exc.__class__.__name__)
            return LLMResult(text=text, source="ollama", fallback_reason=f"mistral_fallback_failed:{exc.__class__.__name__}")

        return LLMResult(text=text, source="ollama", fallback_reason="empty_mistral_fallback_response")

    def _looks_like_ollama_failure(self, text: str) -> bool:
        normalized = text.strip().lower()
        return "can't reach the local ollama service" in normalized or "cannot reach the local ollama service" in normalized


llm_provider_service = LLMProviderService()
