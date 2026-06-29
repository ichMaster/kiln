"""
kiln tick-server — the FastAPI app (KILN-049 scaffold).

`GET /health` and a WebSocket **stub** (`/ws`: accept → one `notice` → close). The real per-agent
host, the network bus, and the event protocol land in later v1.1 issues (KILN-050/052/053). The
`[server]` extra is required; importing without it fails with a clear message.

Run: `uvicorn server.app:app` (after `pip install -e '.[server]'`).
"""

from __future__ import annotations

try:
    from fastapi import FastAPI, WebSocket
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "the kiln tick-server needs the [server] extra: pip install -e '.[server]'"
    ) from exc

app = FastAPI(title="kiln", summary="kiln tick-server (v1.1)")


@app.get("/health")
def health() -> dict:
    """Liveness probe — also lets a client confirm the server is up before attaching."""
    return {"ok": True}


@app.websocket("/ws")
async def ws_stub(ws: WebSocket) -> None:
    """KILN-049 stub: accept the connection, send one `notice`, then close. The real
    `WS /agent/{agent_id}` (attach → snapshot → stream the agent's events) is KILN-053."""
    await ws.accept()
    await ws.send_json({"kind": "notice", "text": "kiln tick-server (scaffold) — connected"})
    await ws.close()
