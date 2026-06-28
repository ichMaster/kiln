"""
Status-event contract (KILN-012): per-tick status snapshot + session stats.

The engine emits a `status` snapshot every tick (needs + thresholds + cooldowns +
session stats) through the Output seam. ConsoleOutput.status is a no-op; TuiOutput
emits it to the bus. All against a mock brain — zero paid calls.
"""

from __future__ import annotations

from kiln.brain import MockBrain
from kiln.config import CHAT_MODEL, DEEP_MODEL, REST_MESSAGE
from kiln.engine import State, TriggerBook, _status_snapshot
from kiln.output import ConsoleOutput
from kiln.stats import SessionStats
from kiln.store import empty_store
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
    "self_messages",
    "stats",
}
STATS_KEYS = {
    "turns",
    "tokens_total",
    "tokens_by_branch",
    "last_tokens",
    "last_latency",
    "avg_latency",
    "input_total",
    "output_total",
    "cache_read_total",
    "cache_write_total",
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
    monkeypatch.setattr(eng, "load_store", lambda *a, **k: empty_store())
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "")
    monkeypatch.setattr(eng, "extract_facts", lambda *a, **k: [])  # no real claude -p in tests
    monkeypatch.setattr(eng, "digest_facts", lambda *a, **k: "")  # start-time facts digest off
    monkeypatch.setattr(eng, "save_store", lambda *a, **k: None)


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


def test_run_session_close_writes_to_store(monkeypatch, tmp_path):
    """KILN-018: closing a session writes one session + its messages + one summary to the store."""
    import kiln.engine as eng
    from kiln import store as kstore

    _isolate(monkeypatch, eng, tmp_path)  # isolates state; store + summarize overridden below
    store_path = tmp_path / "store.json"
    monkeypatch.setattr(eng, "load_store", lambda: kstore.load_store(store_path))
    monkeypatch.setattr(eng, "save_store", lambda s: kstore.save_store(s, store_path))
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "ПІДСУМОК")

    rec = StatusRecorder()
    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=MockBrain(),
        output=rec,
    )
    s = kstore.load_store(store_path)
    assert len(s["sessions"]) == 1 and s["sessions"][0]["turns"] >= 2
    sid = s["sessions"][0]["id"]
    assert s["messages"].get(sid)  # the raw turns are stored (the RAG corpus)
    assert len(s["summaries"]) == 1 and s["summaries"][0]["text"] == "ПІДСУМОК"


def test_run_session_close_extracts_facts(monkeypatch, tmp_path):
    """KILN-023: closing a session folds extracted facts into the store, deduped."""
    import kiln.engine as eng
    from kiln import store as kstore

    _isolate(monkeypatch, eng, tmp_path)
    store_path = tmp_path / "store.json"
    # pre-seed one fact so we exercise the dedupe path on close
    seeded = kstore.empty_store()
    kstore.add_facts(seeded, ["Віталік пише агентів"], "old", "2026-06-01")
    kstore.save_store(seeded, store_path)
    monkeypatch.setattr(eng, "load_store", lambda: kstore.load_store(store_path))
    monkeypatch.setattr(eng, "save_store", lambda s: kstore.save_store(s, store_path))
    # the model "extracts" one repeat (deduped) + one new fact
    monkeypatch.setattr(
        eng, "extract_facts", lambda *a, **k: ["Віталік пише агентів", "Любить шахи"]
    )

    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=MockBrain(),
        output=StatusRecorder(),
    )
    facts = kstore.load_store(store_path)["facts"]
    texts = [f["text"] for f in facts]
    assert texts == ["Віталік пише агентів", "Любить шахи"]  # repeat deduped, new appended
    assert facts[0]["source_session"] == "old"  # origin kept on the deduped one


def test_run_start_injects_facts_digest_into_system(monkeypatch, tmp_path):
    """KILN-025: run() start composes canon + memory + the facts digest into the system prompt."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(eng, "load_canon", lambda *a, **k: "CANON")
    monkeypatch.setattr(eng, "load_memory", lambda *a, **k: "")
    monkeypatch.setattr(eng, "digest_facts", lambda *a, **k: "Віталік любить шахи")

    seen = {}

    class RecordingBrain(MockBrain):
        def chat(self, history, system):
            seen["system"] = system
            return super().chat(history, system)

    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=RecordingBrain(),
        output=StatusRecorder(),
    )
    assert "## Facts about the user" in seen["system"]  # the dedicated section is present
    assert "Віталік любить шахи" in seen["system"]  # carrying the digest


def test_run_noise_only_session_not_stored(monkeypatch, tmp_path):
    """KILN-019: when the session prunes to nothing, the store stays empty (no session/summary)."""
    import kiln.engine as eng
    from kiln import store as kstore

    _isolate(monkeypatch, eng, tmp_path)
    store_path = tmp_path / "store.json"
    monkeypatch.setattr(eng, "load_store", lambda: kstore.load_store(store_path))
    monkeypatch.setattr(eng, "save_store", lambda s: kstore.save_store(s, store_path))
    monkeypatch.setattr(eng, "prune_history", lambda h: [])  # everything was noise
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "SHOULD-NOT-RUN")

    rec = StatusRecorder()
    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=MockBrain(),
        output=rec,
    )
    s = kstore.load_store(store_path)
    assert s["sessions"] == [] and s["summaries"] == [] and s["messages"] == {}


def test_run_connection_reach_out_uses_novelty_model(monkeypatch, tmp_path):
    """connection fires the reach-out; high novelty (low intensity) makes it session-wiki."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    # connection over its 0.80 threshold -> reach-out fires; novelty over 0.85 -> session-wiki
    monkeypatch.setattr(
        eng,
        "load_state",
        lambda *a, **k: eng.State(
            needs={"connection": 0.95, "rest": 0.0, "novelty": 0.95, "intensity": 0.0}
        ),
    )
    monkeypatch.setattr(eng, "load_prompts", lambda *a, **k: {"connection": ["озвись"]})

    class ToolSpyBrain(MockBrain):
        calls: list = []

        def tool(self, agent, history, system):
            type(self).calls.append(agent)
            return "НОВИЙ ФАКТ", usage_record("sonnet", {"input_tokens": 1, "output_tokens": 1})

    rec = StatusRecorder()
    eng.run(ticks=1, live=False, channel=eng.ScriptedChannel({}), brain=ToolSpyBrain(), output=rec)
    assert ToolSpyBrain.calls == ["session-wiki"]  # connection fired; novelty chose the model
    assert rec.replies == ["НОВИЙ ФАКТ"]
    assert rec.statuses[-1]["branch"] == "tool"


def test_run_self_messages_off_suppresses_reach_out(monkeypatch, tmp_path):
    """With /self off (self_messages=False), a connection crossing does NOT reach out."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(
        eng,
        "load_state",
        lambda *a, **k: eng.State(
            needs={"connection": 0.95, "rest": 0.0, "novelty": 0.0, "intensity": 0.0},
            self_messages=False,
        ),
    )
    brain = _CountingBrain()
    rec = StatusRecorder()
    eng.run(ticks=1, live=False, channel=eng.ScriptedChannel({}), brain=brain, output=rec)
    assert brain.calls == 0  # no proactive reach-out
    assert rec.replies == []
    assert rec.statuses[-1]["self_messages"] is False  # surfaced for the status bar


def test_run_connection_reach_out_is_chat_when_calm(monkeypatch, tmp_path):
    """connection fires; with calm intensity/novelty it answers via cheap chat, not opus."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(
        eng,
        "load_state",
        lambda *a, **k: eng.State(
            needs={"connection": 0.95, "rest": 0.0, "novelty": 0.0, "intensity": 0.0}
        ),
    )
    monkeypatch.setattr(eng, "load_prompts", lambda *a, **k: {"connection": ["озвись"]})
    rec = StatusRecorder()
    eng.run(ticks=1, live=False, channel=eng.ScriptedChannel({}), brain=MockBrain(), output=rec)
    assert rec.statuses[-1]["branch"] == "chat"  # baseline reach-out, no opus
    assert any("dry-run chat" in r for r in rec.replies)


# --- rest gate: too tired to answer ----------------------------------------


class _CountingBrain(MockBrain):
    """MockBrain that counts every brain call — to prove the gate skips the brain."""

    def __init__(self):
        self.calls = 0

    def chat(self, history, system):
        self.calls += 1
        return super().chat(history, system)

    def deep(self, prompt, history, system, with_tools):
        self.calls += 1
        return super().deep(prompt, history, system, with_tools)

    def tool(self, agent, history, system):
        self.calls += 1
        return super().tool(agent, history, system)


def _rest_state(rest):
    return lambda *a, **k: State(
        needs={"connection": 0.0, "rest": rest, "novelty": 0.0, "intensity": 0.0}
    )


def test_run_resting_says_rest_message_and_does_not_answer(monkeypatch, tmp_path):
    """rest over its threshold -> a user message is met with REST_MESSAGE, brain untouched."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(eng, "load_state", _rest_state(0.95))
    brain = _CountingBrain()
    rec = StatusRecorder()
    eng.run(
        ticks=1, live=False, channel=eng.ScriptedChannel({0: "привіт"}), brain=brain, output=rec
    )
    assert brain.calls == 0  # she did not answer through any branch
    assert rec.replies == [REST_MESSAGE]  # she said she needs to rest
    assert rec.statuses[-1]["status"] == "resting"


def test_run_resting_says_message_once_per_episode(monkeypatch, tmp_path):
    """She says REST_MESSAGE once on entering rest, not to every message during it."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(eng, "load_state", _rest_state(0.95))
    monkeypatch.setitem(eng.SATIATION["idle"], "rest", 0.0)  # no recovery -> stays resting
    brain = _CountingBrain()
    rec = StatusRecorder()
    eng.run(
        ticks=3,
        live=False,
        channel=eng.ScriptedChannel({0: "1", 1: "2", 2: "3"}),
        brain=brain,
        output=rec,
    )
    assert brain.calls == 0
    assert rec.replies == [REST_MESSAGE]  # said ONCE, despite three messages
    assert all(s["status"] == "resting" for s in rec.statuses)


def test_run_resting_suppresses_self_trigger(monkeypatch, tmp_path):
    """While resting, even a need over threshold does not reach out (no tool/deep call)."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    # both rest (sleep) and novelty (would reach out) are over threshold -> rest wins
    monkeypatch.setattr(
        eng,
        "load_state",
        lambda *a, **k: State(
            needs={"connection": 0.0, "rest": 0.95, "novelty": 0.95, "intensity": 0.0}
        ),
    )
    brain = _CountingBrain()
    rec = StatusRecorder()
    eng.run(ticks=1, live=False, channel=eng.ScriptedChannel({}), brain=brain, output=rec)
    assert brain.calls == 0  # self-trigger suppressed
    assert rec.replies == [REST_MESSAGE]  # announced once on entering rest
    assert rec.statuses[-1]["status"] == "resting"


def test_run_resting_commands_still_work(monkeypatch, tmp_path):
    """Slash commands are handled even while resting (e.g. /quit isn't locked out)."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(eng, "load_state", _rest_state(0.95))
    brain = _CountingBrain()
    rec = StatusRecorder()
    eng.run(
        ticks=2, live=False, channel=eng.ScriptedChannel({0: "/status"}), brain=brain, output=rec
    )
    assert brain.calls == 0
    assert REST_MESSAGE not in rec.replies  # a command is not met with the rest line


def test_run_rest_hysteresis_sleeps_then_wakes(monkeypatch, tmp_path):
    """Once resting she stays resting (between threshold and REST_WAKE), then wakes."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(eng, "REST_WAKE", 0.80)
    monkeypatch.setitem(eng.DRIFT, "rest", 0.0)
    monkeypatch.setitem(eng.SATIATION["idle"], "rest", -0.10)  # recover 0.10/idle tick
    monkeypatch.setattr(eng, "load_state", _rest_state(0.95))
    rec = StatusRecorder()
    eng.run(ticks=3, live=False, channel=eng.ScriptedChannel({}), brain=MockBrain(), output=rec)
    # rest: 0.95 ->0.85 ->0.75; resting until rest <= 0.80, so it wakes on tick 3
    assert [s["status"] for s in rec.statuses] == ["resting", "resting", "idle"]
