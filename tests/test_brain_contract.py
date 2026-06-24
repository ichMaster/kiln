"""
Контракт seam'а «мозок» (ARCHITECTURE §Contracts, §Two brains and cost routing).

Кожна гілка повертає (text, usage), де usage = {model, input, output, total}.
respond() кличе модель ЛИШЕ через brain — тут перевіряємо це на моку, без жодних
мережевих чи субпроцесних викликів (нуль платних викликів).
"""

from __future__ import annotations

from kiln.brain import Brain, LiveBrain, MockBrain
from kiln.config import CHAT_MODEL, DEEP_MODEL
from kiln.engine import State, respond
from kiln.usage import usage_record

USAGE_KEYS = {"model", "input", "output", "total"}


# --- usage_record: нормалізація токенів (її ділять обидва адаптери) ----------


def test_usage_record_from_cli_dict():
    rec = usage_record(DEEP_MODEL, {"input_tokens": 3, "output_tokens": 4})
    assert rec == {"model": DEEP_MODEL, "input": 3, "output": 4, "total": 7}


def test_usage_record_from_sdk_object():
    class _U:  # імітація msg.usage із SDK
        input_tokens = 10
        output_tokens = 5

    rec = usage_record(CHAT_MODEL, _U())
    assert rec == {"model": CHAT_MODEL, "input": 10, "output": 5, "total": 15}


def test_usage_record_none_is_zeroed():
    assert usage_record(CHAT_MODEL, None) == {
        "model": CHAT_MODEL,
        "input": 0,
        "output": 0,
        "total": 0,
    }


# --- Brain seam: форма (text, usage) ----------------------------------------


def test_mockbrain_chat_contract():
    text, usage = MockBrain().chat([{"role": "user", "text": "привіт"}], "sys")
    assert isinstance(text, str) and text
    assert set(usage) == USAGE_KEYS
    assert usage["total"] == usage["input"] + usage["output"]


def test_mockbrain_deep_contract():
    text, usage = MockBrain().deep(
        "питання", [{"role": "user", "text": "питання"}], "sys", with_tools=False
    )
    assert isinstance(text, str) and text
    assert set(usage) == USAGE_KEYS


def test_both_brains_satisfy_protocol():
    # runtime_checkable Protocol: структурна відповідність seam'у.
    assert isinstance(MockBrain(), Brain)
    assert isinstance(LiveBrain(), Brain)


# --- respond() через мок: маршрутизація + 0 IO ------------------------------


class RecordingBrain:
    """Мок, що запам'ятовує, яку гілку покликали; жодного IO."""

    def __init__(self):
        self.calls: list = []

    def chat(self, history, system):
        self.calls.append("chat")
        return "CHAT-REPLY", usage_record(CHAT_MODEL, {"input_tokens": 1, "output_tokens": 1})

    def deep(self, prompt, history, system, with_tools):
        self.calls.append(("deep", with_tools))
        return "DEEP-REPLY", usage_record(DEEP_MODEL, {"input_tokens": 2, "output_tokens": 2})


def test_respond_chat_routes_to_brain_chat():
    brain = RecordingBrain()
    out = respond("привіт", State(needs={"intensity": 0.0, "connection": 0.0}), [], "sys", brain)
    assert brain.calls == ["chat"]
    assert out["class"] == "chat"
    assert out["reply"] == "CHAT-REPLY"
    assert set(out["usage"]) == USAGE_KEYS


def test_respond_think_routes_to_brain_deep():
    brain = RecordingBrain()
    # 'поясни'/'чому' — THINK_HINTS -> гілка deep, без тулів.
    out = respond(
        "поясни, чому так", State(needs={"intensity": 0.0, "connection": 0.0}), [], "sys", brain
    )
    assert brain.calls == [("deep", False)]
    assert out["reply"] == "DEEP-REPLY"


def test_respond_force_deep_bypasses_classify():
    brain = RecordingBrain()
    out = respond("будь-що", State(needs={}), [], "sys", brain, force="deep")
    assert brain.calls == [("deep", False)]
    assert out["class"] == "deep"


def test_respond_with_mock_never_touches_subprocess(monkeypatch):
    """З мок-мозком respond не торкається subprocess (нуль платних викликів)."""
    import kiln.brain as brainmod

    def _boom(*a, **k):
        raise AssertionError("subprocess.run не має викликатися з MockBrain")

    monkeypatch.setattr(brainmod.subprocess, "run", _boom)
    respond("поясни, чому так", State(needs={}), [], "sys", MockBrain(), force="deep")
