"""
Status-event contract (KILN-012): per-tick status snapshot + session stats.

The engine emits a `status` snapshot every tick (needs + thresholds + cooldowns +
session stats) through the Output seam. ConsoleOutput.status is a no-op; TuiOutput
emits it to the bus. All against a mock brain — zero paid calls.
"""

from __future__ import annotations

from kiln.brain import MockBrain
from kiln.config import CHAT_MODEL, DEEP_MODEL
from kiln.engine import State, TriggerBook, _status_snapshot
from kiln.output import ConsoleOutput
from kiln.stats import SessionStats
from kiln.usage import usage_record

SNAPSHOT_KEYS = {
    "status",
    "model",
    "branch",
    "tick",
    "needs",
    "thresholds",
    "actions",
    "hottest",
    "cooldowns",
    "stats",
}
STATS_KEYS = {
    "turns",
    "tokens_total",
    "tokens_by_branch",
    "last_tokens",
    "last_latency",
    "avg_latency",
}


# --- SessionStats ----------------------------------------------------------


def test_session_stats_accumulates_by_branch():
    s = SessionStats()
    s.record("chat", {"model": "m", "input": 1, "output": 2, "total": 3}, 0.5)
    s.record("think", {"model": "m", "input": 4, "output": 6, "total": 10}, 1.5)
    assert s.turns == 2
    assert s.tokens_total == 13
    assert s.tokens_by_branch == {"chat": 3, "think": 10}
    assert s.last_tokens == 10
    assert s.avg_latency == 1.0


def test_session_stats_none_usage_is_zero_tokens():
    s = SessionStats()
    s.record("chat", None, 0.2)
    assert s.turns == 1 and s.tokens_total == 0


def test_session_stats_snapshot_shape():
    s = SessionStats()
    s.record("chat", {"model": "m", "input": 2, "output": 3, "total": 5}, 0.25)
    assert set(s.snapshot()) == STATS_KEYS


# --- status snapshot -------------------------------------------------------


def test_status_snapshot_shape():
    state = State(needs={"connection": 0.5, "novelty": 0.9})
    tg = TriggerBook()
    tg.cooldown = {"novelty": 3, "rest": 0}  # only >0 should surface
    snap = _status_snapshot("idle", state, tg, SessionStats(), branch=None, tick=7)
    assert set(snap) == SNAPSHOT_KEYS
    assert set(snap["stats"]) == STATS_KEYS
    assert snap["status"] == "idle"
    assert snap["tick"] == 7
    assert snap["needs"] == {"connection": 0.5, "novelty": 0.9}
    assert snap["thresholds"]  # populated from NEED_TRIGGERS
    assert snap["actions"]["novelty"] == "tool"  # NEED_TRIGGERS action for the need
    assert snap["cooldowns"] == {"novelty": 3}  # rest (0) filtered out
    assert snap["hottest"][0] == "novelty"


def test_status_snapshot_model_follows_branch():
    state, tg, stats = State(needs={}), TriggerBook(), SessionStats()
    assert _status_snapshot("responding", state, tg, stats, "chat", 0)["model"] == CHAT_MODEL
    assert _status_snapshot("responding", state, tg, stats, "think", 0)["model"] == DEEP_MODEL
    assert (
        _status_snapshot("idle", state, tg, stats, None, 0)["model"] == DEEP_MODEL
    )  # headline default


# --- ConsoleOutput.status is a no-op ---------------------------------------


def test_console_status_is_noop(capsys):
    ConsoleOutput().status({"status": "idle"})
    assert capsys.readouterr().out == ""


# --- run() emits a status event each tick ----------------------------------


class StatusRecorder:
    """Output double that records status snapshots (and replies)."""

    def __init__(self):
        self.statuses: list[dict] = []
        self.replies: list[str] = []

    def user(self, text): ...
    def agent(self, text, *, is_self=False, lead=False, model=None):
        self.replies.append(text)

    def usage(self, usage, latency=None): ...
    def notice(self, text): ...

    def status(self, snapshot):
        self.statuses.append(snapshot)


def _isolate(monkeypatch, eng, tmp_path):
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


def test_run_emits_status_every_tick(monkeypatch, tmp_path):
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    rec = StatusRecorder()
    eng.run(ticks=3, live=False, channel=eng.ScriptedChannel({}), brain=MockBrain(), output=rec)
    assert len(rec.statuses) == 3  # one per idle tick
    assert all(set(s) == SNAPSHOT_KEYS for s in rec.statuses)
    assert all(s["status"] == "idle" for s in rec.statuses)
    # real elapsed ticks (dry-run: 1 per tick, catch-up = 1)
    assert [s["tick"] for s in rec.statuses] == [1, 2, 3]


def test_run_tick_counts_real_elapsed_including_blocking(monkeypatch, tmp_path):
    """A long blocking call advances the tick by the real elapsed ticks, not just by 1."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(eng, "TICK_SECONDS", 0.5)
    # Controlled clock: gaps = elapsed; the middle 8.0s gap simulates a long deep call.
    times = iter([0.0, 0.5, 8.5, 9.0])  # last_tick + `now` for ticks 0/1/2
    monkeypatch.setattr(eng.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(eng.time, "sleep", lambda *_: None)
    rec = StatusRecorder()
    eng.run(ticks=3, live=True, channel=eng.ScriptedChannel({}), brain=MockBrain(), output=rec)
    # steps = round(gap / 0.5): 1, 16, 1 -> cumulative real ticks 1, 17, 18
    assert [s["tick"] for s in rec.statuses] == [1, 17, 18]


def test_run_status_reflects_a_turn(monkeypatch, tmp_path):
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    rec = StatusRecorder()
    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=MockBrain(),
        output=rec,
    )
    assert any(s["status"] == "responding" for s in rec.statuses)
    last = rec.statuses[-1]
    assert last["branch"] == "chat"
    assert last["stats"]["turns"] == 1
    assert last["stats"]["tokens_total"] == 20  # MockBrain chat usage: 8 + 12


def test_run_novelty_selftrigger_routes_to_tool_agent(monkeypatch, tmp_path):
    """With no user input, a novelty crossing reaches out via its named "tool" sub-agent."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    # novelty already over its 0.85 threshold -> the novelty self-trigger fires on tick 1
    monkeypatch.setattr(
        eng,
        "load_state",
        lambda *a, **k: eng.State(
            needs={"connection": 0.0, "rest": 0.0, "novelty": 0.95, "intensity": 0.0}
        ),
    )
    monkeypatch.setattr(eng, "load_prompts", lambda *a, **k: {"novelty": ["розкажи щось нове"]})

    class ToolSpyBrain(MockBrain):
        calls: list = []

        def tool(self, agent, history, system):
            type(self).calls.append(agent)
            return "НОВИЙ ФАКТ", usage_record("sonnet", {"input_tokens": 1, "output_tokens": 1})

    rec = StatusRecorder()
    eng.run(ticks=1, live=False, channel=eng.ScriptedChannel({}), brain=ToolSpyBrain(), output=rec)
    assert ToolSpyBrain.calls == ["session-wiki"]  # routed to the named sub-agent, not deep
    assert rec.replies == ["НОВИЙ ФАКТ"]  # the agent's paragraph is emitted as the reach-out
    assert rec.statuses[-1]["branch"] == "tool"
