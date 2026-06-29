"""
kiln tick-server — the FastAPI app (KILN-049 scaffold; KILN-052 agent host).

`GET /health`, the agent host wired into the app lifespan (the home agent **agnika** ticks
server-side, no client), and a WebSocket **stub** (`/ws`). The real `WS /agent/{agent_id}` (attach →
snapshot → stream) and the HTTP reads land in KILN-053. The `[server]` extra is required.

Run a live server: `KILN_SERVE=1 uvicorn server.app:app`. The `KILN_SERVE` guard keeps a plain
import (tests / `TestClient`) from ever spinning a LIVE agent — so no paid calls leak into tests.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

try:
    from fastapi import FastAPI, HTTPException, WebSocket
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "the kiln tick-server needs the [server] extra: pip install -e '.[server]'"
    ) from exc

from .host import AgentHost
from .ws import serve_agent

# The server's live-agent registry. The WS endpoints attach clients to it (KILN-053).
host = AgentHost()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Boot the home agent (agnika) so it ticks server-side with no client. Guarded by KILN_SERVE so
    # importing the app (tests / TestClient) never starts a LIVE agent (tests stay paid-call-free).
    if os.environ.get("KILN_SERVE"):
        host.boot_default(live=True)
    try:
        yield
    finally:
        host.stop_all()


app = FastAPI(title="kiln", summary="kiln tick-server (v1.1)", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    """Liveness probe — also lets a client confirm the server is up before attaching."""
    return {"ok": True}


@app.get("/agents")
def list_agents() -> list[dict]:
    """The live agents and each one's latest status snapshot."""
    return [{"agent_id": aid, "status": host.get(aid).latest_status()} for aid in host.agents()]


@app.get("/agent/{agent_id}/history")
def agent_history(agent_id: str, limit: int = 20) -> dict:
    """The last `limit` persisted turns for an agent (404 if it isn't hosted)."""
    runtime = host.get(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"no such agent: {agent_id}")
    return {"agent_id": agent_id, "turns": runtime.recent_history(limit)}


@app.websocket("/agent/{agent_id}")
async def agent_ws(ws: WebSocket, agent_id: str) -> None:
    """Attach a client to an agent: snapshot, then stream its events; relay input to its inbox.
    Disconnecting leaves the agent ticking (KILN-053)."""
    runtime = host.get(agent_id)
    if runtime is None:
        await ws.close(code=4404)  # unknown agent (private-use close code)
        return
    await serve_agent(ws, runtime)


@app.websocket("/ws")
async def ws_stub(ws: WebSocket) -> None:
    """KILN-049 health WS: accept, send one `notice`, close. (Real streaming is `/agent/{id}`.)"""
    await ws.accept()
    await ws.send_json({"kind": "notice", "text": "kiln tick-server — connected"})
    await ws.close()
