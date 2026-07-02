"""Unit: fact extraction + digest on the SDK (v1.4 — FACTS_MODEL/Sonnet, no `claude -p`), and the
parser (KILN-023/024, KILN-071)."""

from __future__ import annotations

import kiln.memory as mem
from kiln.config import FACTS_MODEL


def _fake_anthropic(monkeypatch, reply_text, seen=None):
    """Patch anthropic.Anthropic so the SDK call returns `reply_text`; records kwargs in `seen`."""

    class _Block:
        type = "text"
        text = reply_text

    class _Msg:
        content = [_Block()]

    class _Messages:
        def create(self, **kwargs):
            if seen is not None:
                seen.update(kwargs)
            return _Msg()

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _Client)


def _no_subprocess(monkeypatch):
    """Guard: the facts layer must NOT spawn a subprocess (v1.4 SDK path)."""
    import kiln.memory as memmod

    # memory no longer imports subprocess; assert it's gone and trap the stdlib just in case.
    assert not hasattr(memmod, "subprocess"), "memory.py must not use subprocess (SDK-only facts)"
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("facts must not spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)


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


# --- extract_facts ----------------------------------------------------------


def test_extract_facts_dry_run_is_stub():
    assert mem.extract_facts([{"role": "user", "text": "привіт"}], [], live=False) == []


def test_extract_facts_empty_history():
    assert mem.extract_facts([], [], live=True) == []


def test_extract_facts_uses_the_sdk_no_subprocess(monkeypatch):
    _no_subprocess(monkeypatch)
    seen = {}
    _fake_anthropic(monkeypatch, '["Любить шахи", "Пише агентів"]', seen)
    out = mem.extract_facts([{"role": "user", "text": "привіт"}], ["вже відоме"], live=True)
    assert out == ["Любить шахи", "Пише агентів"]
    assert seen["model"] == FACTS_MODEL  # Sonnet via the Messages API (not claude -p)
    assert seen["messages"][0]["role"] == "user"


def test_extract_facts_refuses_opus_model(monkeypatch):
    """The SDK path must never bill Opus — an Opus facts_model is skipped (returns [])."""
    _fake_anthropic(monkeypatch, '["x"]')  # would return a fact IF it were called
    out = mem.extract_facts([{"role": "user", "text": "x"}], [], live=True, model="claude-opus-4-8")
    assert out == []


def test_extract_facts_sdk_error_returns_empty(monkeypatch):
    import anthropic

    def _boom(*a, **k):
        raise RuntimeError("network")

    class _Client:
        def __init__(self, *a, **k):
            self.messages = type("M", (), {"create": staticmethod(_boom)})()

    monkeypatch.setattr(anthropic, "Anthropic", _Client)
    assert mem.extract_facts([{"role": "user", "text": "x"}], [], live=True) == []


# --- _parse_facts (now parses the model's reply TEXT, not CLI JSON) ---------


def test_parse_facts_json_array():
    assert mem._parse_facts('["a", "b"]') == ["a", "b"]


def test_parse_facts_fenced_and_prose():
    assert mem._parse_facts('ось факти:\n```json\n["a", "b"]\n```') == ["a", "b"]


def test_parse_facts_line_fallback():
    assert mem._parse_facts("- факт один\n- факт два") == ["факт один", "факт два"]


def test_parse_facts_empty():
    assert mem._parse_facts("[]") == []
    assert mem._parse_facts("") == []


# --- digest_facts -----------------------------------------------------------


def test_digest_facts_empty_is_blank(monkeypatch):
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store_with_facts([]))
    assert mem.digest_facts(live=True) == ""


def test_digest_facts_dry_run_is_stub(monkeypatch):
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store_with_facts(["факт"]))
    assert mem.digest_facts(live=False).startswith("(dry-run facts digest")


def test_digest_facts_uses_the_sdk_and_caps_lines(monkeypatch):
    _no_subprocess(monkeypatch)
    seen = {}
    twelve = "\n".join(f"рядок {i}" for i in range(1, 13))  # 12 lines — over the cap
    _fake_anthropic(monkeypatch, twelve, seen)
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store_with_facts(["a", "b"]))
    monkeypatch.setattr(mem, "FACTS_DIGEST_LINES", 8)
    out = mem.digest_facts(live=True)
    assert len(out.splitlines()) == 8  # capped at FACTS_DIGEST_LINES
    assert seen["model"] == FACTS_MODEL  # Sonnet via the Messages API


def test_digest_facts_respects_config_line_count(monkeypatch):
    _fake_anthropic(monkeypatch, "1\n2\n3\n4\n5")
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store_with_facts(["a"]))
    monkeypatch.setattr(mem, "FACTS_DIGEST_LINES", 3)
    assert len(mem.digest_facts(live=True).splitlines()) == 3


def test_digest_facts_refuses_opus_model(monkeypatch):
    _fake_anthropic(monkeypatch, "portrait")
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store_with_facts(["a"]))
    assert mem.digest_facts(live=True, model="claude-opus-4-8") == ""


# --- MAX_FACTS + FACTS_ENABLED ----------------------------------------------


def test_digest_facts_caps_input_to_max_facts(monkeypatch):
    """MAX_FACTS feeds only the most recent N facts to the digest (bounds the per-start input)."""
    monkeypatch.setattr(
        mem, "load_store", lambda *a, **k: _store_with_facts(["alpha", "beta", "gamma", "delta"])
    )
    monkeypatch.setattr(mem, "MAX_FACTS", 2)
    seen = {}
    _fake_anthropic(monkeypatch, "x", seen)
    mem.digest_facts(live=True)
    prompt = seen["messages"][0]["content"]
    assert "gamma" in prompt and "delta" in prompt  # the last 2
    assert "alpha" not in prompt and "beta" not in prompt  # older dropped


def test_facts_disabled_skips_extraction_and_digest(monkeypatch):
    """FACTS_ENABLED=0 → no extraction on close and no digest in the prompt (no model call)."""
    monkeypatch.setattr(mem, "FACTS_ENABLED", False)
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store_with_facts(["a", "b"]))

    def _boom(*a, **k):  # must not reach the model when disabled
        raise AssertionError("the SDK was called while FACTS_ENABLED is off")

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _boom)
    assert mem.extract_facts([{"role": "user", "text": "x"}], [], live=True) == []
    assert mem.digest_facts(live=True) == ""
