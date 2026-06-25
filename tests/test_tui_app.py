"""
KILN-011: TUI application + concurrent loop.

Two levels:
  1) Wiring/concurrency (without textual) — the engine runs on a background thread,
     driven by the bridge: input from inbox -> reply into outbox; `/quit` cleanly stops the loop.
  2) Pilot (under importorskip textual) — the app mounts, submitting input goes
     into the bridge inbox (UI->bridge wired).

Everything on MockBrain — zero paid calls.
"""

from __future__ import annotations

import threading

import pytest

from kiln.brain import MockBrain
from tui.bridge import Bridge
from tui.channel import TuiChannel
from tui.output import TuiOutput


def _isolate_persistence(monkeypatch, eng, tmp_path):
    monkeypatch.setattr(eng, "STATE_DIR", tmp_path)
    monkeypatch.setattr(
        eng,
        "load_state",
        lambda *a, **k: eng.State(
            needs={"connection": 0.0, "rest": 0.0, "novelty": 0.0, "intensity": 0.0}
        ),
    )
    monkeypatch.setattr(eng, "save_state", lambda *a, **k: None)
    monkeypatch.setattr(eng, "save_session", lambda *a, **k: tmp_path / "s.json")
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "")
    monkeypatch.setattr(eng, "save_summary", lambda *a, **k: None)


def test_engine_runs_on_thread_driven_by_bridge(monkeypatch, tmp_path):
    """The engine runs on a background thread; bridge input yields a reply; /quit stops it."""
    import kiln.engine as eng

    _isolate_persistence(monkeypatch, eng, tmp_path)

    bridge = Bridge()
    bridge.submit("привіт")  # turn
    bridge.submit("/quit")  # clean stop (engine finally will save the session)

    thread = threading.Thread(
        target=eng.run,
        kwargs=dict(
            ticks=None,
            live=False,
            channel=TuiChannel(bridge),
            brain=MockBrain(),
            output=TuiOutput(bridge),
        ),
        daemon=True,
    )
    thread.start()
    thread.join(timeout=5.0)

    assert not thread.is_alive()  # /quit stopped the infinite loop (ticks=None)
    kinds = [e["kind"] for e in bridge.drain_output()]
    assert "agent" in kinds  # "привіт" got a reply
    assert "notice" in kinds  # "/quit" -> "[exit] exit by command"


def test_app_input_submits_to_bridge(tmp_path):
    """UI->bridge: submitting a line puts it into the inbox (we don't start the engine)."""
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import Input, RichLog

    from tui.app import KilnApp

    async def scenario():
        bridge = Bridge()
        app = KilnApp(bridge=bridge, live=False, start_engine=False)
        async with app.run_test() as pilot:
            assert app.query(Input)  # input line exists
            assert app.query(RichLog)  # log exists
            app.query_one(Input).value = "привіт"
            await pilot.press("enter")
            await pilot.pause()
            # after submit the input cleared, and the line went into the bridge inbox
            assert app.query_one(Input).value == ""
            assert bridge.poll_input() == "привіт"

    asyncio.run(scenario())


def test_app_status_bar_updates_from_status_event(tmp_path):
    """A status event on the outbox updates the top status-bar widget."""
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import Static

    from tui.app import KilnApp

    snap = {
        "status": "responding",
        "model": "claude-opus-4-8",
        "branch": "think",
        "needs": {"novelty": 0.9},
        "thresholds": {"novelty": 0.85},
        "hottest": ["novelty", 0.9],
        "cooldowns": {},
        "stats": {
            "turns": 1,
            "tokens_total": 50,
            "tokens_by_branch": {"think": 50},
            "last_tokens": 50,
            "last_latency": 1.2,
            "avg_latency": 1.2,
        },
    }

    async def scenario():
        bridge = Bridge()
        app = KilnApp(bridge=bridge, live=False, start_engine=False)
        async with app.run_test() as pilot:
            bridge.emit({"kind": "status", "snapshot": snap})
            app._drain()  # deterministic: drain now instead of waiting for the timer
            await pilot.pause()
            bar = str(app.query_one("#statusbar", Static).render())
            assert "responding" in bar and "opus-4-8" in bar
            assert "1 turns" in bar  # stats line rendered
            # needs/thresholds panel rendered the over-threshold need
            panel = str(app.query_one("#needspanel", Static).render())
            assert "novelty" in panel and "0.90/0.85" in panel and " !" in panel

    asyncio.run(scenario())
