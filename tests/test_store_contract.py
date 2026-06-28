"""
Contract + unit: the `.kiln/store.json` persistence store (KILN-017, +`facts` KILN-022).

Pins the store schema (sessions/messages/summaries/facts), atomic write + `.bak`, corrupt-file
recovery, and the `add_facts` dedupe — all against a tmp_path store (no real `.kiln/`, zero paid
calls).
"""

from __future__ import annotations

import json

from kiln.store import add_facts, add_thought, empty_store, load_store, save_store

STORE_KEYS = {"sessions", "messages", "summaries", "facts", "thoughts"}


def test_empty_store_shape():
    assert set(empty_store()) == STORE_KEYS
    assert empty_store() == {
        "sessions": [],
        "messages": {},
        "summaries": [],
        "facts": [],
        "thoughts": [],
    }


def test_missing_file_loads_fresh(tmp_path):
    assert load_store(tmp_path / "store.json") == empty_store()


def test_round_trip_preserves_all_sections(tmp_path):
    p = tmp_path / "store.json"
    store = {
        "sessions": [{"id": "s1", "started_at": "a", "ended_at": "b", "mode": "live", "turns": 2}],
        "messages": {"s1": [{"role": "user", "text": "привіт"}]},
        "summaries": [{"session_id": "s1", "stamp": "2026-06-27", "text": "підсумок"}],
        "facts": [
            {
                "id": "f1",
                "text": "Віталік пише агентів",
                "first_seen": "2026-06-27",
                "last_seen": "2026-06-27",
                "source_session": "s1",
            }
        ],
        "thoughts": [
            {
                "id": "t1",
                "text": "тихо",
                "at": "2026-06-27T20:00:00",
                "session": "s1",
                "shown": False,
            }
        ],
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
    assert set(store) == STORE_KEYS  # sessions/messages/facts added back
    assert store["summaries"] and store["sessions"] == [] and store["messages"] == {}


def test_v05_store_without_facts_is_healed(tmp_path):
    """A v0.5-shaped store (no `facts` key) loads forward, healed to facts: []."""
    p = tmp_path / "store.json"
    p.write_text(
        json.dumps(
            {
                "sessions": [{"id": "s1", "started_at": "a", "ended_at": "b", "mode": "x"}],
                "messages": {"s1": [{"role": "user", "text": "hi"}]},
                "summaries": [{"session_id": "s1", "stamp": "x", "text": "t"}],
            }
        ),
        encoding="utf-8",
    )
    store = load_store(p)
    assert store["facts"] == [] and store["sessions"] and store["summaries"]


def test_add_facts_appends_new_and_dedupes_repeats():
    store = empty_store()
    n = add_facts(store, ["Віталік пише агентів", "Любить шахи"], "s1", "2026-06-27")
    assert n == 2
    assert [f["id"] for f in store["facts"]] == ["f1", "f2"]
    assert store["facts"][0]["first_seen"] == store["facts"][0]["last_seen"] == "2026-06-27"
    assert store["facts"][0]["source_session"] == "s1"
    # a repeat (normalized: case/space-insensitive) updates last_seen, adds no row
    n2 = add_facts(store, ["  віталік   ПИШЕ агентів  "], "s2", "2026-06-28")
    assert n2 == 0 and len(store["facts"]) == 2
    assert store["facts"][0]["last_seen"] == "2026-06-28"  # bumped
    assert store["facts"][0]["first_seen"] == "2026-06-27"  # origin kept
    assert store["facts"][0]["source_session"] == "s1"  # origin kept


def test_add_facts_skips_empty_and_returns_count():
    store = empty_store()
    assert add_facts(store, ["", "   ", "реальний факт"], "s1", "x") == 1
    assert len(store["facts"]) == 1 and store["facts"][0]["text"] == "реальний факт"


def test_add_thought_appends_and_round_trips(tmp_path):
    store = empty_store()
    t = add_thought(store, "  думки врозтіч  ", "s1", "2026-06-28T19:00:00")
    assert t == {
        "id": "t1",
        "text": "думки врозтіч",  # stripped
        "at": "2026-06-28T19:00:00",
        "session": "s1",
        "shown": False,
    }
    add_thought(store, "озвучена", "s1", "2026-06-28T19:01:00", shown=True)
    assert [x["id"] for x in store["thoughts"]] == ["t1", "t2"]  # stable ids, order preserved
    assert store["thoughts"][1]["shown"] is True
    p = tmp_path / "store.json"
    save_store(store, p)
    assert load_store(p)["thoughts"] == store["thoughts"]  # round-trips


def test_pre_v010_store_without_thoughts_is_healed(tmp_path):
    """A pre-v0.10 store (no `thoughts` key) loads forward, healed to thoughts: []."""
    p = tmp_path / "store.json"
    p.write_text(json.dumps({"facts": [], "summaries": []}), encoding="utf-8")
    assert load_store(p)["thoughts"] == []


def p_b_t(tmp_path):
    p = tmp_path / "store.json"
    return p, p.with_suffix(".json.bak"), p.with_suffix(".json.tmp")


def one_summary(text):
    return {**empty_store(), "summaries": [{"session_id": "s", "stamp": "x", "text": text}]}
