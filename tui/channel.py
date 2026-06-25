"""
kiln.tui.channel — канал вводу над містком (дзеркало StdinChannel).

poll() неблокуюче забирає черговий набраний рядок з bridge.inbox (або None), тож
цикл тіків не зупиняється в очікуванні вводу — як і StdinChannel, лише джерело інше
(черга містка, яку наповнює UI, а не stdin).
"""

from __future__ import annotations

from .bridge import Bridge


class TuiChannel:
    """Канал вводу для TUI: poll() -> черговий рядок з bridge.inbox або None."""

    def __init__(self, bridge: Bridge) -> None:
        self._bridge = bridge

    def poll(self) -> str | None:
        return self._bridge.poll_input()
