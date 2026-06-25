"""
kiln — session statistics: token/turn/latency aggregates for the status bar.

A small accumulator the tick loop updates after each turn. It feeds the per-tick
`status` event (see engine._status_snapshot); the TUI status bar renders it. No
cost/`$` here — that stays in v0.5.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionStats:
    """Running session totals: turns, tokens (total + by branch), and latency."""

    turns: int = 0
    tokens_total: int = 0
    tokens_by_branch: dict[str, int] = field(default_factory=dict)
    last_tokens: int = 0
    last_latency: float = 0.0
    _latencies: list[float] = field(default_factory=list)

    def record(self, branch: str, usage: dict | None, latency: float) -> None:
        """Fold one turn into the totals (branch = the turn class: chat/think/tools)."""
        tok = usage["total"] if usage else 0
        self.turns += 1
        self.tokens_total += tok
        self.tokens_by_branch[branch] = self.tokens_by_branch.get(branch, 0) + tok
        self.last_tokens = tok
        self.last_latency = latency
        self._latencies.append(latency)

    @property
    def avg_latency(self) -> float:
        return sum(self._latencies) / len(self._latencies) if self._latencies else 0.0

    def snapshot(self) -> dict:
        """Plain dict for the status event (rounded latencies)."""
        return {
            "turns": self.turns,
            "tokens_total": self.tokens_total,
            "tokens_by_branch": dict(self.tokens_by_branch),
            "last_tokens": self.last_tokens,
            "last_latency": round(self.last_latency, 2),
            "avg_latency": round(self.avg_latency, 2),
        }
