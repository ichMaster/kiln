"""Contract + unit: the history turn shape — {role, text, at}; helpers ignore `at` (KILN-032).
Speaker labels come from config (USER_NAME / AGENT_NAME) — pinned here for determinism."""

from __future__ import annotations

import pytest

import kiln.history as h
from kiln.history import (
    ROLE_BOT,
    ROLE_USER,
    fmt_stamp,
    role_label,
    strip_leading_stamp,
    to_messages,
    to_transcript,
    turn,
)


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


def test_fmt_stamp():
    assert (
        fmt_stamp("2026-06-28T11:52:00") == "[Нд 28.06.2026 11:52]"
    )  # неділя; used by the timeline
    assert fmt_stamp(None) == "" and fmt_stamp("not-a-date") == ""


def test_strip_leading_stamp():
    assert strip_leading_stamp("[Нд 28.06.2026 15:46] Хе.") == "Хе."
    assert strip_leading_stamp("[Нд 15:46] [Нд 15:46] двічі") == "двічі"  # multiple echoes
    assert strip_leading_stamp("без штампа") == "без штампа"  # untouched
    assert strip_leading_stamp("[note] не час") == "[note] не час"  # no HH:MM -> kept


def test_to_messages_has_no_timestamp_in_live_convo():
    h = [turn(ROLE_USER, "привіт", at="2026-06-28T11:52:00")]
    assert to_messages(h) == [{"role": "user", "content": "привіт"}]  # no `[time]` prefix


def test_to_messages_strips_echoed_stamp():
    h = [{"role": "assistant", "text": "[Нд 28.06.2026 15:46] Хе. 🔥", "at": "2026-06-28T15:46:00"}]
    assert to_messages(h) == [{"role": "assistant", "content": "Хе. 🔥"}]  # echoed stamp removed


def test_to_transcript_names_no_timestamp():
    hist = [turn(ROLE_USER, "привіт", at="x"), turn(ROLE_BOT, "вітаю", at="y")]
    assert to_transcript(hist) == "Користувач: привіт\nАгніка: вітаю"  # named, no timestamps


def test_role_label_uses_config_names(monkeypatch):
    monkeypatch.setattr(h, "USER_NAME", "Віталік")  # overrides the autouse pin
    assert role_label(ROLE_USER) == "Віталік"
    assert role_label(ROLE_BOT) == "Агніка"
    assert role_label("system") == "system"  # unknown role -> raw
