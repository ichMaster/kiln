"""
kiln.tui.bridge — echo-free bridge between the tick loop and the UI thread.

Two INDEPENDENT queues (thread-safe, since the engine runs in a background thread and the
UI in its own):
  - inbox  (UI -> engine): user-typed lines; the engine reads them via
    TuiChannel.poll() (KILN-009);
  - outbox (engine -> UI): render events placed by TuiOutput (KILN-008), which the UI
    drains and draws.

**Echo-free by construction:** input goes ONLY to inbox, render ONLY to outbox; a typed
line never "echoes" back into outbox. So the UI shows the typed text itself (once), while
the engine writes ONLY ITS OWN replies to outbox — no double echo and no mixing of input
with output.

A render event is a plain dict (foreshadowing the server event protocol v1.1/1.2); the
key `kind` ∈ {"user", "agent", "usage", "notice"} mirrors the Output seam's methods:
  {"kind": "user",   "text": str}
  {"kind": "agent",  "text": str, "is_self": bool, "lead": bool}
  {"kind": "usage",  "usage": dict | None}
  {"kind": "notice", "text": str}
"""

from __future__ import annotations

import queue


class Bridge:
    """Thread-safe inbox/outbox between engine and UI (no agent logic)."""

    def __init__(self) -> None:
        self._inbox: queue.Queue[str] = queue.Queue()  # UI -> engine (lines)
        self._outbox: queue.Queue[dict] = queue.Queue()  # engine -> UI (render events)

    # --- UI side: input -> engine ------------------------------------------
    def submit(self, line: str) -> None:
        """UI places a typed line for the engine."""
        self._inbox.put(line)

    # --- engine side: read input (non-blocking) ----------------------------
    def poll_input(self) -> str | None:
        """Engine (TuiChannel) takes the next line or None, without blocking the loop."""
        try:
            return self._inbox.get_nowait()
        except queue.Empty:
            return None

    # --- engine side: render -> UI -----------------------------------------
    def emit(self, event: dict) -> None:
        """Engine (TuiOutput) places a render event for the UI."""
        self._outbox.put(event)

    # --- UI side: read render (non-blocking) -------------------------------
    def poll_output(self) -> dict | None:
        """UI takes the next render event or None."""
        try:
            return self._outbox.get_nowait()
        except queue.Empty:
            return None

    def drain_output(self) -> list[dict]:
        """All available render events at once (UI draws them in one pass)."""
        events: list[dict] = []
        while (event := self.poll_output()) is not None:
            events.append(event)
        return events
