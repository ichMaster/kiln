"""Unit: extract_facts() on Opus via `claude -p` (thinking on, key stripped) + the parser (KILN-023)."""

from __future__ import annotations

import kiln.memory as mem
from kiln.config import DEEP_MODEL, THINKING_TOKENS


def test_extract_facts_dry_run_is_stub():
    assert mem.extract_facts([{"role": "user", "text": "привіт"}], [], live=False) == []


def test_extract_facts_empty_history():
    assert mem.extract_facts([], [], live=True) == []


def test_extract_facts_uses_opus_with_thinking_and_no_api_key(monkeypatch):
    seen = {}

    class _R:
        returncode = 0
        stdout = '{"result": "[\\"Любить шахи\\", \\"Пише агентів\\"]"}'
        stderr = ""

    def _run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return _R()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-should-be-stripped")
    monkeypatch.setattr(mem.subprocess, "run", _run)
    out = mem.extract_facts([{"role": "user", "text": "привіт"}], ["вже відоме"], live=True)
    assert out == ["Любить шахи", "Пише агентів"]
    assert "--model" in seen["cmd"] and DEEP_MODEL in seen["cmd"]  # Opus via claude -p
    assert seen["env"]["MAX_THINKING_TOKENS"] == str(THINKING_TOKENS)  # thinking ON
    assert "ANTHROPIC_API_KEY" not in seen["env"]  # Opus never billed via the API key


def test_extract_facts_cli_error_returns_empty(monkeypatch):
    class _R:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(mem.subprocess, "run", lambda *a, **k: _R())
    assert mem.extract_facts([{"role": "user", "text": "x"}], [], live=True) == []


def test_parse_facts_json_array():
    assert mem._parse_facts('{"result": "[\\"a\\", \\"b\\"]"}') == ["a", "b"]


def test_parse_facts_fenced_and_prose():
    out = mem._parse_facts('{"result": "ось факти:\\n```json\\n[\\"a\\", \\"b\\"]\\n```"}')
    assert out == ["a", "b"]


def test_parse_facts_line_fallback():
    assert mem._parse_facts('{"result": "- факт один\\n- факт два"}') == ["факт один", "факт два"]


def test_parse_facts_empty():
    assert mem._parse_facts('{"result": "[]"}') == []
    assert mem._parse_facts('{"result": ""}') == []
