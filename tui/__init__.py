"""
kiln.tui — terminal UI (Textual) client on top of the engine.

Thin client: holds NO agent logic. It talks to the tick loop only through an
echo-free Bridge (bridge.Bridge) — inbox (UI -> engine: typed lines) and outbox
(engine -> UI: render events). The engine itself (kiln.engine) does not import this package.
"""

from __future__ import annotations

from .bridge import Bridge

__all__ = ["Bridge"]
