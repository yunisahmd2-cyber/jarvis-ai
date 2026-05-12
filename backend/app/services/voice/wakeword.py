from __future__ import annotations

import os

from backend.app.core.config import get_settings
from backend.app.services.memory.service import memory_service


class WakeWordService:
    def desired_enabled(self) -> bool:
        return memory_service.get_wake_word_desired_enabled()

    def load_paused(self) -> bool:
        settings = get_settings()
        try:
            cpu_count = max(1, os.cpu_count() or 1)
            load_avg = os.getloadavg()[0]
        except (AttributeError, OSError):
            return False
        threshold = cpu_count * settings.wake_word_load_threshold_ratio
        return load_avg >= threshold

    def effective_enabled(self) -> bool:
        if memory_service.get_power_mode() != "advanced":
            return False
        if not self.desired_enabled():
            return False
        if self.load_paused():
            return False
        return True

    def status(self) -> dict[str, object]:
        settings = get_settings()
        power_mode = memory_service.get_power_mode()
        desired = self.desired_enabled()
        load_paused = self.load_paused()
        effective = self.effective_enabled()
        if power_mode != "advanced":
            reason = "Wake word is paused in basic mode. Use manual activation or switch to advanced mode."
        elif not desired:
            reason = "Wake word is disabled. Jarvis still works through the orb and keyboard activation."
        elif load_paused:
            reason = "Wake word is temporarily paused because system load is high."
        else:
            reason = "Wake word is enabled for lightweight advanced-mode listening."
        return {
            "wake_word": settings.wake_word,
            "desired_enabled": desired,
            "effective_enabled": effective,
            "power_mode": power_mode,
            "listener_active": False,
            "load_paused": load_paused,
            "reason": reason,
        }

    def set_enabled(self, enabled: bool) -> dict[str, object]:
        memory_service.set_wake_word_desired_enabled(enabled)
        return self.status()


wake_word_service = WakeWordService()
