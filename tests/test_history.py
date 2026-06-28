"""Contract + unit: the history turn shape — {role, text, at}; helpers ignore `at` (KILN-032).
Speaker labels come from config (USER_NAME / AGENT_NAME) — pinned here for determinism."""

from __future__ import annotations

import pytest

import kiln.history as h
from kiln.history import ROLE_BOT, ROLE_USER, role_label, to_messages, to_transcript, turn


@pytest.fixture(autouse=True)
def _pin_names(monkeypatch):
    monkeypatch.setattr(h, "USER_NAME", "Користувач")
    monkeypatch.setattr(h, "AGENT_NAME", "Агніка")


def test_turn_shape_with_explicit_at():
    t = turn(ROLE_USER, "привіт", at="2026-06-28T11:52:00")
    assert t == {"role": "user", "text": "привіт", "at": "2026-06-28T11:52:00"}


def test_turn_default_at_is_iso_timestamp():
    t = turn(ROLE_BOT, "вітаю")
    assert t["role"] == "assistant" and t["text"] == "вітаю"
    assert isinstance(t["at"], str) and "T" in t["at"]  # ISO, default = now


def test_to_messages_ignores_at():
    h = [turn(ROLE_USER, "привіт", at="2026-06-28T11:52:00")]
    assert to_messages(h) == [{"role": "user", "content": "привіт"}]  # no `at` leaks through


def test_to_transcript_uses_names_ignores_at():
    hist = [turn(ROLE_USER, "привіт", at="x"), turn(ROLE_BOT, "вітаю", at="y")]
    assert to_transcript(hist) == "Користувач: привіт\nАгніка: вітаю"  # named speakers, no times


def test_role_label_uses_config_names(monkeypatch):
    monkeypatch.setattr(h, "USER_NAME", "Віталік")  # overrides the autouse pin
    assert role_label(ROLE_USER) == "Віталік"
    assert role_label(ROLE_BOT) == "Агніка"
    assert role_label("system") == "system"  # unknown role -> raw
