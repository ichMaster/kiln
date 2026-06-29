"""
kiln.server — the tick-server (v1.1): the engine becomes a WS/HTTP service; clients attach.

The engine never depends on this package — it stays a thin host + transport over the v0 seams
(`Channel`/`Output`/`Bridge`). Requires the `[server]` extra (`pip install -e '.[server]'`).

Built across v1.1:
  - app.py      — the FastAPI app: `GET /health` + the WS endpoint (KILN-049 scaffold).
  - bus.py      — ServerChannel / ServerOutput / BroadcastHub, the network `Bridge` (KILN-050).
  - host.py     — AgentRuntime + AgentHost, one engine.run() per agent_id on a thread (KILN-052).
  - protocol.py — the WS event (de)serialisers (KILN-053).
  - ws.py       — the WS endpoint + connection lifecycle (KILN-053).
"""
