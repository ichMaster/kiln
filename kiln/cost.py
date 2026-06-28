"""
kiln — cost estimation (v0.7): a per-model price table + estimators over the four token buckets.

Pure functions, no I/O. Costs are **estimates** from Anthropic list prices (not a billing source
of truth) — except where the CLI reports an actual `total_cost_usd`, which `turn_cost` /
`session_cost` prefer. Cache pricing follows Lumi: cache read = 10% of the input rate; cache
write = 1.25× input @ 5m TTL, 2× @ 1h TTL.
"""

from __future__ import annotations

# Per-model list prices, USD per 1M tokens (input, output), matched by model-family substring.
# Cache rates derive from the input rate (below). Tune here as Anthropic prices change.
PRICES: dict[str, dict[str, float]] = {
    "opus": {"input": 15.0, "output": 75.0},
    "sonnet": {"input": 3.0, "output": 15.0},
    "haiku": {"input": 1.0, "output": 5.0},
}
_FALLBACK = {"input": 3.0, "output": 15.0}  # unknown model -> sonnet-ish

_CACHE_READ_FRACTION = 0.10  # cache read = 10% of the input rate
_CACHE_WRITE_MULT = {"5m": 1.25, "1h": 2.0}  # cache write multiplier on the input rate, by TTL

_BUCKETS = ("input", "output", "cache_read", "cache_write")


def _rates(model: str) -> dict[str, float]:
    """Price row for a model, matched by family substring (opus/sonnet/haiku); else fallback."""
    m = (model or "").lower()
    for key, row in PRICES.items():
        if key in m:
            return row
    return _FALLBACK


def _per_1m(model: str, ttl: str) -> dict[str, float]:
    """The /1M rate for each of the four buckets at a model's prices + cache TTL."""
    r = _rates(model)
    write_mult = _CACHE_WRITE_MULT.get(ttl, _CACHE_WRITE_MULT["5m"])
    return {
        "input": r["input"],
        "output": r["output"],
        "cache_read": r["input"] * _CACHE_READ_FRACTION,
        "cache_write": r["input"] * write_mult,
    }


def bucket_costs(buckets: dict, model: str, ttl: str = "5m") -> dict[str, float]:
    """Per-bucket `$` for {input, output, cache_read, cache_write} at the model's rates."""
    rate = _per_1m(model, ttl)
    return {b: buckets.get(b, 0) / 1_000_000 * rate[b] for b in _BUCKETS}


def estimate_cost(buckets: dict, model: str, ttl: str = "5m") -> float:
    """Total estimated `$` for the four buckets at the model's rates."""
    return sum(bucket_costs(buckets, model, ttl).values())


def cache_savings(buckets: dict, model: str, ttl: str = "5m") -> float:
    """Net `$` saved by caching: the cache reads at the *full* input rate minus what they actually
    cost (cached read) and the cache writes that produced them."""
    full = buckets.get("cache_read", 0) / 1_000_000 * _rates(model)["input"]
    costs = bucket_costs(buckets, model, ttl)
    return full - costs["cache_read"] - costs["cache_write"]


def turn_cost(usage: dict | None, ttl: str = "5m") -> tuple[float, bool]:
    """`(cost, is_estimate)` for one turn's usage record. Prefer its actual `cost_usd` (the CLI's
    `total_cost_usd`); else estimate from the four buckets at the model's rates."""
    if not usage:
        return 0.0, True
    if usage.get("cost_usd") is not None:
        return float(usage["cost_usd"]), False
    return estimate_cost(usage, usage.get("model", ""), ttl), True


def session_cost(buckets: dict, model: str, ttl: str = "5m", actual_usd: float | None = None):
    """`(cost, is_estimate)` for a session: prefer a known actual cost, else estimate buckets."""
    if actual_usd is not None:
        return float(actual_usd), False
    return estimate_cost(buckets, model, ttl), True
