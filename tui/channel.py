"""
kiln.tui.channel — input channel over the bridge (mirror of StdinChannel).

poll() non-blockingly takes the next typed line from bridge.inbox (or None), so the tick
loop never stalls waiting for input — like StdinChannel, only the source differs (the
bridge queue filled by the UI, not stdin).
"""

from __future__ import annotations

from .bridge import Bridge


class TuiChannel:
    """Input channel for the TUI: poll() -> next line from bridge.inbox or None."""

    def __init__(self, bridge: Bridge) -> None:
        self._bridge = bridge

    def poll(self) -> str | None:
        return self._bridge.poll_input()
