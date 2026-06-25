"""
Pure render formatting for the TUI status bar / needs panel (textual-free).

These cover the formatting logic the widgets render; the widgets themselves are
exercised by the pilots in test_tui_app.py. No model, no paid calls.
"""

from __future__ import annotations

from tui.render import fmt_tok, short_model, status_line1, status_line2


def _snap(**over):
    snap = {
        "status": "idle",
        "model": "claude-opus-4-8",
        "branch": None,
        "needs": {"connection": 0.5, "novelty": 0.9},
        "thresholds": {"connection": 0.8, "novelty": 0.85},
        "hottest": ["novelty", 0.9],
        "cooldowns": {},
        "stats": {
            "turns": 0,
            "tokens_total": 0,
            "tokens_by_branch": {},
            "last_tokens": 0,
            "last_latency": 0.0,
            "avg_latency": 0.0,
        },
    }
    snap.update(over)
    return snap


def test_short_model_drops_vendor_prefix():
    assert short_model("claude-opus-4-8") == "opus-4-8"
    assert short_model("") == "—"


def test_fmt_tok_compacts_thousands():
    assert fmt_tok(42) == "42"
    assert fmt_tok(4321) == "4.3k"
    assert fmt_tok(181800) == "181.8k"


def test_status_line1_basic():
    line = status_line1(_snap(status="idle", branch=None))
    assert line.startswith("status: idle")
    assert "opus-4-8" in line
    assert "hottest: novelty 0.90" in line


def test_status_line1_with_branch():
    line = status_line1(_snap(status="responding", branch="chat"))
    assert "responding" in line and " chat " in line


def test_status_line1_no_hottest():
    line = status_line1(_snap(hottest=["", 0.0]))
    assert "hottest: —" in line


def test_status_line2_empty_session():
    assert status_line2(_snap()) == "stats: (no turns yet)"


def test_status_line2_with_turns():
    line = status_line2(
        _snap(
            stats={
                "turns": 3,
                "tokens_total": 181800,
                "tokens_by_branch": {"chat": 1000, "think": 180800},
                "last_tokens": 4321,
                "last_latency": 12.2,
                "avg_latency": 13.8,
            }
        )
    )
    assert "last 4.3k tok" in line
    assert "12.2s" in line
    assert "3 turns" in line
    assert "181.8k tok" in line
    assert "avg 13.8s" in line
