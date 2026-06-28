"""
Output seam contract (ARCHITECTURE §Components, §Contracts).

The core (engine.run) writes replies only through the Output port — never via print directly.
Here: the default ConsoleOutput prints the reply + a technical line, while a fake sink
captures the events of a full turn (via MockBrain, without writing to the real state/).
"""

from __future__ import annotations

from kiln.brain import MockBrain
from kiln.output import ConsoleOutput, Output
from kiln.store import empty_store

USAGE_KEYS = {"model", "input", "output", "cache_read", "cache_write", "total", "cost_usd"}


class CapturingOutput:
    """Fake sink: records events instead of printing."""

    def __init__(self):
        self.events: list = []

    def user(self, text: str) -> None:
        self.events.append(("user", text))

    def agent(self, text, *, is_self=False, lead=False, model=None) -> None:
        self.events.append(("agent", text))

    def usage(self, usage, latency=None) -> None:
        self.events.append(("usage", usage))

    def notice(self, text: str) -> None:
        self.events.append(("notice", text))

    def status(self, snapshot: dict) -> None:
        self.events.append(("status", snapshot))


def test_sinks_satisfy_protocol():
    assert isinstance(ConsoleOutput(), Output)
    assert isinstance(CapturingOutput(), Output)


def test_console_output_prints_reply_and_tech(capsys):
    out = ConsoleOutput()
    out.user("привіт")
    out.agent("відповідь")
    out.usage({"model": "claude-haiku-4-5", "input": 8, "output": 12, "total": 20})

    printed = capsys.readouterr().out
    assert "you: привіт" in printed
    assert "Agnika: відповідь" in printed
    assert "haiku" in printed and "8→12" in printed


def test_run_routes_turn_through_output_port(monkeypatch, tmp_path):
    """A full turn through run() sends the reply + usage to the port; zero disk writes."""
    import kiln.engine as eng

    # Isolate persistence — no writes to the real state/ or history/.
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

    cap = CapturingOutput()
    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=MockBrain(),
        output=cap,
    )

    kinds = [e[0] for e in cap.events]
    assert "user" in kinds and "agent" in kinds and "usage" in kinds

    agent_texts = [e[1] for e in cap.events if e[0] == "agent"]
    assert any("dry-run chat" in t for t in agent_texts)  # reply from MockBrain

    usages = [e[1] for e in cap.events if e[0] == "usage"]
    assert any(isinstance(u, dict) and set(u) == USAGE_KEYS for u in usages)
