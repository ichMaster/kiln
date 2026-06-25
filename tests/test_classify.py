"""Unit: turn classification — turn_weight (state weight) and classify (branch)."""

from __future__ import annotations

import pytest

from kiln.config import THINK_THRESHOLD
from kiln.engine import State, classify, turn_weight


def test_turn_weight_formula():
    st = State(needs={"intensity": 1.0, "connection": 0.0})
    assert turn_weight(st) == pytest.approx(0.55)
    st = State(needs={"intensity": 0.0, "connection": 1.0})
    assert turn_weight(st) == pytest.approx(0.45)


def test_turn_weight_clamped_0_1():
    assert turn_weight(State(needs={})) == 0.0
    assert turn_weight(State(needs={"intensity": 1.0, "connection": 1.0})) == 1.0


def test_classify_plain_is_chat():
    assert classify("привіт, як справи?", State(needs={})) == "chat"


def test_classify_think_hint_routes_think():
    assert classify("поясни, чому так виходить", State(needs={})) == "think"


def test_classify_tool_hint_routes_tools():
    assert classify("знайди файл і прочитай", State(needs={})) == "tools"


def test_classify_tool_hint_beats_think_hint():
    # TOOL_HINTS are checked first -> tools, even if a reasoning marker is present.
    assert classify("поясни і знайди файл", State(needs={})) == "tools"


def test_classify_high_state_weight_routes_think_without_hints():
    # A high state weight on its own pushes a plain message into reasoning.
    hot = State(needs={"intensity": 1.0, "connection": 1.0})  # weight 1.0 >= threshold
    assert turn_weight(hot) >= THINK_THRESHOLD
    assert classify("привіт", hot) == "think"


def test_classify_low_state_weight_stays_chat():
    cool = State(needs={"intensity": 0.0, "connection": 0.0})
    assert turn_weight(cool) < THINK_THRESHOLD
    assert classify("привіт", cool) == "chat"
