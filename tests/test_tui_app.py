"""
KILN-011: застосунок TUI + паралельний цикл.

Два рівні:
  1) Wiring/паралельність (без textual) — двіжок у фоновому потоці, керований
     містком: ввід з inbox -> відповідь у outbox; `/quit` чисто спиняє цикл.
  2) Pilot (під importorskip textual) — застосунок монтується, submit вводу йде
     в inbox містка (UI->місток зв'язано).

Усе на MockBrain — нуль платних викликів.
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
    """Двіжок крутиться у фоновому потоці; ввід з містка дає відповідь; /quit спиняє."""
    import kiln.engine as eng

    _isolate_persistence(monkeypatch, eng, tmp_path)

    bridge = Bridge()
    bridge.submit("привіт")  # хід
    bridge.submit("/quit")  # чистий стоп (engine finally збереже сесію)

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

    assert not thread.is_alive()  # /quit спинив нескінченний цикл (ticks=None)
    kinds = [e["kind"] for e in bridge.drain_output()]
    assert "agent" in kinds  # "привіт" отримав відповідь
    assert "notice" in kinds  # "/quit" -> "[exit] вихід за командою"


def test_app_input_submits_to_bridge(tmp_path):
    """UI->місток: submit рядка кладе його в inbox (двіжок не стартуємо)."""
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import Input, RichLog

    from tui.app import KilnApp

    async def scenario():
        bridge = Bridge()
        app = KilnApp(bridge=bridge, live=False, start_engine=False)
        async with app.run_test() as pilot:
            assert app.query(Input)  # рядок вводу є
            assert app.query(RichLog)  # лог є
            app.query_one(Input).value = "привіт"
            await pilot.press("enter")
            await pilot.pause()
            # після submit вхід очистився, а рядок пішов у inbox містка
            assert app.query_one(Input).value == ""
            assert bridge.poll_input() == "привіт"

    asyncio.run(scenario())
