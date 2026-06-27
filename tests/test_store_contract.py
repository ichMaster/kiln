"""
Contract + unit: the `.kiln/store.json` persistence store (KILN-017).

Pins the store schema (sessions/messages/summaries), atomic write + `.bak`, and corrupt-file
recovery — all against a tmp_path store (no real `.kiln/`, zero paid calls).
"""

from __future__ import annotations

import json

from kiln.store import empty_store, load_store, save_store

STORE_KEYS = {"sessions", "messages", "summaries"}


def test_empty_store_shape():
    assert set(empty_store()) == STORE_KEYS
    assert empty_store() == {"sessions": [], "messages": {}, "summaries": []}


def test_missing_file_loads_fresh(tmp_path):
    assert load_store(tmp_path / "store.json") == empty_store()


def test_round_trip_preserves_all_sections(tmp_path):
    p = tmp_path / "store.json"
    store = {
        "sessions": [{"id": "s1", "started_at": "a", "ended_at": "b", "mode": "live", "turns": 2}],
        "messages": {"s1": [{"role": "user", "text": "привіт"}]},
        "summaries": [{"session_id": "s1", "stamp": "2026-06-27", "text": "підсумок"}],
    }
    save_store(store, p)
    assert load_store(p) == store
    assert "привіт" in p.read_text(encoding="utf-8")  # ensure_ascii=False -> readable Ukrainian


def test_save_is_atomic_and_keeps_bak(tmp_path):
    p, bak, tmp = p_b_t(tmp_path)
    save_store(one_summary("v1"), p)
    assert not bak.exists()  # first write -> no .bak yet
    save_store(one_summary("v2"), p)
    assert bak.exists()  # second write backed the first up
    assert load_store(p)["summaries"][0]["text"] == "v2"  # live file = newest
    assert json.loads(bak.read_text())["summaries"][0]["text"] == "v1"  # .bak = previous good
    assert not tmp.exists()  # no temp file left behind


def test_corrupt_file_recovers_from_bak(tmp_path):
    p, bak, _ = p_b_t(tmp_path)
    save_store(one_summary("good"), p)
    save_store(one_summary("newer"), p)
    p.write_text("{ this is not json", encoding="utf-8")  # corrupt the live file
    assert load_store(p)["summaries"][0]["text"] == "good"  # recovered from .bak (previous good)


def test_corrupt_with_no_bak_loads_fresh(tmp_path):
    p = tmp_path / "store.json"
    p.write_text("not json at all", encoding="utf-8")
    assert load_store(p) == empty_store()


def test_heals_missing_sections(tmp_path):
    p = tmp_path / "store.json"
    p.write_text(
        json.dumps({"summaries": [{"session_id": "s", "stamp": "x", "text": "t"}]}),
        encoding="utf-8",
    )
    store = load_store(p)
    assert set(store) == STORE_KEYS  # sessions/messages added back
    assert store["summaries"] and store["sessions"] == [] and store["messages"] == {}


def p_b_t(tmp_path):
    p = tmp_path / "store.json"
    return p, p.with_suffix(".json.bak"), p.with_suffix(".json.tmp")


def one_summary(text):
    return {**empty_store(), "summaries": [{"session_id": "s", "stamp": "x", "text": text}]}
