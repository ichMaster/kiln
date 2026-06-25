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

SNAPSHOT_KEYS = {
    "status",
    "model",
    "branch",
    "needs",
    "thresholds",
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
    snap = _status_snapshot("idle", state, tg, SessionStats(), branch=None)
    assert set(snap) == SNAPSHOT_KEYS
    assert set(snap["stats"]) == STATS_KEYS
    assert snap["status"] == "idle"
    assert snap["needs"] == {"connection": 0.5, "novelty": 0.9}
    assert snap["thresholds"]  # populated from NEED_TRIGGERS
    assert snap["cooldowns"] == {"novelty": 3}  # rest (0) filtered out
    assert snap["hottest"][0] == "novelty"


def test_status_snapshot_model_follows_branch():
    state, tg, stats = State(needs={}), TriggerBook(), SessionStats()
    assert _status_snapshot("responding", state, tg, stats, "chat")["model"] == CHAT_MODEL
    assert _status_snapshot("responding", state, tg, stats, "think")["model"] == DEEP_MODEL
    assert (
        _status_snapshot("idle", state, tg, stats, None)["model"] == DEEP_MODEL
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
    def agent(self, text, *, is_self=False, lead=False):
        self.replies.append(text)

    def usage(self, usage): ...
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
