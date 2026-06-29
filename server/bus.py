"""
kiln tick-server — the network bus (KILN-050): the network form of the v0 `Bridge`.

`engine.run(channel=…, output=…)` is unchanged: the server just gives it a `ServerChannel` (input,
drains an inbox queue) and a `ServerOutput` (render events → a `BroadcastHub` that fans out to every
attached client). Echo-free, exactly like the TUI bridge: `ServerOutput.user()` is a no-op — the
client shows the typed line itself, the server never echoes it back.

The event dicts are the same `kind`s as the `Bridge` outbox (`agent`/`usage`/`notice`/`status`,
carrying the v0.10/v0.11 reply flags), so a client renders identically whether it's the in-process
TUI bridge or a remote WS connection.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable

# A render-event sink — one per attached client (e.g. the WS layer's per-connection put_nowait).
Sink = Callable[[dict], None]


class BroadcastHub:
    """Fan-out of one agent's render events to every subscribed client. Thread-safe (the engine
    broadcasts from its own thread). **Best-effort:** a sink that raises is dropped and never blocks
    the engine thread or the other sinks — so a slow/dead client can't stall the agent. Sinks must
    be non-blocking (e.g. `queue.Queue.put_nowait`, which raises `queue.Full` on a slow client →
    that sink is dropped)."""

    def __init__(self) -> None:
        self._subs: set[Sink] = set()
        self._lock = threading.Lock()

    def subscribe(self, sink: Sink) -> Callable[[], None]:
        """Register a sink; returns an unsubscribe callable."""
        with self._lock:
            self._subs.add(sink)
        return lambda: self.unsubscribe(sink)

    def unsubscribe(self, sink: Sink) -> None:
        with self._lock:
            self._subs.discard(sink)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def broadcast(self, event: dict) -> None:
        """Fan `event` to every sink; a sink that raises is dropped (never propagates)."""
        with self._lock:
            sinks = list(self._subs)
        for sink in sinks:
            try:
                sink(event)
            except Exception:  # a dead/stuck client must not crash the engine thread
                self.unsubscribe(sink)


class ServerChannel:
    """Input channel for the server (the `Channel` seam — `poll() -> str | None`). Drains an inbox
    `queue.Queue` filled by the WS layer with `user.message`/`command` lines; non-blocking, so the
    tick loop never stalls. Identical contract to `TuiChannel`/`StdinChannel`."""

    def __init__(self, inbox: queue.Queue[str]) -> None:
        self._inbox = inbox

    def poll(self) -> str | None:
        try:
            return self._inbox.get_nowait()
        except queue.Empty:
            return None


class ServerOutput:
    """The `Output` seam over the network bus: each reply/usage/notice/status becomes a render event
    broadcast to every client. `user()` is a **no-op** (echo-free). Mirror of `TuiOutput`."""

    def __init__(self, hub: BroadcastHub) -> None:
        self._hub = hub

    def user(self, text: str) -> None:
        # Echo-free: the client shows the typed line itself; the engine does NOT echo input.
        return

    def agent(
        self,
        text: str,
        *,
        is_self: bool = False,
        lead: bool = False,
        model: str | None = None,
        is_thought: bool = False,
        is_curiosity: bool = False,
    ) -> None:
        self._hub.broadcast(
            {
                "kind": "agent",
                "text": text,
                "is_self": is_self,
                "lead": lead,
                "model": model,
                "is_thought": is_thought,
                "is_curiosity": is_curiosity,
            }
        )

    def usage(self, usage: dict | None, latency: float | None = None) -> None:
        self._hub.broadcast({"kind": "usage", "usage": usage, "latency": latency})

    def notice(self, text: str) -> None:
        self._hub.broadcast({"kind": "notice", "text": text})

    def status(self, snapshot: dict) -> None:
        self._hub.broadcast({"kind": "status", "snapshot": snapshot})
