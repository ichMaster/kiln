"""Unit: summarize() runs on Opus via `claude -p` with thinking on and the API key stripped."""

from __future__ import annotations

import kiln.memory as mem
from kiln.config import DEEP_MODEL, THINKING_TOKENS, claude_env


def test_claude_env_strips_key_and_turns_on_thinking(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = claude_env()
    assert "ANTHROPIC_API_KEY" not in env  # never billed via the API key
    assert env["MAX_THINKING_TOKENS"] == str(THINKING_TOKENS)  # thinking ON for every claude -p
    assert env.get("PATH") == "/usr/bin"  # the rest of the env is preserved
    # an explicit extra overrides the default budget
    assert claude_env(MAX_THINKING_TOKENS="1234")["MAX_THINKING_TOKENS"] == "1234"


def test_summarize_dry_run_is_a_stub():
    assert mem.summarize([{"role": "user", "text": "привіт"}], live=False).startswith("(dry-run")


def test_summarize_empty_history_is_empty():
    assert mem.summarize([], live=True) == ""


def test_summarize_uses_opus_with_thinking_and_no_api_key(monkeypatch):
    seen = {}

    class _R:
        returncode = 0
        stdout = '{"result": "підсумок"}'
        stderr = ""

    def _run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return _R()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-should-be-stripped")
    monkeypatch.setattr(mem.subprocess, "run", _run)
    out = mem.summarize([{"role": "user", "text": "привіт"}], live=True)
    assert out == "підсумок"
    assert "--model" in seen["cmd"] and DEEP_MODEL in seen["cmd"]  # Opus, via claude -p
    assert seen["env"]["MAX_THINKING_TOKENS"] == str(THINKING_TOKENS)  # thinking ON
    assert "ANTHROPIC_API_KEY" not in seen["env"]  # Opus never billed via the API key
