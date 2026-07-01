"""
kiln — input channels. Input is abstracted behind a `poll() -> str | None` seam so the tick loop
never blocks waiting for a message: `ScriptedChannel` (tick→text map, deterministic, for the demo +
tests) and `StdinChannel` (a daemon thread reads stdin into a queue). A leaf module (stdlib only);
the server's `ServerChannel` (server/bus.py) implements the same seam over the network.
"""

from __future__ import annotations

import queue
import sys
import threading


class ScriptedChannel:
    """Deterministic channel for tests: input is keyed to tick numbers."""

    def __init__(self, inputs: dict[int, str] | None = None):
        self._inputs = inputs or {}
        self._tick = -1

    def poll(self) -> str | None:
        self._tick += 1
        return self._inputs.get(self._tick)


class StdinChannel:
    """
    Live channel: a background daemon thread reads stdin and queues lines.
    `poll()` non-blockingly pulls the next line (or None if empty), so the
    tick loop never stalls waiting for input.
    """

    def __init__(self):
        self._q: queue.Queue[str] = queue.Queue()
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def _reader(self) -> None:
        while True:
            line = sys.stdin.readline()
            if line == "":  # EOF
                break
            line = line.strip()
            if line:
                self._q.put(line)

    def poll(self) -> str | None:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None
