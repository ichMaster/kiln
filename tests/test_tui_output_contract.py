"""
TuiOutput contract (the Output seam implemented over the bridge).

agent/usage/notice -> events into the outbox; user() is an echo-free no-op. A full turn
through run() with MockBrain puts the reply + usage into the outbox and does NOT echo input.
Zero paid calls.
"""

from __future__ import annotations

from kiln.brain import MockBrain
from kiln.output import Output
from kiln.store import empty_store
from tui.bridge import Bridge
from tui.output import TuiOutput

USAGE_KEYS = {"model", "input", "output", "total"}


def test_tuioutput_satisfies_output_protocol():
    assert isinstance(TuiOutput(Bridge()), Output)


def test_tuioutput_emits_agent_and_usage():
    b = Bridge()
    out = TuiOutput(b)
    out.agent("привіт", is_self=True)
    out.usage({"model": "m", "input": 1, "output": 2, "total": 3})
    events = b.drain_output()
    assert [e["kind"] for e in events] == ["agent", "usage"]
    assert events[0]["text"] == "привіт" and events[0]["is_self"] is True
    assert events[1]["usage"]["total"] == 3


def test_tuioutput_user_is_echo_free_noop():
    b = Bridge()
    TuiOutput(b).user("моє повідомлення")
    assert b.drain_output() == []  # input is not echoed into the render


def test_run_turn_emits_to_outbox(monkeypatch, tmp_path):
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
    monkeypatch.setattr(eng, "save_store", lambda *a, **k: None)

    b = Bridge()
    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=MockBrain(),
        output=TuiOutput(b),
    )

    events = b.drain_output()
    kinds = [e["kind"] for e in events]
    assert "agent" in kinds and "usage" in kinds
    assert "user" not in kinds  # echo-free: the engine doesn't send input to the render

    agent_ev = next(e for e in events if e["kind"] == "agent")
    assert "dry-run chat" in agent_ev["text"]  # reply from MockBrain
    usage_ev = next(e for e in events if e["kind"] == "usage")
    assert set(usage_ev["usage"]) == USAGE_KEYS
