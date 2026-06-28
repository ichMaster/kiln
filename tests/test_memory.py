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


# --- facts -> system prompt (KILN-025) ---


def test_build_system_appends_facts_section():
    """Contract: the facts digest lands under a dedicated `## Facts about the user` section,
    separate from the canon and the memory summaries, and after the memory block."""
    system = mem.build_system("CANON", "MEMORY-SUMMARIES", "FACTS-DIGEST")
    assert "CANON" in system and "MEMORY-SUMMARIES" in system
    assert "## Facts about the user" in system and "FACTS-DIGEST" in system
    # ordering: canon, then memory, then the facts section
    assert system.index("CANON") < system.index("MEMORY-SUMMARIES")
    assert system.index("MEMORY-SUMMARIES") < system.index("## Facts about the user")


def test_build_system_empty_facts_is_v05_backcompat():
    """`facts=""` reproduces the v0.5 output (canon + memory) byte-for-byte."""
    assert mem.build_system("CANON", "MEM", "") == mem.build_system("CANON", "MEM")
    assert mem.build_system("CANON", "", "") == "CANON"  # nothing -> canon only


def test_build_system_facts_without_memory():
    """Facts attach even when there are no memory summaries (no empty memory block)."""
    system = mem.build_system("CANON", "", "FACTS-DIGEST")
    assert system.startswith("CANON")
    assert "## Facts about the user\nFACTS-DIGEST" in system
    assert "Довга пам'ять" not in system  # no memory section when memory is empty


# --- world block -> system prompt (KILN-034) ---


def test_build_system_appends_world_block_last():
    system = mem.build_system("CANON", "MEM", "FACTS", "## Зараз\nсьогодні неділя")
    assert "CANON" in system and "## Зараз\nсьогодні неділя" in system
    assert system.index("## Facts about the user") < system.index("## Зараз")  # world comes last


def test_build_system_empty_world_is_v07_backcompat():
    assert mem.build_system("CANON", "MEM", "FACTS", "") == mem.build_system(
        "CANON", "MEM", "FACTS"
    )


# --- MEMORY_SUMMARIES cap (how many summaries enter the prompt) ---


def _four_summaries():
    texts = ["перша", "друга", "третя", "четверта"]
    rows = [{"session_id": f"s{i}", "stamp": str(i), "text": t} for i, t in enumerate(texts)]
    return _store(rows)


def test_load_memory_caps_to_last_n_summaries(monkeypatch):
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _four_summaries())
    monkeypatch.setattr(mem, "MEMORY_SUMMARIES", 2)
    blob = mem.load_memory()
    assert "третя" in blob and "четверта" in blob  # the last 2 kept
    assert "перша" not in blob and "друга" not in blob  # older dropped


def test_load_memory_zero_means_all(monkeypatch):
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: _four_summaries())
    monkeypatch.setattr(mem, "MEMORY_SUMMARIES", 0)
    blob = mem.load_memory()
    assert all(t in blob for t in ("перша", "друга", "третя", "четверта"))  # 0 = all
