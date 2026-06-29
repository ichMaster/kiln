"""
kiln tick-server — the WS connection lifecycle (KILN-053).

`serve_agent()` runs one client's session on an agent:
  accept → send a `snapshot` → subscribe the connection to the agent's hub → relay inbound
  `user.message`/`command` onto the agent's inbox → on disconnect, unsubscribe.

The agent's `BroadcastHub` is driven from the **engine thread**; the WS handler is **async**. The
sink hops each event from the engine thread onto the asyncio loop (`call_soon_threadsafe`); a pump
task drains it to the socket — so a slow socket never blocks the engine, and a blocking engine tick
never blocks the socket. Disconnecting one client leaves the agent (and any other client) untouched.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import WebSocket, WebSocketDisconnect

from .protocol import decode, encode, parse_client, snapshot_event
from .runtime import AgentRuntime


async def serve_agent(ws: WebSocket, runtime: AgentRuntime, *, history_limit: int = 20) -> None:
    """Drive one WS client attached to `runtime` until it disconnects."""
    await ws.accept()
    loop = asyncio.get_running_loop()
    outgoing: asyncio.Queue[dict] = asyncio.Queue()

    def sink(event: dict) -> None:
        # Called from the ENGINE thread. Hop onto the loop; if the loop is gone (client closing),
        # call_soon_threadsafe raises → the BroadcastHub drops this sink. Never blocks the engine.
        loop.call_soon_threadsafe(outgoing.put_nowait, event)

    # One-shot snapshot BEFORE subscribing, so the client has state before the live stream starts.
    # The transcript read is off-loop (it touches the store file).
    status = runtime.latest_status()
    history = await loop.run_in_executor(None, runtime.recent_history, history_limit)
    await ws.send_text(encode(snapshot_event(status, history)))

    unsubscribe = runtime.subscribe(sink)
    pump = asyncio.create_task(_pump(ws, outgoing))
    try:
        while True:
            raw = await ws.receive_text()
            try:
                mtype, text = parse_client(decode(raw))
            except ValueError:
                continue  # ignore a malformed frame (JSONDecodeError is a ValueError)
            if mtype in ("user.message", "command"):
                runtime.submit(text)
            # 'attach' → no-op (the snapshot was already sent)
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe()  # the agent keeps ticking; only this client's stream ends
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump


async def _pump(ws: WebSocket, outgoing: asyncio.Queue) -> None:
    """Drain the loop-side queue and write each event to the socket."""
    while True:
        event = await outgoing.get()
        await ws.send_text(encode(event))
