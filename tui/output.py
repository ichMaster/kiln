"""
kiln.tui.output — Output seam implementation that writes to the bridge outbox.

Mirror of ConsoleOutput, but instead of print it places render events in bridge.outbox; the
UI drains and draws them. Echo-free: user() is a no-op, because the UI shows the typed line
itself (on submit), and the engine must not echo it (otherwise — double display).
"""

from __future__ import annotations

from .bridge import Bridge


class TuiOutput:
    """Output sink for the TUI: render events -> bridge.outbox."""

    def __init__(self, bridge: Bridge) -> None:
        self._bridge = bridge

    def user(self, text: str) -> None:
        # Echo-free: UI already showed the typed text; the engine does NOT echo input.
        return

    def agent(self, text: str, *, is_self: bool = False, lead: bool = False) -> None:
        self._bridge.emit({"kind": "agent", "text": text, "is_self": is_self, "lead": lead})

    def usage(self, usage: dict | None) -> None:
        # Carry the raw record {model, input, output, total} — the UI draws the tech line.
        self._bridge.emit({"kind": "usage", "usage": usage})

    def notice(self, text: str) -> None:
        self._bridge.emit({"kind": "notice", "text": text})
