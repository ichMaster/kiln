"""
Контракт seam'а виводу (ARCHITECTURE §Components, §Contracts).

Ядро (engine.run) пише репліки лише через порт Output — ніколи у print напряму.
Тут: типовий ConsoleOutput друкує репліку + технічний рядок, а підмінний сінк
ловить події повного ходу (через MockBrain, без запису в реальний state/).
"""

from __future__ import annotations

from kiln.brain import MockBrain
from kiln.output import ConsoleOutput, Output

USAGE_KEYS = {"model", "input", "output", "total"}


class CapturingOutput:
    """Фейковий сінк: записує події замість друку."""

    def __init__(self):
        self.events: list = []

    def user(self, text: str) -> None:
        self.events.append(("user", text))

    def agent(self, text: str, *, is_self: bool = False, lead: bool = False) -> None:
        self.events.append(("agent", text))

    def usage(self, usage) -> None:
        self.events.append(("usage", usage))

    def notice(self, text: str) -> None:
        self.events.append(("notice", text))


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
    """Повний хід через run() надсилає репліку + usage у порт; нуль запису на диск."""
    import kiln.engine as eng

    # Ізолюємо персистентність — жодного запису в реальний state/ чи history/.
    monkeypatch.setattr(eng, "STATE_DIR", tmp_path)
    monkeypatch.setattr(
        eng, "load_state",
        lambda *a, **k: eng.State(needs={"connection": 0.0, "rest": 0.0, "novelty": 0.0, "intensity": 0.0}),
    )
    monkeypatch.setattr(eng, "save_state", lambda *a, **k: None)
    monkeypatch.setattr(eng, "save_session", lambda *a, **k: tmp_path / "s.json")
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "")
    monkeypatch.setattr(eng, "save_summary", lambda *a, **k: None)

    cap = CapturingOutput()
    eng.run(ticks=2, live=False,
            channel=eng.ScriptedChannel({1: "привіт"}),
            brain=MockBrain(), output=cap)

    kinds = [e[0] for e in cap.events]
    assert "user" in kinds and "agent" in kinds and "usage" in kinds

    agent_texts = [e[1] for e in cap.events if e[0] == "agent"]
    assert any("dry-run chat" in t for t in agent_texts)        # репліка від MockBrain

    usages = [e[1] for e in cap.events if e[0] == "usage"]
    assert any(isinstance(u, dict) and set(u) == USAGE_KEYS for u in usages)
