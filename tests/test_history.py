"""Contract + unit: the history turn shape — {role, text, at}; helpers ignore `at` (KILN-032)."""

from __future__ import annotations

from kiln.history import ROLE_BOT, ROLE_USER, to_messages, to_transcript, turn


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


def test_to_transcript_ignores_at():
    h = [turn(ROLE_USER, "привіт", at="x"), turn(ROLE_BOT, "вітаю", at="y")]
    assert to_transcript(h) == "Користувач: привіт\nТи: вітаю"  # no timestamps in the transcript
