"""Unit: SessionStats accumulates the four token buckets (KILN-026)."""

from __future__ import annotations

from kiln.stats import SessionStats
from kiln.usage import usage_record


def _rec(model, i, o, cr=0, cw=0):
    u = {
        "input_tokens": i,
        "output_tokens": o,
        "cache_read_input_tokens": cr,
        "cache_creation_input_tokens": cw,
    }
    return usage_record(model, u)


def test_session_stats_accumulates_buckets():
    s = SessionStats()
    s.record("chat", _rec("haiku", 10, 5, cr=100, cw=20), latency=0.5)
    s.record("think", _rec("opus", 30, 8, cr=200, cw=0), latency=1.0)
    assert s.input_total == 40 and s.output_total == 13
    assert s.cache_read_total == 300 and s.cache_write_total == 20
    assert s.tokens_total == 10 + 5 + 30 + 8  # total stays input+output
    assert s.turns == 2


def test_session_stats_snapshot_exposes_buckets():
    s = SessionStats()
    s.record("chat", _rec("haiku", 7, 3, cr=11, cw=2), latency=0.2)
    snap = s.snapshot()
    assert snap["input_total"] == 7 and snap["output_total"] == 3
    assert snap["cache_read_total"] == 11 and snap["cache_write_total"] == 2


def test_session_stats_handles_none_usage():
    s = SessionStats()
    s.record("idle", None, latency=0.1)  # a turn with no model call
    assert s.turns == 1 and s.input_total == 0 and s.cache_read_total == 0
