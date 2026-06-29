"""
kiln.tui.ws_bridge — remote bridge (KILN-054): the TUI over a WS server, not a local engine.

Same surface as the in-process `Bridge` (`submit` / `drain_output` / `poll_output`), so `KilnApp`'s
`_render` and the status/needs panel are reused **unchanged** — the server→client events carry the
exact same `kind`s the local `Bridge` does. A background thread connects to `ws://…/agent/{id}`,
receives the agent's events, and puts them on the outbox the UI drains; `submit()` sends the typed
line as `user.message` (or `command`, for `/lines`). The `snapshot` is expanded into initial
scrollback (status + recent turns) using the same kinds `_render` understands.

`websockets` is imported lazily inside `start()` — so a plain `--tui` (no `--remote`) needs only the
`[tui]` extra; `--remote` additionally needs `websockets` (ships with the `[server]` extra).
"""

from __future__ import annotations

import queue
import threading

from kiln.history import ROLE_BOT
from server.protocol import build_client, decode


class WsBridge:
    """Drop-in for `Bridge` that talks to a remote agent over a WebSocket."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._outbox: queue.Queue[dict] = queue.Queue()
        self._ws = None  # set once connected (websockets sync ClientConnection)
        self._connected = threading.Event()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> WsBridge:
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, name="ws-bridge", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        from websockets.sync.client import connect

        try:
            with connect(self._url) as ws:
                self._ws = ws
                self._connected.set()
                for raw in ws:  # blocks; ends when the server closes the socket
                    self._ingest(raw)
        except Exception as exc:  # connection refused / dropped — surface as a notice, don't crash
            self._outbox.put({"kind": "notice", "text": f"(disconnected: {exc})"})
        finally:
            self._connected.set()  # unblock any waiter even if the connect failed
            self._closed.set()

    def wait_connected(self, timeout: float | None = None) -> bool:
        return self._connected.wait(timeout) and self._ws is not None

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()

    # --- engine→UI: render events -----------------------------------------
    def _ingest(self, raw: str) -> None:
        event = decode(raw)
        if event.get("kind") == "snapshot":
            self._expand_snapshot(event)
        else:
            self._outbox.put(event)

    def _expand_snapshot(self, snap: dict) -> None:
        """Turn the one-shot snapshot into scrollback: a status event + one event per recent turn,
        all in the kinds `_render`/`_update_status` already handle."""
        status = snap.get("status")
        if status:
            self._outbox.put({"kind": "status", "snapshot": status})
        for turn in snap.get("history", []):
            text = turn.get("text", "")
            if turn.get("role") == ROLE_BOT:
                self._outbox.put({"kind": "agent", "text": text})
            else:
                self._outbox.put({"kind": "user", "text": text})

    def poll_output(self) -> dict | None:
        try:
            return self._outbox.get_nowait()
        except queue.Empty:
            return None

    def drain_output(self) -> list[dict]:
        events: list[dict] = []
        while (event := self.poll_output()) is not None:
            events.append(event)
        return events

    # --- UI→engine: input --------------------------------------------------
    def submit(self, line: str) -> None:
        """Send the typed line — a slash line as `command`, otherwise `user.message`."""
        mtype = "command" if line.startswith("/") else "user.message"
        if self._ws is not None:
            self._ws.send(build_client(mtype, line))
