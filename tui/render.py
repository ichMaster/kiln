"""
kiln.tui.render — pure formatting for the status bar / needs panel (no textual).

Kept textual-free so the formatting is unit-tested directly; the widgets just render
these strings. Input is the per-tick status snapshot (engine._status_snapshot):
{status, model, branch, needs, thresholds, hottest, cooldowns, stats}.
"""

from __future__ import annotations


def short_model(model: str) -> str:
    """`claude-opus-4-8` -> `opus-4-8` (drops the vendor prefix)."""
    return model.removeprefix("claude-") if model else "—"


def fmt_tok(n: int) -> str:
    """Compact token count: 4321 -> `4.3k`, 42 -> `42`."""
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def status_line1(snap: dict) -> str:
    """Top status line: status · model · branch · hottest need."""
    status = snap.get("status", "idle")
    model = short_model(snap.get("model", ""))
    branch = snap.get("branch") or "—"
    hottest = snap.get("hottest") or ["", 0.0]
    need, level = hottest[0], hottest[1]
    hot = f"{need} {level:.2f}" if need else "—"
    return f"status: {status} · {model} · {branch} · hottest: {hot}"


def status_line2(snap: dict) -> str:
    """Stats line: last-turn tokens + latency, session totals, average latency."""
    s = snap.get("stats", {})
    if not s.get("turns"):
        return "stats: (no turns yet)"
    return (
        f"stats: last {fmt_tok(s['last_tokens'])} tok · {s['last_latency']}s · "
        f"{s['turns']} turns · {fmt_tok(s['tokens_total'])} tok · avg {s['avg_latency']}s"
    )
