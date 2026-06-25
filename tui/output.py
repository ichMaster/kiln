"""
kiln.tui.output — реалізація seam'а Output, що пише в outbox містка.

Дзеркало ConsoleOutput, але замість print кладе події рендера в bridge.outbox; UI
їх вичерпує й малює. Echo-free: user() — НЕ-операція, бо набраний рядок UI показує
сам (на submit), і двіжок не має його відлунювати (інакше — подвійний показ).
"""

from __future__ import annotations

from .bridge import Bridge


class TuiOutput:
    """Сінк Output для TUI: події рендера -> bridge.outbox."""

    def __init__(self, bridge: Bridge) -> None:
        self._bridge = bridge

    def user(self, text: str) -> None:
        # Echo-free: UI вже показав набране; двіжок ввід НЕ відлунює.
        return

    def agent(self, text: str, *, is_self: bool = False, lead: bool = False) -> None:
        self._bridge.emit({"kind": "agent", "text": text, "is_self": is_self, "lead": lead})

    def usage(self, usage: dict | None) -> None:
        # Несемо сирий запис {model, input, output, total} — UI відмалює тех-рядок.
        self._bridge.emit({"kind": "usage", "usage": usage})

    def notice(self, text: str) -> None:
        self._bridge.emit({"kind": "notice", "text": text})
