"""Unit: модель потреб — drift (зростання + надолуження + кліп) і satiation (закриття)."""

from __future__ import annotations

import pytest

from kiln.config import DRIFT, SATIATION
from kiln.engine import State, apply_satiation, drift


def test_drift_raises_each_need_by_its_rate():
    st = State(needs={"connection": 0.0, "intensity": 0.0})
    drift(st)
    assert st.needs["connection"] == pytest.approx(DRIFT["connection"])
    assert st.needs["intensity"] == pytest.approx(DRIFT["intensity"])


def test_drift_catch_up_multiplies_by_ticks():
    """ticks>1 — надолуження дрейфу за реальний час (×ticks)."""
    st = State(needs={"novelty": 0.0})
    drift(st, ticks=3)
    assert st.needs["novelty"] == pytest.approx(DRIFT["novelty"] * 3)


def test_drift_clamps_at_one():
    st = State(needs={"intensity": 0.99})
    drift(st, ticks=10)
    assert st.needs["intensity"] == 1.0


def test_drift_ignores_needs_without_a_rate():
    st = State(needs={"foo": 0.5})  # 'foo' немає в DRIFT -> росту нема
    drift(st, ticks=4)
    assert st.needs["foo"] == 0.5


def test_satiation_chat_closes_connection():
    st = State(needs={"connection": 0.80})
    apply_satiation(st, "chat")
    assert st.needs["connection"] == pytest.approx(0.80 + SATIATION["chat"]["connection"])


def test_satiation_clamps_at_zero():
    st = State(needs={"connection": 0.10})  # -0.50 -> кліп на 0
    apply_satiation(st, "chat")
    assert st.needs["connection"] == 0.0


def test_satiation_deep_closes_novelty_harder_than_chat():
    deep = State(needs={"novelty": 0.90})
    chat = State(needs={"novelty": 0.90})
    apply_satiation(deep, "deep")
    apply_satiation(chat, "chat")
    assert deep.needs["novelty"] < chat.needs["novelty"]  # deep — «ситна їжа»


def test_satiation_idle_cools_intensity_up_and_rests():
    st = State(needs={"intensity": 0.40, "rest": 0.40})
    apply_satiation(st, "idle")
    assert st.needs["intensity"] == pytest.approx(
        0.40 + SATIATION["idle"]["intensity"]
    )  # напруга росте
    assert st.needs["rest"] == pytest.approx(0.40 + SATIATION["idle"]["rest"])  # відпочинок


def test_satiation_only_touches_present_needs():
    st = State(needs={"connection": 0.50})  # novelty/rest/intensity відсутні
    apply_satiation(st, "deep")
    assert set(st.needs) == {"connection"}


def test_satiation_unknown_event_is_noop():
    st = State(needs={"connection": 0.50})
    apply_satiation(st, "не-подія")
    assert st.needs["connection"] == 0.50
