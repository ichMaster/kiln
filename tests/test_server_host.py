"""
KILN-052: AgentRuntime + AgentHost — one living agent on a dedicated thread.

Integration: the host starts the home agent; with no client attached, `status` events accumulate on
its hub over a bounded `ticks=N` run (MockBrain — deterministic, no paid calls). Unit: a brain call
blocking on the agent thread leaves `GET /health` responsive (the async layer isn't stalled).

Each runtime gets a tmp `AgentPaths`, so nothing touches real agent data. The model-calling helpers
are stubbed — zero paid calls.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("fastapi")

import kiln.engine as eng  # noqa: E402
from kiln.brain import MockBrain  # noqa: E402
from kiln.config import AgentPaths  # noqa: E402
from server.host import AgentHost  # noqa: E402
from server.runtime import AgentRuntime  # noqa: E402


def _tmp_paths(tmp_path) -> AgentPaths:
    s = tmp_path / "state"
    k = tmp_path / "kiln"
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
    """No `claude -p` / SDK — keep the engine deterministic and free."""
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "")
    monkeypatch.setattr(eng, "extract_facts", lambda *a, **k: [])
    monkeypatch.setattr(eng, "digest_facts", lambda *a, **k: "")


def test_agent_ticks_with_no_client_and_emits_status(monkeypatch, tmp_path):
    _stub_model(monkeypatch)
    rt = AgentRuntime("agnika", brain=MockBrain(), ticks=3, live=False, paths=_tmp_paths(tmp_path))
    events: list[dict] = []
    rt.subscribe(events.append)  # subscribe BEFORE start → catch every tick
    rt.start()
    rt.join(timeout=5)

    statuses = [e for e in events if e["kind"] == "status"]
    assert len(statuses) == 3  # one status per idle tick, no client attached
    assert rt.latest_status() is not None  # cached for a just-attached client
    assert not rt.is_alive()


def test_host_registers_one_agent_and_is_idempotent(monkeypatch, tmp_path):
    _stub_model(monkeypatch)
    host = AgentHost()
    rt = host.start("agnika", brain=MockBrain(), ticks=1, live=False, paths=_tmp_paths(tmp_path))

    assert host.agents() == ["agnika"]
    assert host.get("agnika") is rt
    assert host.start("agnika", brain=MockBrain()) is rt  # idempotent — same runtime, not a 2nd

    host.stop_all()
    assert host.agents() == []


class _BlockingBrain:
    """A brain whose reply blocks on the agent thread until released (simulating a slow `deep`)."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def chat(self, history, system):
        self.entered.set()
        self.release.wait(timeout=5)
        return ("(unblocked)", None)

    def deep(self, prompt, history, system, with_tools):
        self.entered.set()
        self.release.wait(timeout=5)
        return ("(unblocked)", None)

    def tool(self, agent, history, system):
        return ("(tool)", None)


def test_blocking_brain_on_agent_thread_does_not_stall_health(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from server.app import app

    _stub_model(monkeypatch)
    brain = _BlockingBrain()
    rt = AgentRuntime("agnika", brain=brain, ticks=None, live=False, paths=_tmp_paths(tmp_path))
    rt.submit("привіт")  # queued before start → picked up on the first tick → blocks in chat()
    rt.start()
    try:
        assert brain.entered.wait(timeout=5)  # the agent thread is now blocked inside the brain
        with TestClient(app) as client:  # the async server is a different thread → still responsive
            assert client.get("/health").status_code == 200
            assert client.get("/health").json() == {"ok": True}
    finally:
        brain.release.set()  # let the agent thread finish its turn
        rt.stop()
    assert not rt.is_alive()
