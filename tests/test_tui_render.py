"""
Pure render formatting for the TUI status bar / needs panel (textual-free).

These cover the formatting logic the widgets render; the widgets themselves are
exercised by the pilots in test_tui_app.py. No model, no paid calls.
"""

from __future__ import annotations

from tui.render import (
    agent_label,
    fmt_tok,
    need_color,
    needs_panel_lines,
    short_model,
    status_line1,
    status_line2,
)


def _snap(**over):
    snap = {
        "status": "idle",
        "model": "claude-opus-4-8",
        "branch": None,
        "needs": {"connection": 0.5, "novelty": 0.9},
        "thresholds": {"connection": 0.8, "novelty": 0.85},
        "actions": {"connection": "chat", "novelty": "deep"},
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


# --- needs / thresholds panel ----------------------------------------------


def test_needs_panel_one_row_per_need():
    rows = needs_panel_lines(_snap())
    assert len(rows) == 2  # connection + novelty
    assert any(r.lstrip().startswith("connection") for r in rows)
    assert any("novelty" in r for r in rows)


def test_needs_panel_marks_hottest_and_shows_threshold():
    rows = needs_panel_lines(_snap(hottest=["novelty", 0.9]))
    novelty = next(r for r in rows if "novelty" in r)
    assert novelty.startswith("*")  # hottest marked
    assert "0.90/0.85" in novelty  # level/threshold


def test_needs_panel_flags_over_threshold_and_cooldown():
    rows = needs_panel_lines(_snap(needs={"novelty": 0.9}, cooldowns={"novelty": 3}))
    novelty = next(r for r in rows if "novelty" in r)
    assert " !" in novelty  # 0.90 >= 0.85 threshold
    assert "cd3" in novelty  # active cooldown


def test_needs_panel_below_threshold_no_flag():
    rows = needs_panel_lines(_snap(needs={"connection": 0.5}, hottest=["", 0.0]))
    connection = next(r for r in rows if "connection" in r)
    assert " !" not in connection
    assert "0.50/0.80" in connection


def test_need_color_bands():
    assert need_color(0.4, 0.8) == "green"  # ratio 0.5 — calm
    assert need_color(0.7, 0.8) == "yellow"  # ratio 0.875 — approaching
    assert need_color(0.8, 0.8) == "red"  # ratio 1.0 — at threshold
    assert need_color(0.9, 0.8) == "red"  # over threshold
    assert need_color(0.5, None) == "white"  # no threshold
    assert need_color(0.5, 0) == "white"  # zero threshold


def test_needs_panel_colors_bar_by_closeness():
    over = needs_panel_lines(
        _snap(needs={"intensity": 1.0}, thresholds={"intensity": 0.75}, hottest=["intensity", 1.0])
    )
    assert "[red]" in over[0]
    calm = needs_panel_lines(
        _snap(needs={"rest": 0.1}, thresholds={"rest": 0.9}, hottest=["", 0.0])
    )
    assert "[green]" in calm[0]


def test_needs_panel_shows_action():
    rows = needs_panel_lines(_snap())
    assert "→ deep" in next(r for r in rows if "novelty" in r)
    assert "→ chat" in next(r for r in rows if "connection" in r)


# --- chat-log polish: labels + per-turn tech line --------------------------


def test_agent_label_marks_self():
    assert agent_label(False) == "Agnika"
    assert agent_label(True) == "Agnika (self)"


def test_agent_label_includes_model():
    assert agent_label(False, "opus") == "Agnika [opus]"
    assert agent_label(True, "haiku") == "Agnika (self) [haiku]"
    assert agent_label(False, None) == "Agnika"  # no model -> plain
