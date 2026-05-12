from __future__ import annotations

import base64
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.services.memory.service import memory_service


logger = get_logger(__name__)


class VisionService:
    def _normalize_base64(self, screenshot_base64: str) -> tuple[str, str]:
        payload = screenshot_base64.strip()
        if payload.startswith("data:"):
            header, encoded = payload.split(",", 1)
            mime_type = header.split(";", 1)[0].removeprefix("data:")
            return encoded, mime_type
        return payload, "image/png"

    def _png_dimensions(self, binary: bytes) -> tuple[int, int] | None:
        if len(binary) < 24 or binary[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        width = int.from_bytes(binary[16:20], "big")
        height = int.from_bytes(binary[20:24], "big")
        return width, height

    def _ocr_timeout(self, mode: str) -> float:
        return 2.6 if mode == "basic" else 5.5

    def _run_tesseract_ocr(self, image_path: Path, *, timeout_seconds: float) -> tuple[str | None, str]:
        if shutil.which("tesseract") is None:
            return None, "unavailable"

        command = ["tesseract", str(image_path), "stdout", "-l", "eng", "--psm", "6"]
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Tesseract OCR timed out for %s", image_path)
            return None, "timeout"
        except Exception:
            logger.exception("Tesseract OCR failed unexpectedly for %s", image_path)
            return None, "error"

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            logger.warning("Tesseract OCR returned non-zero exit code: %s", stderr)
            return None, "failed"

        text = (proc.stdout or "").strip()
        return text, "ok"

    def _summarize_ocr_text(self, ocr_text: str, *, max_lines: int = 5, max_chars: int = 420) -> str:
        lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
        if not lines:
            return ""
        condensed = " | ".join(lines[:max_lines])
        condensed = re.sub(r"\s+", " ", condensed).strip()
        if len(condensed) > max_chars:
            return condensed[: max_chars - 1].rstrip() + "…"
        return condensed

    def _key_ocr_lines(self, ocr_text: str, *, limit: int = 6) -> list[str]:
        lines = []
        for line in ocr_text.splitlines():
            cleaned = re.sub(r"\s+", " ", line).strip()
            if len(cleaned) < 4:
                continue
            if cleaned.lower() in {"|", "-", "_"}:
                continue
            if cleaned not in lines:
                lines.append(cleaned)
            if len(lines) >= limit:
                break
        return lines

    def analyze_screenshot(self, screenshot_base64: str, prompt: str | None = None) -> dict[str, Any]:
        settings = get_settings()
        mode = memory_service.get_power_mode()
        try:
            normalized, mime_type = self._normalize_base64(screenshot_base64)
            binary = base64.b64decode(normalized)
        except Exception as exc:  # pragma: no cover
            return {"summary": f"Invalid screenshot payload: {exc}", "ocr_text": None, "metadata": {"enabled": settings.screen_analysis_enabled}}

        images_dir = settings.workspace_path / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output_path = images_dir / f"screenshot-{timestamp}.png"
        output_path.write_bytes(binary)

        dimensions = self._png_dimensions(binary)

        if not settings.screen_analysis_enabled:
            dimension_text = ""
            if dimensions:
                dimension_text = f" The captured image is {dimensions[0]}x{dimensions[1]} pixels."
            return {
                "summary": "Screenshot captured successfully, but screen analysis is disabled in config." + dimension_text,
                "ocr_text": None,
                "metadata": {
                    "enabled": False,
                    "analysis_level": "disabled",
                    "ocr_enabled": settings.ocr_enabled,
                    "ocr_state": "disabled",
                    "ocr_engine": None,
                    "ocr_characters": 0,
                    "ocr_key_lines": [],
                    "saved_path": str(output_path),
                    "mime_type": mime_type,
                    "bytes": len(binary),
                    "mode": mode,
                    "dimensions": {"width": dimensions[0], "height": dimensions[1]} if dimensions else None,
                },
            }

        ocr_text: str | None = None
        ocr_engine = None
        ocr_state = "disabled"
        if settings.ocr_enabled:
            if mode == "basic" and len(binary) > 4_000_000:
                ocr_state = "skipped_for_thermal_safety"
            else:
                extracted, state = self._run_tesseract_ocr(output_path, timeout_seconds=self._ocr_timeout(mode))
                ocr_text = extracted if extracted else None
                ocr_state = state
                if state in {"ok", "failed", "timeout", "error"}:
                    ocr_engine = "tesseract"

        prompt_hint = f" Prompt: {prompt}." if prompt else ""
        dimension_hint = f" Dimensions: {dimensions[0]}x{dimensions[1]}." if dimensions else ""
        if ocr_text:
            excerpt = self._summarize_ocr_text(ocr_text)
            key_lines = self._key_ocr_lines(ocr_text)
            summary = (
                "Screenshot captured and analyzed."
                f"{dimension_hint}{prompt_hint} "
                f"I detected readable on-screen text ({len(key_lines)} key lines). "
                f"Key extracted content: {excerpt or 'text detected, but it was too sparse to summarize.'}"
            )
            suggested_next_actions = [
                "Summarize this screen text.",
                "Search based on this screen.",
                "Compare this with another source.",
            ]
        elif settings.ocr_enabled and ocr_state == "skipped_for_thermal_safety":
            summary = (
                "Screenshot captured successfully."
                f"{dimension_hint}{prompt_hint} "
                "OCR was skipped in Basic mode to keep system load low for this large frame."
            )
            suggested_next_actions = [
                "Use current page context for search.",
                "Try a smaller screen capture region.",
                "Switch to advanced mode for deeper on-demand analysis.",
            ]
        elif settings.ocr_enabled and ocr_state in {"failed", "timeout", "error"}:
            summary = (
                "Screenshot captured successfully."
                f"{dimension_hint}{prompt_hint} "
                "OCR attempted but did not return usable text, so I can only provide metadata-level screen context."
            )
            suggested_next_actions = [
                "Retry inspect screen.",
                "Use current page context instead.",
                "Search based on active app context.",
            ]
        elif settings.ocr_enabled and ocr_state == "unavailable":
            summary = (
                "Screenshot captured successfully."
                f"{dimension_hint}{prompt_hint} "
                "OCR is enabled in settings, but no local OCR engine is available on this machine. Install Tesseract to enable text extraction."
            )
            suggested_next_actions = [
                "Install Tesseract for local OCR.",
                "Use page/app context for now.",
                "Summarize current page instead.",
            ]
        else:
            summary = (
                "Screenshot captured and attached for analysis."
                f"{dimension_hint}{prompt_hint} "
                "Local screen analysis is currently metadata-only because OCR is disabled."
            )
            suggested_next_actions = [
                "Enable OCR for text extraction.",
                "Use current page context for summary.",
                "Search based on active app context.",
            ]

        key_lines = self._key_ocr_lines(ocr_text) if ocr_text else []
        return {
            "summary": summary,
            "ocr_text": ocr_text,
            "metadata": {
                "enabled": True,
                "ocr_enabled": settings.ocr_enabled,
                "ocr_state": ocr_state,
                "ocr_engine": ocr_engine,
                "ocr_characters": len(ocr_text or ""),
                "ocr_key_lines": key_lines,
                "suggested_next_actions": suggested_next_actions,
                "bytes": len(binary),
                "prompt": prompt,
                "mime_type": mime_type,
                "saved_path": str(output_path),
                "mode": mode,
                "analysis_level": "ocr_text" if ocr_text else "metadata",
                "dimensions": {"width": dimensions[0], "height": dimensions[1]} if dimensions else None,
            },
        }


vision_service = VisionService()
