"""Unit: needs model — drift (growth + catch-up + clamp) and satiation (closing)."""

from __future__ import annotations

import pytest

from kiln.config import DRIFT, NEED_TRIGGERS, SATIATION
from kiln.engine import State, apply_satiation, drift


def test_drift_raises_each_need_by_its_rate():
    st = State(needs={"connection": 0.0, "intensity": 0.0})
    drift(st)
    assert st.needs["connection"] == pytest.approx(DRIFT["connection"])
    assert st.needs["intensity"] == pytest.approx(DRIFT["intensity"])


def test_drift_catch_up_multiplies_by_ticks():
    """ticks>1 — drift catch-up for real elapsed time (×ticks)."""
    st = State(needs={"novelty": 0.0})
    drift(st, ticks=3)
    assert st.needs["novelty"] == pytest.approx(DRIFT["novelty"] * 3)


def test_drift_clamps_at_one():
    st = State(needs={"intensity": 0.99})
    drift(st, ticks=1000)  # plenty to exceed 1.0 for any positive drift -> clamps
    assert st.needs["intensity"] == 1.0


def test_drift_ignores_needs_without_a_rate():
    st = State(needs={"foo": 0.5})  # 'foo' is not in DRIFT -> no growth
    drift(st, ticks=4)
    assert st.needs["foo"] == 0.5


def test_satiation_chat_closes_connection():
    st = State(needs={"connection": 0.80})
    apply_satiation(st, "chat")
    assert st.needs["connection"] == pytest.approx(0.80 + SATIATION["chat"]["connection"])


def test_satiation_clamps_at_zero():
    st = State(needs={"connection": 0.10})  # -0.50 -> clamp at 0
    apply_satiation(st, "chat")
    assert st.needs["connection"] == 0.0


def test_satiation_deep_closes_novelty_harder_than_chat():
    deep = State(needs={"novelty": 0.90})
    chat = State(needs={"novelty": 0.90})
    apply_satiation(deep, "deep")
    apply_satiation(chat, "chat")
    assert deep.needs["novelty"] < chat.needs["novelty"]  # deep is the "filling meal"


def test_satiation_idle_cools_intensity_up_and_rests():
    st = State(needs={"intensity": 0.40, "rest": 0.40})
    apply_satiation(st, "idle")
    assert st.needs["intensity"] == pytest.approx(
        0.40 + SATIATION["idle"]["intensity"]
    )  # intensity rises
    assert st.needs["rest"] == pytest.approx(0.40 + SATIATION["idle"]["rest"])  # rest


def test_satiation_only_touches_present_needs():
    st = State(needs={"connection": 0.50})  # novelty/rest/intensity absent
    apply_satiation(st, "deep")
    assert set(st.needs) == {"connection"}


def test_satiation_unknown_event_is_noop():
    st = State(needs={"connection": 0.50})
    apply_satiation(st, "не-подія")
    assert st.needs["connection"] == 0.50


# --- v0.10: the reflection need + the `thought` satiation event ---


def test_reflection_is_a_configured_need():
    assert "reflection" in DRIFT and DRIFT["reflection"] > 0  # slow upward drift
    assert NEED_TRIGGERS["reflection"]["action"] == "thought"  # crossing fires a thought


def test_drift_raises_reflection():
    st = State(needs={"reflection": 0.0})
    drift(st)
    assert st.needs["reflection"] == pytest.approx(DRIFT["reflection"])


def test_satiation_thought_discharges_reflection():
    st = State(needs={"reflection": 0.90})
    apply_satiation(st, "thought")  # the inner-monologue discharge (gathers the scattered thoughts)
    assert st.needs["reflection"] == pytest.approx(0.90 + SATIATION["thought"]["reflection"])
    assert SATIATION["thought"]["reflection"] < 0  # it lowers «незібраність»
