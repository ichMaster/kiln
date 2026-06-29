"""
KILN-049: tick-server scaffold — `GET /health` + a WS stub.

Skipped unless the `[server]` extra is installed (mirrors the TUI `importorskip`), so a base
install's test suite stays green. Uses Starlette's `TestClient` — no real network, no paid calls.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from starlette.testclient import TestClient  # noqa: E402

from server.app import app  # noqa: E402


def test_health_returns_ok():
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


def test_ws_stub_sends_a_notice():
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        event = ws.receive_json()
        assert event["kind"] == "notice"
        assert "kiln" in event["text"].lower()
