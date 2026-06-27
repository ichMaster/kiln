"""
Contract + unit: store summaries → system prompt (KILN-020).

`load_memory` now reads the `.kiln/store.json` `summaries` (not `memory.md`); `build_system`
composes canon + those summaries. Pinned against a fixture store (mock — zero paid calls).
"""

from __future__ import annotations

import kiln.memory as mem


def _store(summaries):
    return {"sessions": [], "messages": {}, "summaries": summaries}


def summ(text):
    return {"session_id": "s", "stamp": "d", "text": text}


def test_load_memory_reads_store_summaries_in_order(monkeypatch):
    monkeypatch.setattr(
        mem,
        "load_store",
        lambda *a, **k: _store(
            [
                {"session_id": "s1", "stamp": "2026-06-26", "text": "перша розмова"},
                {"session_id": "s2", "stamp": "2026-06-27", "text": "друга розмова"},
            ]
        ),
    )
    blob = mem.load_memory()
    assert "перша розмова" in blob and "друга розмова" in blob
    assert blob.index("перша") < blob.index("друга")  # oldest -> newest


def test_load_memory_empty_store_is_blank(monkeypatch):
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store([]))
    assert mem.load_memory() == ""


def test_load_memory_skips_empty_summary_text(monkeypatch):
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store([summ(""), summ("є")]))
    assert mem.load_memory().strip() and "є" in mem.load_memory()


def test_build_system_includes_store_summaries(monkeypatch):
    """Contract: canon + the store's summaries reach the system prompt (one source)."""
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store([summ("ФАКТ")]))
    system = mem.build_system("CANON", mem.load_memory())
    assert "CANON" in system and "ФАКТ" in system


def test_build_system_empty_store_is_canon_only(monkeypatch):
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _store([]))
    assert mem.build_system("CANON", mem.load_memory()) == "CANON"
