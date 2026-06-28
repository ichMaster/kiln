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
from kiln.store import empty_store
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
    monkeypatch.setattr(eng, "load_store", lambda *a, **k: empty_store())
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "")
    monkeypatch.setattr(eng, "extract_facts", lambda *a, **k: [])  # no real claude -p in tests
    monkeypatch.setattr(eng, "digest_facts", lambda *a, **k: "")  # start-time facts digest off
    monkeypatch.setattr(eng, "save_store", lambda *a, **k: None)
    monkeypatch.setattr(eng, "append_session", lambda *a, **k: None)  # no real usage-ledger write
    monkeypatch.setattr(eng, "write_report", lambda *a, **k: None)  # no real usage-report write


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

    from textual.widgets import Header, RichLog

    from tui.app import ChatInput, KilnApp

    async def scenario():
        bridge = Bridge()
        app = KilnApp(bridge=bridge, live=False, start_engine=False)
        async with app.run_test() as pilot:
            assert app.query(Header)  # standard top bar present
            assert app.query(ChatInput)  # 3-line input box exists
            assert app.query(RichLog)  # log exists
            app.query_one(ChatInput).text = "привіт"
            await pilot.press("enter")
            await pilot.pause()
            # after submit the input cleared, and the line went into the bridge inbox
            assert app.query_one(ChatInput).text == ""
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
        "tick": 42,
        "needs": {"novelty": 0.9},
        "thresholds": {"novelty": 0.85},
        "actions": {"novelty": "deep"},
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
            status = str(app.query_one("#status", Static).render())
            stats = str(app.query_one("#stats", Static).render())
            assert "responding" in status and "opus-4-8" in status
            assert "1 turns" in stats  # stats line rendered
            # needs/thresholds panel: its own bordered box, title carries the tick counter
            needs = app.query_one("#needspanel", Static)
            assert needs.border_title == "Needs · tick 42"
            panel = str(needs.render())
            assert "нудьга" in panel and "0.90/0.85" in panel and " !" in panel
            assert "deep" in panel  # the NEED_TRIGGERS action shown

    asyncio.run(scenario())


def test_app_copy_and_clear_actions(tmp_path, monkeypatch):
    """Ctrl+Y copies the last reply, Ctrl+O copies the transcript, Ctrl+L clears."""
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import RichLog

    import tui.app as appmod
    from tui.app import KilnApp

    sys_copies: list[str] = []  # capture the OS-clipboard writes (no real pbcopy in the test)
    monkeypatch.setattr(appmod, "system_clipboard_copy", lambda t: sys_copies.append(t) or True)

    async def scenario():
        bridge = Bridge()
        app = KilnApp(bridge=bridge, live=False, start_engine=False)
        async with app.run_test() as pilot:
            bridge.emit({"kind": "agent", "text": "відповідь", "is_self": False, "lead": False})
            bridge.emit({"kind": "notice", "text": "[exit] saved"})
            app._drain()
            await pilot.pause()

            app.action_copy_reply()
            assert app.clipboard == "відповідь"  # OSC 52 path
            assert sys_copies[-1] == "відповідь"  # AND the OS clipboard tool (pbcopy/…)

            app.action_copy_all()
            assert "Agnika: відповідь" in app.clipboard and "[exit] saved" in app.clipboard
            assert "Agnika: відповідь" in sys_copies[-1]  # transcript reached the OS clipboard too

            app.action_clear_log()
            assert app._transcript == [] and app._last_reply == ""
            assert app.query_one(RichLog).lines == []

    asyncio.run(scenario())


def test_app_copy_keys_fire_while_input_focused(tmp_path, monkeypatch):
    """The Ctrl+Y / Ctrl+O / Ctrl+L bindings fire even though the TextArea input is focused —
    priority bindings beat the TextArea, which otherwise swallows ctrl+y (redo) and ctrl+l."""
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import RichLog

    import tui.app as appmod
    from tui.app import ChatInput, KilnApp

    sys_copies: list[str] = []
    monkeypatch.setattr(appmod, "system_clipboard_copy", lambda t: sys_copies.append(t) or True)

    async def scenario():
        bridge = Bridge()
        app = KilnApp(bridge=bridge, live=False, start_engine=False)
        async with app.run_test() as pilot:
            bridge.emit({"kind": "agent", "text": "відповідь", "is_self": False, "lead": False})
            app._drain()
            await pilot.pause()
            assert app.focused is app.query_one("#prompt", ChatInput)  # input has focus

            await pilot.press("ctrl+o")  # copy all — TextArea doesn't bind it, but verify it fires
            assert sys_copies and "Agnika: відповідь" in sys_copies[-1]
            await pilot.press("ctrl+y")  # copy reply — TextArea binds ctrl+y=redo; priority wins
            assert sys_copies[-1] == "відповідь"
            await pilot.press("ctrl+l")  # clear — TextArea binds ctrl+l; priority wins
            assert app._transcript == [] and app.query_one(RichLog).lines == []

    asyncio.run(scenario())
