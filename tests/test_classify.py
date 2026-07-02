"""Unit: turn classification — turn_weight (state weight) and classify (branch + agent)."""

from __future__ import annotations

import pytest

from kiln.engine import State, classify, turn_weight


def test_turn_weight_formula():
    st = State(needs={"intensity": 1.0, "connection": 0.0})
    assert turn_weight(st) == pytest.approx(0.55)
    st = State(needs={"intensity": 0.0, "connection": 1.0})
    assert turn_weight(st) == pytest.approx(0.45)


def test_turn_weight_clamped_0_1():
    assert turn_weight(State(needs={})) == 0.0
    assert turn_weight(State(needs={"intensity": 1.0, "connection": 1.0})) == 1.0


# --- classify -> (class, agent) --------------------------------------------


def test_classify_plain_is_chat():
    assert classify("привіт, як справи?", State(needs={})) == ("chat", None)


def test_classify_think_hint_routes_think():
    assert classify("поясни, чому так виходить", State(needs={})) == ("think", None)


def test_classify_tool_hint_routes_tools():
    assert classify("знайди файл і прочитай", State(needs={})) == ("tools", None)


def test_classify_tool_hint_beats_think_hint():
    # TOOL_HINTS are checked first -> tools, even with a reasoning marker present.
    assert classify("поясни і знайди файл", State(needs={})) == ("tools", None)


# ambient high needs pick the deeper brain for a plain user turn (same map as a self-trigger)


def test_classify_high_intensity_routes_deep():
    # v1.4: intensity fires the `deep` sub-agent (action: tool, agent: deep)
    assert classify("привіт", State(needs={"intensity": 0.80})) == ("tool", "deep")


def test_classify_high_novelty_routes_session_wiki():
    assert classify("привіт", State(needs={"novelty": 0.90})) == ("tool", "session-wiki")


def test_classify_intensity_wins_over_novelty():
    st = State(needs={"intensity": 0.80, "novelty": 0.95})
    assert classify("привіт", st) == ("tool", "deep")


def test_classify_explicit_hint_beats_high_need():
    # an explicit "explain" request is honored over ambient high novelty
    assert classify("поясни це", State(needs={"novelty": 0.95})) == ("think", None)


def test_classify_state_weight_routes_think_without_hints(monkeypatch):
    import kiln.routing as routing  # classify lives here now; patch the threshold where it's read

    monkeypatch.setattr(routing, "THINK_THRESHOLD", 0.40)
    st = State(needs={"intensity": 0.5, "connection": 0.7})  # no high need; weight 0.59 >= 0.40
    assert turn_weight(st) >= 0.40
    assert classify("привіт", st) == ("think", None)


def test_classify_low_state_weight_stays_chat():
    cool = State(needs={"intensity": 0.0, "connection": 0.0})  # weight 0 < any threshold
    assert classify("привіт", cool) == ("chat", None)
