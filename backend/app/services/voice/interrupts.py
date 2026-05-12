from __future__ import annotations


class InterruptRegistry:
    def __init__(self) -> None:
        self._interrupted_sessions: set[str] = set()

    def interrupt(self, session_id: str) -> None:
        self._interrupted_sessions.add(session_id)

    def consume(self, session_id: str) -> bool:
        if session_id in self._interrupted_sessions:
            self._interrupted_sessions.remove(session_id)
            return True
        return False


interrupt_registry = InterruptRegistry()
