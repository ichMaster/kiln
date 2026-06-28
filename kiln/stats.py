"""
kiln — session statistics: token/turn/latency/cost aggregates for the status bar + the ledger.

A small accumulator the tick loop updates after each turn. It feeds the per-tick `status` event
(see engine._status_snapshot; the TUI status bar renders it) and, at session close, the usage
ledger (v0.7): the four token buckets, the models used, and the running cost (`$`) — actual where
the CLI reports it, else estimated from the price table (`cost.turn_cost`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cost import turn_cost


@dataclass
class SessionStats:
    """Running session totals: turns, tokens (total + by branch), and latency."""

    turns: int = 0
    tokens_total: int = 0
    tokens_by_branch: dict[str, int] = field(default_factory=dict)
    last_tokens: int = 0
    last_latency: float = 0.0
    # the four token buckets (v0.7): input / output / cache read / cache write
    input_total: int = 0
    output_total: int = 0
    cache_read_total: int = 0
    cache_write_total: int = 0
    # v0.7 cost: models used (first-seen order) + running $ (actual where known, else estimated)
    models: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    cost_estimated: bool = False
    # per-branch call COUNTS (chat = SDK; think/tools/tool = `claude -p`) and per-model breakdown
    calls_by_branch: dict[str, int] = field(default_factory=dict)
    by_model: dict[str, dict] = field(default_factory=dict)
    _latencies: list[float] = field(default_factory=list)

    def record(self, branch: str, usage: dict | None, latency: float) -> None:
        """Fold one turn into the totals (branch = the turn class: chat/think/tools/tool)."""
        tok = usage["total"] if usage else 0
        self.turns += 1
        self.tokens_total += tok
        self.calls_by_branch[branch] = self.calls_by_branch.get(branch, 0) + 1
        if usage:
            self.input_total += usage.get("input", 0)
            self.output_total += usage.get("output", 0)
            self.cache_read_total += usage.get("cache_read", 0)
            self.cache_write_total += usage.get("cache_write", 0)
            model = usage.get("model") or "?"
            if model not in self.models:
                self.models.append(model)
            cost, is_estimate = turn_cost(usage)  # actual cost_usd if present, else estimate
            self.cost_usd += cost
            if is_estimate and cost:
                self.cost_estimated = True
            m = self.by_model.setdefault(
                model,
                {
                    "calls": 0,
                    "input": 0,
                    "output": 0,
                    "cache_read": 0,
                    "cache_write": 0,
                    "cost_usd": 0.0,
                },
            )
            m["calls"] += 1
            m["input"] += usage.get("input", 0)
            m["output"] += usage.get("output", 0)
            m["cache_read"] += usage.get("cache_read", 0)
            m["cache_write"] += usage.get("cache_write", 0)
            m["cost_usd"] += cost
        self.tokens_by_branch[branch] = self.tokens_by_branch.get(branch, 0) + tok
        self.last_tokens = tok
        self.last_latency = latency
        self._latencies.append(latency)

    @property
    def avg_latency(self) -> float:
        return sum(self._latencies) / len(self._latencies) if self._latencies else 0.0

    @property
    def cli_calls(self) -> int:
        """Turns that shelled out to `claude -p` (every branch except the SDK `chat`)."""
        return sum(c for b, c in self.calls_by_branch.items() if b != "chat")

    def snapshot(self) -> dict:
        """Plain dict for the status event (rounded latencies)."""
        return {
            "turns": self.turns,
            "tokens_total": self.tokens_total,
            "tokens_by_branch": dict(self.tokens_by_branch),
            "last_tokens": self.last_tokens,
            "last_latency": round(self.last_latency, 2),
            "avg_latency": round(self.avg_latency, 2),
            "input_total": self.input_total,
            "output_total": self.output_total,
            "cache_read_total": self.cache_read_total,
            "cache_write_total": self.cache_write_total,
            "cost_usd": round(self.cost_usd, 6),
            "cost_estimated": self.cost_estimated,
            "cli_calls": self.cli_calls,
        }
