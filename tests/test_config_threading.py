"""
KILN-057: `AgentConfig` threaded through `engine.run` — per-agent calibration drives the loop.

Contract: two configs that differ in drift / need_triggers / think_threshold diverge (need
trajectory + routing + status), and the default (`config=None`) path reads the module globals
unchanged (the whole v0/v1.1 suite — which calls `run`/`drift`/`classify`/`respond` without a config
— is the byte-for-byte back-compat proof). Brain pick follows `config.chat_model`/`deep_model`.
MockBrain — zero paid calls.
"""

from __future__ import annotations

import dataclasses

import pytest

import kiln.engine as eng
from kiln.brain import MockBrain
from kiln.config import AgentConfig
from kiln.engine import State, classify, drift, respond
from kiln.store import empty_store


def test_drift_uses_per_agent_need_model():
    base = AgentConfig.for_agent()
    fast = dataclasses.replace(base, drift={**base.drift, "connection": 0.05})
    slow = dataclasses.replace(base, drift={**base.drift, "connection": 0.0})
    s_fast, s_slow = State(needs={"connection": 0.0}), State(needs={"connection": 0.0})
    for _ in range(5):
        drift(s_fast, 1, fast)
        drift(s_slow, 1, slow)
    assert s_fast.needs["connection"] == pytest.approx(0.25)  # 5 × 0.05
    assert s_slow.needs["connection"] == 0.0  # frozen
    # default path (config=None) still reads the module global, not either agent's
    s_glob = State(needs={"connection": 0.0})
    drift(s_glob, 1)
    assert s_glob.needs["connection"] == eng.DRIFT["connection"]


def test_classify_uses_per_agent_think_threshold():
    state = State(needs={"intensity": 0.5, "connection": 0.5})  # turn_weight == 0.5
    base = AgentConfig.for_agent()
    low = dataclasses.replace(base, think_threshold=0.4, reach_out_models=())
    high = dataclasses.replace(base, think_threshold=0.9, reach_out_models=())
    assert classify("привіт", state, low)[0] == "think"  # 0.5 >= 0.4
    assert classify("привіт", state, high)[0] == "chat"  # 0.5 < 0.9


def test_respond_brain_pick_follows_config_models():
    base = AgentConfig.for_agent()
    cfg = dataclasses.replace(base, chat_model="x-FOO-1", deep_model="y-BAR-2")
    state = State(needs={})
    chat = respond("привіт", state, [], "sys", MockBrain(), force="chat", config=cfg)
    deep = respond("питання", state, [], "sys", MockBrain(), force="deep", config=cfg)
    assert chat["route"] == "CHAT/FOO"  # from config.chat_model, not the module CHAT_MODEL
    assert deep["route"] == "THINK/BAR"  # from config.deep_model


def test_run_threads_config_into_drift_and_status(monkeypatch):
    """An integration run with a per-agent config: drift and the status thresholds follow it."""
    monkeypatch.setattr(eng, "load_state", lambda *a, **k: State(needs={"connection": 0.0}))
    for name in ("save_state", "save_store", "append_session", "write_report"):
        monkeypatch.setattr(eng, name, lambda *a, **k: None)
    monkeypatch.setattr(eng, "load_store", lambda *a, **k: empty_store())
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "")
    monkeypatch.setattr(eng, "extract_facts", lambda *a, **k: [])
    monkeypatch.setattr(eng, "digest_facts", lambda *a, **k: "")

    base = AgentConfig.for_agent()
    cfg = dataclasses.replace(
        base,
        drift={**base.drift, "connection": 0.05},
        need_triggers={"connection": {"threshold": 0.99, "action": "chat"}},  # 0.99 → no fire in 3
    )
    snaps: list[dict] = []

    class Rec:
        def user(self, t): ...
        def agent(self, t, **k): ...
        def usage(self, u, latency=None): ...
        def notice(self, t): ...
        def status(self, s):
            snaps.append(s)

    eng.run(
        ticks=3,
        live=False,
        channel=eng.ScriptedChannel({}),
        brain=MockBrain(),
        output=Rec(),
        config=cfg,
    )
    assert snaps[-1]["thresholds"]["connection"] == 0.99  # config's triggers, not the global
    assert snaps[-1]["needs"]["connection"] == pytest.approx(0.15)  # 3 idle ticks × 0.05 drift


def test_status_snapshot_carries_per_agent_name():
    """The snapshot carries agent_name so a remote TUI labels the right agent (None → global)."""
    import kiln.config as config
    from kiln.stats import SessionStats

    state, tg, stats = State(needs={}), eng.TriggerBook(), SessionStats()
    pashu = dataclasses.replace(AgentConfig.for_agent(), agent_name="Пашу")
    assert (
        eng._status_snapshot("idle", state, tg, stats, None, 0)["agent_name"] == config.AGENT_NAME
    )
    assert eng._status_snapshot("idle", state, tg, stats, None, 0, pashu)["agent_name"] == "Пашу"
