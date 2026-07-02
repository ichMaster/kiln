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
    # Boot every configured agent (server.yaml `agents:` → SERVER_AGENTS; v1.2 = agnika + pashu) so
    # each ticks server-side with no client, on its own thread + isolated state. Guarded by
    # KILN_SERVE so importing the app (tests/TestClient) never starts a LIVE agent (no paid calls).
    if os.environ.get("KILN_SERVE"):
        host.boot_configured(live=True)
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
    """The live agents, each one's permission scope (v1.2; set, not enforced), the v1.4
    deep-branch security profile, and latest status."""
    return [
        {
            "agent_id": aid,
            "scope": host.get(aid).scope,
            "security": host.get(aid).security_summary(),
            "status": host.get(aid).latest_status(),
        }
        for aid in host.agents()
    ]


@app.get("/agent/{agent_id}/history")
def agent_history(agent_id: str, limit: int = 20) -> dict:
    """The last `limit` persisted turns for an agent (404 if it isn't hosted)."""
    runtime = host.get(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"no such agent: {agent_id}")
    return {"agent_id": agent_id, "turns": runtime.recent_history(limit)}


@app.post("/agent/{agent_id}/reload")
def agent_reload(agent_id: str) -> dict:
    """Queue a `/reload` for an agent — re-read its canon/prompts/memory without dropping the
    session (the agent applies it on its next tick). 404 if it isn't hosted."""
    runtime = host.get(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"no such agent: {agent_id}")
    runtime.submit("/reload")
    return {"agent_id": agent_id, "reload": "queued"}


@app.post("/agent/{agent_id}/rotate")
def agent_rotate(agent_id: str) -> dict:
    """Queue a `/rotate` for an agent — close+summarize the current session and start a fresh one,
    non-blocking (the summary runs off the agent thread). 404 if it isn't hosted."""
    runtime = host.get(agent_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"no such agent: {agent_id}")
    runtime.submit("/rotate")
    return {"agent_id": agent_id, "rotate": "queued"}


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
