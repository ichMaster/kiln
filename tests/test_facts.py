"""Unit: fact extraction + digest on Opus via `claude -p`, and the parser (KILN-023/024)."""

from __future__ import annotations

import json

import kiln.memory as mem
from kiln.config import DEEP_MODEL, THINKING_TOKENS


def _result(stdout, code=0):
    return type("R", (), {"returncode": code, "stdout": stdout, "stderr": "boom"})()


def _store_with_facts(texts):
    return {
        "sessions": [],
        "messages": {},
        "summaries": [],
        "facts": [
            {"id": f"f{i}", "text": t, "first_seen": "x", "last_seen": "x", "source_session": "s"}
            for i, t in enumerate(texts, 1)
        ],
    }


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


# --- digest_facts (KILN-024) ---


def test_digest_facts_empty_is_blank(monkeypatch):
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store_with_facts([]))
    assert mem.digest_facts(live=True) == ""


def test_digest_facts_dry_run_is_stub(monkeypatch):
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store_with_facts(["факт"]))
    assert mem.digest_facts(live=False).startswith("(dry-run facts digest")


def test_digest_facts_opus_thinking_no_key_and_line_cap(monkeypatch):
    seen = {}
    twelve = "\n".join(f"рядок {i}" for i in range(1, 13))  # 12 lines — over the cap

    def _run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return _result(json.dumps({"result": twelve}))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-strip")
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store_with_facts(["a", "b"]))
    monkeypatch.setattr(mem, "FACTS_DIGEST_LINES", 8)
    monkeypatch.setattr(mem.subprocess, "run", _run)
    out = mem.digest_facts(live=True)
    assert len(out.splitlines()) == 8  # capped at FACTS_DIGEST_LINES
    assert "--model" in seen["cmd"] and DEEP_MODEL in seen["cmd"]  # Opus via claude -p
    assert seen["env"]["MAX_THINKING_TOKENS"] == str(THINKING_TOKENS)  # thinking ON
    assert "ANTHROPIC_API_KEY" not in seen["env"]  # Opus never billed via the API key


def test_digest_facts_respects_config_line_count(monkeypatch):
    """The cap follows FACTS_DIGEST_LINES (overridable from .env)."""
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store_with_facts(["a"]))
    monkeypatch.setattr(mem, "FACTS_DIGEST_LINES", 3)
    monkeypatch.setattr(
        mem.subprocess, "run", lambda *a, **k: _result(json.dumps({"result": "1\n2\n3\n4\n5"}))
    )
    assert len(mem.digest_facts(live=True).splitlines()) == 3


def test_digest_facts_cli_error_is_blank(monkeypatch):
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store_with_facts(["a"]))
    monkeypatch.setattr(mem.subprocess, "run", lambda *a, **k: _result("", code=1))
    assert mem.digest_facts(live=True) == ""


# --- MAX_FACTS + FACTS_ENABLED (KILN-024 follow-on knobs) ---


def test_digest_facts_caps_input_to_max_facts(monkeypatch):
    """MAX_FACTS feeds only the most recent N facts to the digest (bounds the per-start input)."""
    monkeypatch.setattr(
        mem, "load_store", lambda *a, **k: _store_with_facts(["alpha", "beta", "gamma", "delta"])
    )
    monkeypatch.setattr(mem, "MAX_FACTS", 2)
    seen = {}

    def _run(cmd, **kwargs):
        seen["prompt"] = cmd[-1]  # the prompt is the last positional arg
        return _result(json.dumps({"result": "x"}))

    monkeypatch.setattr(mem.subprocess, "run", _run)
    mem.digest_facts(live=True)
    assert "gamma" in seen["prompt"] and "delta" in seen["prompt"]  # the last 2
    assert "alpha" not in seen["prompt"] and "beta" not in seen["prompt"]  # older dropped


def test_facts_disabled_skips_extraction_and_digest(monkeypatch):
    """FACTS_ENABLED=0 → no extraction on close and no digest in the prompt (no model call)."""
    monkeypatch.setattr(mem, "FACTS_ENABLED", False)
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store_with_facts(["a", "b"]))

    def _boom(*a, **k):  # must not reach the subprocess when disabled
        raise AssertionError("claude -p called while FACTS_ENABLED is off")

    monkeypatch.setattr(mem.subprocess, "run", _boom)
    assert mem.extract_facts([{"role": "user", "text": "x"}], [], live=True) == []
    assert mem.digest_facts(live=True) == ""
