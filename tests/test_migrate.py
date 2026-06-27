"""Unit: migrate_legacy — fold legacy memory.md + history/*.json into the store (KILN-021)."""

from __future__ import annotations

import json

import kiln.memory as mem
from kiln import store as kstore


def _legacy(tmp_path):
    """Build fixture legacy files; return (memory_file, history_dir)."""
    mem_md = tmp_path / "memory.md"
    mem_md.write_text(
        "kiln — Memory\n\n"
        "## Conversation 2026-06-26 10:49\nперший підсумок\n\n"
        "## Conversation 2026-06-26 11:51\nдругий підсумок\n",
        encoding="utf-8",
    )
    hist = tmp_path / "history"
    hist.mkdir()
    (hist / "session-2026-06-26_10-49-00.json").write_text(
        json.dumps(
            {
                "session": "2026-06-26_10-49-00",
                "started_at": "2026-06-26T10:49:00",
                "ended_at": "2026-06-26T10:51:00",
                "mode": "live",
                "turns": 2,
                "history": [
                    {"role": "user", "text": "привіт"},
                    {"role": "assistant", "text": "вітаю"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return mem_md, hist


def _wire_store(monkeypatch, tmp_path):
    store_path = tmp_path / "store.json"
    monkeypatch.setattr(mem, "load_store", lambda *a, **k: kstore.load_store(store_path))
    monkeypatch.setattr(mem, "save_store", lambda s, *a, **k: kstore.save_store(s, store_path))
    return store_path


def test_migrate_folds_legacy_files_losslessly(monkeypatch, tmp_path):
    mem_md, hist = _legacy(tmp_path)
    store_path = _wire_store(monkeypatch, tmp_path)

    n = mem.migrate_legacy(memory_file=mem_md, history_dir=hist)
    assert n == 3  # 2 summaries + 1 session

    s = kstore.load_store(store_path)
    assert [x["text"] for x in s["summaries"]] == ["перший підсумок", "другий підсумок"]
    assert len(s["sessions"]) == 1 and s["sessions"][0]["id"] == "2026-06-26_10-49-00"
    assert s["sessions"][0]["turns"] == 2
    assert s["messages"]["2026-06-26_10-49-00"][0]["text"] == "привіт"


def test_migrate_is_idempotent(monkeypatch, tmp_path):
    mem_md, hist = _legacy(tmp_path)
    _wire_store(monkeypatch, tmp_path)
    assert mem.migrate_legacy(memory_file=mem_md, history_dir=hist) == 3
    # store now has data -> a re-run imports nothing (no duplication)
    assert mem.migrate_legacy(memory_file=mem_md, history_dir=hist) == 0


def test_migrate_nothing_when_no_legacy(monkeypatch, tmp_path):
    _wire_store(monkeypatch, tmp_path)
    n = mem.migrate_legacy(memory_file=tmp_path / "nope.md", history_dir=tmp_path / "nohist")
    assert n == 0


def test_migrate_handles_session_id_collisions(monkeypatch, tmp_path):
    """Two files closed the same second (same inner 'session' stamp) keep DISTINCT messages."""
    hist = tmp_path / "history"
    hist.mkdir()
    for suffix, who in [("", "a"), ("-2", "b")]:
        (hist / f"session-2026-06-26_10-49-00{suffix}.json").write_text(
            json.dumps(
                {
                    "session": "2026-06-26_10-49-00",  # SAME inner stamp -> the collision
                    "started_at": "x",
                    "ended_at": "y",
                    "mode": "live",
                    "turns": 1,
                    "history": [{"role": "user", "text": who}],
                }
            ),
            encoding="utf-8",
        )
    store_path = _wire_store(monkeypatch, tmp_path)
    assert mem.migrate_legacy(memory_file=tmp_path / "none.md", history_dir=hist) == 2
    s = kstore.load_store(store_path)
    assert len(s["sessions"]) == 2 and len(s["messages"]) == 2  # no overwrite
    assert {m[0]["text"] for m in s["messages"].values()} == {"a", "b"}  # both transcripts kept
