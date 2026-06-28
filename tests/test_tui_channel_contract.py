"""
TuiChannel contract (input channel over the bridge, same poll() contract as StdinChannel).

poll() returns a typed line once, then None; FIFO. Integration: a line from the inbox
reaches the engine through run() and yields a reply (MockBrain, zero paid calls).
"""

from __future__ import annotations

from kiln.brain import MockBrain
from kiln.store import empty_store
from tui.bridge import Bridge
from tui.channel import TuiChannel
from tui.output import TuiOutput


def test_poll_returns_submitted_line_then_none():
    b = Bridge()
    ch = TuiChannel(b)
    b.submit("привіт")
    assert ch.poll() == "привіт"
    assert ch.poll() is None  # exhausted


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
    monkeypatch.setattr(eng, "load_store", lambda *a, **k: empty_store())
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "")
    monkeypatch.setattr(eng, "extract_facts", lambda *a, **k: [])  # no real claude -p in tests
    monkeypatch.setattr(eng, "digest_facts", lambda *a, **k: "")  # start-time facts digest off
    monkeypatch.setattr(eng, "save_store", lambda *a, **k: None)
    monkeypatch.setattr(eng, "append_session", lambda *a, **k: None)  # no real usage-ledger write

    b = Bridge()
    b.submit("привіт")  # input queued before the loop starts
    eng.run(
        ticks=3,
        live=False,
        channel=TuiChannel(b),
        brain=MockBrain(),
        output=TuiOutput(b),
    )

    kinds = [e["kind"] for e in b.drain_output()]
    assert "agent" in kinds  # input reached the engine and produced a reply
