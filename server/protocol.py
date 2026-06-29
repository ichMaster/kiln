"""
kiln tick-server — the WS event protocol (KILN-053): the client↔server contract.

**server → client** events are the agent's render events **verbatim** — the same `kind`s the
`Bridge`/`BroadcastHub` carry (`agent`/`usage`/`notice`/`status`), plus two the server adds:
`snapshot` (one-shot on attach: latest status + recent transcript) and `tick` (optional heartbeat).
Keeping the wire `kind`s identical to the in-process `Bridge` means a client renders the same local
or remote (the remote TUI reuses its bridge handler with zero translation — KILN-054).

The agent reply is `kind:"agent"` (the roadmap's *agnika.message*) and carries the v0.10/v0.11 flags
`is_self` / `lead` / `model` / `is_thought` / `is_curiosity`.

**client → server** messages are typed: `attach` (handshake; optional `history` limit),
`user.message` (a chat line), `command` (a slash command line). Both message kinds just carry `text`
that goes onto the agent's inbox — the engine's `handle_command` intercepts lines starting with `/`.

The wire encoding is JSON (`ensure_ascii=False`, so Ukrainian stays readable).
"""

from __future__ import annotations

import json

# server → client (agent render events + the two server-only events)
SERVER_EVENT_KINDS = ("agent", "usage", "notice", "status", "snapshot", "tick")
# client → server message types
CLIENT_MESSAGE_TYPES = ("attach", "user.message", "command")


def encode(event: dict) -> str:
    """Serialise a server→client event to a JSON line."""
    return json.dumps(event, ensure_ascii=False)


def decode(raw: str) -> dict:
    """Parse a JSON line back into an event dict."""
    return json.loads(raw)


def snapshot_event(status: dict | None, history: list[dict]) -> dict:
    """The one-shot a client gets on attach: the latest status + recent persisted transcript."""
    return {"kind": "snapshot", "status": status, "history": list(history)}


def tick_event(n: int) -> dict:
    """An optional heartbeat (defined for completeness; v1.1 streams `status` per tick instead)."""
    return {"kind": "tick", "n": n}


def parse_client(data: dict) -> tuple[str, str]:
    """Validate a client→server message; return `(type, text)`. `attach` carries no text ("").
    Raises `ValueError` on an unknown type or a non-string `text`."""
    mtype = data.get("type")
    if mtype not in CLIENT_MESSAGE_TYPES:
        raise ValueError(f"unknown client message type: {mtype!r}")
    text = data.get("text", "")
    if mtype in ("user.message", "command"):
        if not isinstance(text, str):
            raise ValueError("`text` must be a string")
    return mtype, text


def build_client(mtype: str, text: str = "") -> str:
    """Serialise a client→server message (inverse of `parse_client`) — used by remote clients."""
    if mtype not in CLIENT_MESSAGE_TYPES:
        raise ValueError(f"unknown client message type: {mtype!r}")
    return json.dumps({"type": mtype, "text": text}, ensure_ascii=False)
