"""
KILN-053: WS event protocol + connection lifecycle + HTTP reads.

- Contract: the (de)serialiser round-trips every event kind; the `agent` reply carries the
  v0.10/v0.11 flags; `parse_client` validates client→server messages.
- Integration (`TestClient`, MockBrain): attach → `snapshot`; `user.message` → `agent` + `usage`;
  a second client gets the same broadcast; disconnecting one leaves the agent ticking.
- Unit: `GET /agents` lists agnika; `GET /agent/agnika/history?limit=N` returns the last N turns.

No paid calls — MockBrain + stubbed model helpers + a tmp `AgentPaths`.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

import kiln.engine as eng  # noqa: E402
import server.app as appmod  # noqa: E402
from kiln import store as kstore  # noqa: E402
from kiln.brain import MockBrain  # noqa: E402
from kiln.config import AgentPaths  # noqa: E402
from server.host import AgentHost  # noqa: E402
from server.protocol import (  # noqa: E402
    decode,
    encode,
    parse_client,
    snapshot_event,
    tick_event,
)


def _tmp_paths(tmp_path) -> AgentPaths:
    s = tmp_path / "state"
    k = tmp_path / "kiln"
    return AgentPaths(
        state_dir=s,
        store_file=k / "store.json",
        usage_ledger=k / "usage-ledger.jsonl",
        usage_report=k / "usage-report.md",
        canon_file=s / "canon.md",
        prompts_file=s / "prompts.md",
    )


def _stub_model(monkeypatch) -> None:
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "")
    monkeypatch.setattr(eng, "extract_facts", lambda *a, **k: [])
    monkeypatch.setattr(eng, "digest_facts", lambda *a, **k: "")


def _collect(ws, want: set[str], cap: int = 600):
    """Receive events until every kind in `want` is seen (or `cap`). Returns {kind: first event}."""
    seen: dict[str, dict] = {}
    for _ in range(cap):
        event = ws.receive_json()
        seen.setdefault(event["kind"], event)
        if want <= set(seen):
            break
    return seen


# --- contract: the wire protocol --------------------------------------------


def test_protocol_round_trips_every_event_kind():
    events = [
        {
            "kind": "agent",
            "text": "привіт",
            "is_self": True,
            "lead": False,
            "model": "haiku",
            "is_thought": False,
            "is_curiosity": True,
        },
        {"kind": "usage", "usage": {"model": "m", "total": 3}, "latency": 0.5},
        {"kind": "notice", "text": "[exit] saved"},
        {"kind": "status", "snapshot": {"status": "idle"}},
        snapshot_event({"status": "idle"}, [{"role": "user", "text": "hi"}]),
        tick_event(7),
    ]
    for event in events:
        assert decode(encode(event)) == event  # round-trips, Ukrainian intact

    agent = events[0]
    for flag in ("is_self", "lead", "model", "is_thought", "is_curiosity"):
        assert flag in agent  # the agnika.message carries the v0.10/v0.11 reply flags


def test_parse_client_validates_messages():
    assert parse_client({"type": "user.message", "text": "привіт"}) == ("user.message", "привіт")
    assert parse_client({"type": "command", "text": "/status"}) == ("command", "/status")
    assert parse_client({"type": "attach", "history": 10})[0] == "attach"
    with pytest.raises(ValueError):
        parse_client({"type": "nope"})
    with pytest.raises(ValueError):
        parse_client({"type": "user.message", "text": 5})


# --- integration: the WS lifecycle ------------------------------------------


def test_ws_attach_message_second_client_and_disconnect(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    _stub_model(monkeypatch)
    monkeypatch.setattr(eng, "TICK_SECONDS", 0.01)  # fast ticks → no waiting on real time
    host = AgentHost()
    monkeypatch.setattr(appmod, "host", host)  # routes + lifespan use this host
    host.start("agnika", brain=MockBrain(), ticks=None, live=True, paths=_tmp_paths(tmp_path))

    with TestClient(appmod.app) as client:
        with client.websocket_connect("/agent/agnika") as ws1:
            snap = ws1.receive_json()
            assert snap["kind"] == "snapshot"
            assert "status" in snap and "history" in snap

            ws1.send_json({"type": "user.message", "text": "привіт"})
            seen = _collect(ws1, {"agent", "usage"})
            assert "agent" in seen and "usage" in seen
            # the reply event carries the flags pinned in the protocol contract
            for flag in ("is_self", "model", "is_thought", "is_curiosity"):
                assert flag in seen["agent"]

            # a SECOND client on the same agent gets its own snapshot + the live broadcast
            with client.websocket_connect("/agent/agnika") as ws2:
                assert ws2.receive_json()["kind"] == "snapshot"
                ws2.send_json({"type": "user.message", "text": "ще раз"})
                assert "agent" in _collect(ws2, {"agent"})

            # ws2 disconnected on block-exit; the agent keeps ticking → ws1 still sees status
            assert "status" in _collect(ws1, {"status"})


def test_ws_rejects_unknown_agent(monkeypatch):
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    host = AgentHost()
    monkeypatch.setattr(appmod, "host", host)
    with TestClient(appmod.app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/agent/nobody") as ws:
                ws.receive_json()


# --- unit: the HTTP reads ---------------------------------------------------


def test_http_agents_and_history(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    _stub_model(monkeypatch)
    paths = _tmp_paths(tmp_path)
    # pre-populate the agent's store with three persisted turns
    store = kstore.empty_store()
    store["sessions"].append(
        {
            "id": "s1",
            "started_at": "2026-01-01T00:00:00",
            "ended_at": "2026-01-01T00:01:00",
            "mode": "dry",
            "turns": 3,
        }
    )
    store["messages"]["s1"] = [
        {"role": "user", "text": "а"},
        {"role": "bot", "text": "б"},
        {"role": "user", "text": "в"},
    ]
    paths.store_file.parent.mkdir(parents=True, exist_ok=True)
    kstore.save_store(store, paths.store_file)

    host = AgentHost()
    monkeypatch.setattr(appmod, "host", host)
    host.start("agnika", brain=MockBrain(), ticks=1, live=False, paths=paths)

    with TestClient(appmod.app) as client:
        agents = client.get("/agents").json()
        assert any(a["agent_id"] == "agnika" for a in agents)

        hist = client.get("/agent/agnika/history?limit=2").json()
        assert hist["agent_id"] == "agnika"
        assert len(hist["turns"]) == 2  # the last two
        assert hist["turns"][-1]["text"] == "в"

        assert client.get("/agent/nobody/history").status_code == 404
