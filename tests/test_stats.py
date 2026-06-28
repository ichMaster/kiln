"""Unit: SessionStats accumulates the four token buckets + cost (KILN-026/028)."""

from __future__ import annotations

import pytest

from kiln.stats import SessionStats
from kiln.usage import usage_record


def _rec(model, i, o, cr=0, cw=0, cost=None):
    u = {
        "input_tokens": i,
        "output_tokens": o,
        "cache_read_input_tokens": cr,
        "cache_creation_input_tokens": cw,
    }
    return usage_record(model, u, cost)


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


def test_session_stats_accumulates_cost_and_models():
    s = SessionStats()
    s.record("think", _rec("claude-opus-4-8", 100, 20, cost=0.42), latency=1.0)  # actual cost
    s.record("chat", _rec("claude-haiku-4-5", 1_000_000, 0), latency=0.3)  # estimated (haiku $1/1M)
    assert s.cost_usd == pytest.approx(0.42 + 1.0)  # actual + estimate
    assert s.cost_estimated is True  # the chat turn had no actual cost
    assert s.models == ["claude-opus-4-8", "claude-haiku-4-5"]  # distinct, first-seen order


def test_session_stats_snapshot_includes_cost():
    s = SessionStats()
    s.record("think", _rec("opus", 100, 20, cost=0.5), latency=1.0)
    snap = s.snapshot()
    assert snap["cost_usd"] == 0.5 and snap["cost_estimated"] is False


def test_session_stats_cli_calls_and_by_model():
    s = SessionStats()
    s.record("chat", _rec("claude-haiku-4-5", 10, 5), 0.2)  # SDK
    s.record("think", _rec("claude-opus-4-8", 100, 20, cr=50), 1.0)  # claude -p
    s.record("tool", _rec("sonnet", 50, 8), 1.0)  # claude -p
    assert s.cli_calls == 2  # think + tool, not the chat (SDK) call
    assert s.snapshot()["cli_calls"] == 2
    assert s.calls_by_branch == {"chat": 1, "think": 1, "tool": 1}
    assert set(s.by_model) == {"claude-haiku-4-5", "claude-opus-4-8", "sonnet"}
    opus = s.by_model["claude-opus-4-8"]
    assert opus["calls"] == 1 and opus["input"] == 100 and opus["cache_read"] == 50
