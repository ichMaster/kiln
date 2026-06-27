"""Unit: prune_history — drop non-conversation turns before a session is stored (KILN-019)."""

from __future__ import annotations

from kiln.config import REST_MESSAGE
from kiln.memory import prune_history


def test_prune_removes_empty_slash_and_resting():
    turns = [
        {"role": "user", "text": "привіт"},
        {"role": "assistant", "text": "вітаю"},
        {"role": "user", "text": ""},  # empty
        {"role": "user", "text": "  "},  # whitespace-only
        {"role": "user", "text": "/status"},  # slash-command echo
        {"role": "assistant", "text": REST_MESSAGE},  # resting notice
    ]
    assert prune_history(turns) == [
        {"role": "user", "text": "привіт"},
        {"role": "assistant", "text": "вітаю"},
    ]


def test_prune_keeps_real_conversation_unchanged():
    turns = [
        {"role": "user", "text": "як справи?"},
        {"role": "assistant", "text": "добре, дякую"},
    ]
    assert prune_history(turns) == turns


def test_prune_noise_only_session_is_empty():
    turns = [
        {"role": "user", "text": "/help"},
        {"role": "assistant", "text": REST_MESSAGE},
        {"role": "user", "text": ""},
    ]
    assert prune_history(turns) == []


def test_prune_empty_input_is_empty():
    assert prune_history([]) == []
