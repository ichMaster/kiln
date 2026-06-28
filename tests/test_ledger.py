"""Unit: the usage ledger — one append-only line per session, malformed-tolerant (KILN-028)."""

from __future__ import annotations

from kiln.ledger import append_session, read_ledger


def _entry(sid, cost):
    return {
        "session_id": sid,
        "model": "claude-opus-4-8",
        "started_at": "a",
        "ended_at": "b",
        "turns": 3,
        "input": 100,
        "output": 20,
        "cache_read": 50,
        "cache_write": 5,
        "cache_ttl": "5m",
        "cost_usd": cost,
    }


def test_append_writes_exactly_one_line_per_session(tmp_path):
    p = tmp_path / "usage-ledger.jsonl"
    append_session(_entry("s1", 0.1), p)
    append_session(_entry("s2", 0.2), p)
    assert len(p.read_text(encoding="utf-8").strip().splitlines()) == 2  # one line each
    recs = read_ledger(p)
    assert [r["session_id"] for r in recs] == ["s1", "s2"]
    assert recs[0]["cost_usd"] == 0.1 and recs[0]["cache_read"] == 50


def test_read_ledger_skips_malformed_lines(tmp_path):
    p = tmp_path / "usage-ledger.jsonl"
    append_session(_entry("ok", 0.5), p)
    with p.open("a", encoding="utf-8") as f:
        f.write("{ not json\n\n")  # a corrupt line + a blank line
    append_session(_entry("ok2", 0.6), p)
    assert [r["session_id"] for r in read_ledger(p)] == ["ok", "ok2"]  # malformed/blank skipped


def test_read_ledger_missing_file_is_empty(tmp_path):
    assert read_ledger(tmp_path / "nope.jsonl") == []
