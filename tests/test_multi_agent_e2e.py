"""
KILN-060: end-to-end — the full v1.2 DoD in one test (multi-agent isolation).

host boots agnika + pashu concurrently → both tick with no client → a TUI (WS) attaches to **pashu**
and holds a turn → a turn on one agent **never** touches the other's store/needs/config → `/agents`
lists both with their permission scopes (agnika broad, pashu narrow) → isolated persistence.
MockBrain + tmp `AgentPaths` + stubbed model helpers — zero paid calls.
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


def _root(root) -> AgentPaths:
    s, k = root / "state", root / "kiln"
    return AgentPaths(
        state_dir=s,
        needs_file=k / "needs.json",
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


def _has(turns, text) -> bool:
    return any(text in t.get("text", "") for t in turns)


def test_v1_2_multi_agent_isolation_dod(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    _stub_model(monkeypatch)
    monkeypatch.setattr(eng, "TICK_SECONDS", 0.01)  # fast ticks (agnika runs config=None → globals)

    host = AgentHost()
    monkeypatch.setattr(appmod, "host", host)  # routes + lifespan use this host
    a = host.start("agnika", brain=MockBrain(), ticks=None, live=True, paths=_root(tmp_path / "a"))
    p = host.start("pashu", brain=MockBrain(), ticks=None, live=True, paths=_root(tmp_path / "p"))

    # (1) both agents tick with NO client; each carries its own calibration (config) + scope
    assert _wait(lambda: a.latest_status() is not None and p.latest_status() is not None)
    assert a._config is None and p._config is not None and p._config.agent_id == "pashu"
    assert a.scope == "broad" and p.scope == "narrow"

    with TestClient(appmod.app) as client:
        # (2) a TUI (WS) attaches to PASHU — the second agent — and holds a turn
        with client.websocket_connect("/agent/pashu") as ws:
            assert ws.receive_json()["kind"] == "snapshot"
            ws.send_json({"type": "user.message", "text": "ПАШУ-ТЕСТ"})
            seen = _collect(ws, {"agent", "usage"})
            assert "agent" in seen and "usage" in seen
        # (3) drive AGNIKA with a different message
        with client.websocket_connect("/agent/agnika") as ws:
            assert ws.receive_json()["kind"] == "snapshot"
            ws.send_json({"type": "user.message", "text": "АГНІКА-ТЕСТ"})
            assert "agent" in _collect(ws, {"agent"})

        # (4) isolation: each turn lands ONLY in its own agent's store — neither leaks to the other
        assert _wait(lambda: _has(p.recent_history(50), "ПАШУ-ТЕСТ"))
        assert _wait(lambda: _has(a.recent_history(50), "АГНІКА-ТЕСТ"))
        pashu_hist, agnika_hist = p.recent_history(50), a.recent_history(50)
        assert not _has(agnika_hist, "ПАШУ-ТЕСТ")  # pashu's turn never touched agnika's store
        assert not _has(pashu_hist, "АГНІКА-ТЕСТ")  # and vice-versa

        # (5) agent_id-scoped: /agents lists both with their scopes; an unknown agent is a 404
        agents = {x["agent_id"]: x for x in client.get("/agents").json()}
        assert set(agents) == {"agnika", "pashu"}
        assert agents["agnika"]["scope"] == "broad" and agents["pashu"]["scope"] == "narrow"
        assert client.get("/agent/ghost/history").status_code == 404

    host.stop_all()
    # (5b) isolated persistence: each agent wrote ONLY under its own root
    assert (tmp_path / "a" / "kiln" / "needs.json").exists()
    assert (tmp_path / "p" / "kiln" / "needs.json").exists()
