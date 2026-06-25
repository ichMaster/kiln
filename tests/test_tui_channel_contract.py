"""
Контракт TuiChannel (канал вводу над містком, контракт poll() як у StdinChannel).

poll() віддає набраний рядок раз, далі None; FIFO. Інтеграція: рядок із inbox
доходить до двіжка через run() і дає відповідь (MockBrain, нуль платних викликів).
"""

from __future__ import annotations

from kiln.brain import MockBrain
from tui.bridge import Bridge
from tui.channel import TuiChannel
from tui.output import TuiOutput


def test_poll_returns_submitted_line_then_none():
    b = Bridge()
    ch = TuiChannel(b)
    b.submit("привіт")
    assert ch.poll() == "привіт"
    assert ch.poll() is None  # вичерпано


def test_poll_is_fifo():
    b = Bridge()
    ch = TuiChannel(b)
    b.submit("a")
    b.submit("b")
    assert [ch.poll(), ch.poll(), ch.poll()] == ["a", "b", None]


def test_tuichannel_drives_a_turn(monkeypatch, tmp_path):
    import kiln.engine as eng

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

    b = Bridge()
    b.submit("привіт")  # ввід у черзі ще до старту циклу
    eng.run(
        ticks=3,
        live=False,
        channel=TuiChannel(b),
        brain=MockBrain(),
        output=TuiOutput(b),
    )

    kinds = [e["kind"] for e in b.drain_output()]
    assert "agent" in kinds  # ввід дійшов до двіжка й дав відповідь
