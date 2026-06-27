"""
Contract + integration: respond() — a full turn against MockBrain (zero paid calls).

Pins the return shape {class, route, reply, usage} and the end-to-end turn effect:
input -> classification -> branch -> reply -> satiation + history growth.
"""

from __future__ import annotations

import pytest

from kiln.brain import MockBrain
from kiln.config import SATIATION
from kiln.engine import State, respond

USAGE_KEYS = {"model", "input", "output", "total"}


def test_respond_return_contract():
    history: list[dict] = []
    out = respond("привіт", State(needs={"connection": 0.8}), history, "sys", MockBrain())
    assert set(out) == {"class", "route", "reply", "usage"}
    assert out["class"] == "chat"
    assert out["route"].startswith("CHAT/")
    assert isinstance(out["reply"], str) and out["reply"]
    assert set(out["usage"]) == USAGE_KEYS


def test_respond_appends_user_and_bot_to_history():
    history: list[dict] = []
    respond("привіт", State(needs={}), history, "sys", MockBrain())
    assert [h["role"] for h in history] == ["user", "assistant"]
    assert history[0]["text"] == "привіт"


def test_respond_chat_applies_chat_satiation():
    st = State(needs={"connection": 0.80})
    respond("привіт", st, [], "sys", MockBrain())  # chat event
    assert st.needs["connection"] == pytest.approx(0.80 + SATIATION["chat"]["connection"])


def test_respond_think_routes_deep_and_closes_novelty():
    st = State(needs={"novelty": 0.90, "connection": 0.0})
    out = respond("поясни, чому так", st, [], "sys", MockBrain())  # think -> deep event
    assert out["route"].startswith("THINK/")
    assert st.needs["novelty"] == pytest.approx(0.90 + SATIATION["deep"]["novelty"])


def test_respond_tools_route_label():
    out = respond("знайди файл", State(needs={}), [], "sys", MockBrain())
    assert out["class"] == "tools"
    assert out["route"].startswith("TOOLS/")


def test_respond_tool_uses_per_agent_satiation():
    # session-wiki has its OWN satiation entry (drops novelty), not the deep fallback.
    st = State(needs={"novelty": 0.90})
    out = respond(
        "розкажи щось нове", st, [], "sys", MockBrain(), force="tool", agent="session-wiki"
    )
    assert out["class"] == "tool"
    assert out["route"] == "TOOL/session-wiki"
    assert st.needs["novelty"] == pytest.approx(0.90 + SATIATION["session-wiki"]["novelty"])


def test_respond_tool_unknown_agent_falls_back_to_deep():
    # an agent with no SATIATION entry falls back to the deep "filling meal"
    st = State(needs={"novelty": 0.90})
    respond("x", st, [], "sys", MockBrain(), force="tool", agent="no-such-agent")
    assert st.needs["novelty"] == pytest.approx(0.90 + SATIATION["deep"]["novelty"])


def test_respond_full_turn_is_offline():
    """An end-to-end turn on the mock makes no network/subprocess calls."""
    import kiln.brain as brainmod

    calls = {"n": 0}
    orig = brainmod.subprocess.run

    def _count(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    brainmod.subprocess.run = _count
    try:
        respond("поясни, чому так", State(needs={}), [], "sys", MockBrain())
    finally:
        brainmod.subprocess.run = orig
    assert calls["n"] == 0
