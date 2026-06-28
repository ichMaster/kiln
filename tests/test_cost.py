"""Unit: cost estimation — price table + bucket/cache/turn math (KILN-027). Pure, no paid calls."""

from __future__ import annotations

import pytest

from kiln import cost

_1M = {"input": 1_000_000, "output": 1_000_000, "cache_read": 1_000_000, "cache_write": 1_000_000}


def test_estimate_cost_opus_all_buckets():
    # opus: input 15, output 75, cache_read 1.5 (10% of 15), cache_write 18.75 (1.25× @ 5m)
    assert cost.estimate_cost(_1M, "claude-opus-4-8", "5m") == pytest.approx(15 + 75 + 1.5 + 18.75)


def test_cache_write_doubles_at_1h():
    b = {"cache_write": 1_000_000}
    assert cost.estimate_cost(b, "opus", "1h") == pytest.approx(30.0)  # 15 × 2.0
    assert cost.estimate_cost(b, "opus", "5m") == pytest.approx(18.75)  # 15 × 1.25


def test_haiku_and_bucket_costs():
    costs = cost.bucket_costs({"input": 1_000_000}, "claude-haiku-4-5", "5m")  # haiku input 1.0
    assert costs["input"] == pytest.approx(1.0) and costs["output"] == 0


def test_unknown_model_uses_fallback():
    assert cost.estimate_cost({"input": 1_000_000}, "gpt-5.5", "5m") == pytest.approx(
        3.0
    )  # fb rate


def test_cache_savings_formula():
    # 1M cache reads: full input (opus 15) = $15; cached read = $1.5; no writes -> save $13.5
    assert cost.cache_savings({"cache_read": 1_000_000}, "opus", "5m") == pytest.approx(13.5)


def test_turn_cost_prefers_actual_over_estimate():
    usage = {"model": "claude-opus-4-8", "input": 1_000_000, "cost_usd": 0.42}
    assert cost.turn_cost(usage) == (0.42, False)


def test_turn_cost_estimates_when_no_actual():
    usage = {"model": "haiku", "input": 1_000_000, "output": 0, "cost_usd": None}
    c, is_est = cost.turn_cost(usage)
    assert c == pytest.approx(1.0) and is_est is True


def test_turn_cost_none_usage_is_zero_estimate():
    assert cost.turn_cost(None) == (0.0, True)


def test_session_cost_prefers_actual():
    assert cost.session_cost({"input": 1_000_000}, "opus", actual_usd=9.99) == (9.99, False)
    c, is_est = cost.session_cost({"input": 1_000_000}, "opus")
    assert c == pytest.approx(15.0) and is_est is True
