"""Unit: summarize() runs on Haiku via the Anthropic Messages API (cheap/fast, not claude -p)."""

from __future__ import annotations

import kiln.memory as mem
from kiln.config import CHAT_MODEL


def test_summarize_dry_run_is_a_stub():
    assert mem.summarize([{"role": "user", "text": "привіт"}], live=False).startswith("(dry-run")


def test_summarize_empty_history_is_empty():
    assert mem.summarize([], live=True) == ""


def test_summarize_uses_haiku_via_messages_api(monkeypatch):
    seen = {}

    class _Block:
        type = "text"
        text = "підсумок"

    class _Msg:
        content = [_Block()]

    class _Messages:
        def create(self, **kwargs):
            seen.update(kwargs)
            return _Msg()

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _Client)
    out = mem.summarize([{"role": "user", "text": "привіт"}], live=True)
    assert out == "підсумок"
    assert seen["model"] == CHAT_MODEL  # Haiku, via the Messages API (not claude -p / Opus)
    assert seen["messages"][0]["role"] == "user"  # the transcript prompt goes in as a user message


def test_summarize_refuses_opus_chat_model(monkeypatch):
    """Guard the invariant: if CHAT_MODEL were Opus, summarize must NOT bill it via the API key."""
    monkeypatch.setattr(mem, "CHAT_MODEL", "claude-opus-4-8")
    assert mem.summarize([{"role": "user", "text": "привіт"}], live=True) == ""


def test_summarize_prompt_uses_configured_sentence_count(monkeypatch):
    """The summary prompt asks for SUMMARY_SENTENCES sentences (overridable from .env)."""
    seen = {}

    class _Block:
        type = "text"
        text = "x"

    class _Messages:
        def create(self, **kwargs):
            seen.update(kwargs)
            return type("M", (), {"content": [_Block()]})()

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _Client)
    monkeypatch.setattr(mem, "SUMMARY_SENTENCES", 3)
    mem.summarize([{"role": "user", "text": "привіт"}], live=True)
    assert "3 речень" in seen["messages"][0]["content"]  # the configured length
