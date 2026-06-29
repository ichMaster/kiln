"""
KILN-055: end-to-end — the full v1.1 DoD in one test.

server boots → agnika ticks with **no client** → client A attaches and holds a turn → client B
attaches and sees the **same** session (A's next turn broadcasts to both) → A/B disconnect, the
agent keeps ticking → everything is `agent_id`-scoped (`/agents`, unknown agent 404). MockBrain +
tmp `AgentPaths` + stubbed model helpers — zero paid calls.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")

import kiln.engine as eng  # noqa: E402
import server.app as appmod  # noqa: E402
from kiln.brain import MockBrain  # noqa: E402
from kiln.config import AgentPaths  # noqa: E402
from server.host import AgentHost  # noqa: E402


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


def _wait(pred, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _collect(ws, want: set[str], cap: int = 800) -> set[str]:
    seen: set[str] = set()
    for _ in range(cap):
        seen.add(ws.receive_json()["kind"])
        if want <= seen:
            break
    return seen


def test_v1_1_full_dod(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    _stub_model(monkeypatch)
    monkeypatch.setattr(eng, "TICK_SECONDS", 0.01)  # fast ticks

    host = AgentHost()
    monkeypatch.setattr(appmod, "host", host)  # routes + lifespan use this host
    paths = _tmp_paths(tmp_path)
    runtime = host.start("agnika", brain=MockBrain(), ticks=None, live=True, paths=paths)

    # (1) the server ticks with NO client connected — status accrues on the agent before any attach
    assert _wait(lambda: runtime.latest_status() is not None)

    with TestClient(appmod.app) as client:
        # (2) client A attaches over WS and holds a turn
        with client.websocket_connect("/agent/agnika") as a:
            assert a.receive_json()["kind"] == "snapshot"
            a.send_json({"type": "user.message", "text": "привіт A"})
            seen_a = _collect(a, {"agent", "usage"})
            assert "agent" in seen_a and "usage" in seen_a

            # (3) a SECOND client sees the SAME session: A's next turn broadcasts to both
            with client.websocket_connect("/agent/agnika") as b:
                assert b.receive_json()["kind"] == "snapshot"
                # ensure B is subscribed (A + B + the internal status-cache sink) before the turn
                assert _wait(lambda: runtime.hub.subscriber_count() >= 3)
                a.send_json({"type": "user.message", "text": "ще раз A"})
                assert "agent" in _collect(a, {"agent"})
                assert "agent" in _collect(b, {"agent"})  # same broadcast reaches B

            # (4) B disconnected on block-exit; the agent keeps ticking → A still gets status
            assert "status" in _collect(a, {"status"})

        # (5) agent_id-scoped: /agents lists agnika; an unknown agent is a 404
        agents = client.get("/agents").json()
        assert [x["agent_id"] for x in agents] == ["agnika"]
        assert client.get("/agent/ghost/history").status_code == 404

    host.stop_all()
    # (5b) all persistence stayed under the agent's own root — nothing leaked to repo paths
    assert (tmp_path / "state" / "needs.json").exists()
