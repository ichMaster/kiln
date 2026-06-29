"""
Status-event contract (KILN-012): per-tick status snapshot + session stats.

The engine emits a `status` snapshot every tick (needs + thresholds + cooldowns +
session stats) through the Output seam. ConsoleOutput.status is a no-op; TuiOutput
emits it to the bus. All against a mock brain — zero paid calls.
"""

from __future__ import annotations

import pytest

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
    "cost_usd",
    "cost_estimated",
    "cli_calls",
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
        self.thoughts_shown: list[str] = []  # surfaced inner thoughts (is_thought=True)
        self.curiosity_replies: list[str] = []  # curiosity-driven replies (is_curiosity=True)

    def user(self, text): ...
    def agent(
        self, text, *, is_self=False, lead=False, model=None, is_thought=False, is_curiosity=False
    ):
        self.replies.append(text)
        if is_thought:
            self.thoughts_shown.append(text)
        if is_curiosity:
            self.curiosity_replies.append(text)

    def usage(self, usage, latency=None): ...
    def notice(self, text): ...

    def status(self, snapshot):
        self.statuses.append(snapshot)


def _isolate(monkeypatch, eng, tmp_path):
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
    monkeypatch.setattr(eng, "write_report", lambda *a, **k: None)  # no real usage-report write


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
    monkeypatch.setattr(eng, "load_store", lambda *a, **k: kstore.load_store(store_path))
    monkeypatch.setattr(eng, "save_store", lambda s, *a, **k: kstore.save_store(s, store_path))
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
    monkeypatch.setattr(eng, "load_store", lambda *a, **k: kstore.load_store(store_path))
    monkeypatch.setattr(eng, "save_store", lambda s, *a, **k: kstore.save_store(s, store_path))
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


def test_run_session_close_appends_usage_ledger(monkeypatch, tmp_path):
    """KILN-028: a session close appends exactly one ledger line with the session's totals."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    captured = []
    monkeypatch.setattr(eng, "append_session", lambda entry, *a, **k: captured.append(entry))

    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=MockBrain(),
        output=StatusRecorder(),
    )
    assert len(captured) == 1  # exactly one line per session
    e = captured[0]
    assert set(e) == {
        "session_id", "model", "started_at", "ended_at", "turns", "input", "output",
        "cache_read", "cache_write", "cache_ttl", "cost_usd", "cli_calls", "by_model",
    }  # fmt: skip
    assert e["session_id"] and e["turns"] >= 1 and e["model"]  # a chat turn -> haiku recorded
    assert e["input"] > 0 and e["cost_usd"] >= 0  # tokens + an (estimated) cost
    assert e["cli_calls"] == 0  # MockBrain chat is the SDK branch, not claude -p
    assert e["by_model"] and CHAT_MODEL in e["by_model"]  # per-model breakdown present


def test_run_turn_timestamps_history_and_store(monkeypatch, tmp_path):
    """KILN-032: a run() turn stamps user + bot turns with `at`; the store round-trips them."""
    import kiln.engine as eng
    from kiln import store as kstore

    _isolate(monkeypatch, eng, tmp_path)
    store_path = tmp_path / "store.json"
    monkeypatch.setattr(eng, "load_store", lambda *a, **k: kstore.load_store(store_path))
    monkeypatch.setattr(eng, "save_store", lambda s, *a, **k: kstore.save_store(s, store_path))

    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=MockBrain(),
        output=StatusRecorder(),
    )
    msgs = next(iter(kstore.load_store(store_path)["messages"].values()))
    assert len(msgs) >= 2  # the user turn + the bot reply
    assert all("at" in m and m["at"] for m in msgs)  # every stored turn carries an `at` stamp


def test_run_injects_world_block_per_turn(monkeypatch, tmp_path):
    """KILN-034: run() composes the world block per turn — live ## Зараз + the prior session."""
    import kiln.engine as eng
    from kiln import store as kstore

    _isolate(monkeypatch, eng, tmp_path)
    # seed a previous session so the timeline has content (the current session's turns aren't used)
    seeded = kstore.empty_store()
    seeded["sessions"].append(
        {
            "id": "prev1",
            "started_at": "2026-06-27T20:00:00",
            "ended_at": "x",
            "mode": "live",
            "turns": 2,
        }
    )
    seeded["messages"]["prev1"] = [
        {"role": "user", "text": "вчора питав", "at": "2026-06-27T20:00:00"},
        {"role": "assistant", "text": "вчора відповів", "at": "2026-06-27T20:01:00"},
    ]
    store_path = tmp_path / "store.json"
    kstore.save_store(seeded, store_path)
    monkeypatch.setattr(eng, "load_store", lambda *a, **k: kstore.load_store(store_path))
    monkeypatch.setattr(eng, "save_store", lambda s, *a, **k: kstore.save_store(s, store_path))
    monkeypatch.setattr(eng, "load_canon", lambda *a, **k: "CANON")
    monkeypatch.setattr(eng, "load_memory", lambda *a, **k: "")
    monkeypatch.setattr(eng, "digest_facts", lambda *a, **k: "")
    monkeypatch.setattr(eng, "USER_LOCATION", "Львів")
    monkeypatch.setattr(eng, "WORLD_AWARENESS", True)
    monkeypatch.setattr(eng, "RECENT_MESSAGES", 10)

    seen = []

    class RB(MockBrain):
        def chat(self, history, system):
            seen.append(system)
            return super().chat(history, system)

    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=RB(),
        output=StatusRecorder(),
    )
    assert "## Зараз" in seen[0] and "Львів" in seen[0]  # the live clock
    assert (
        "## Повідомлення з минулої сесії" in seen[0]
    )  # the PRIOR session's tail (ready from turn 1)
    assert "вчора питав" in seen[0]  # from the previous session, not the current one


def test_previous_session_turns_spans_all_sessions(tmp_path):
    """The v0.8 timeline draws from ALL prior sessions (oldest->newest), not just the last one — so
    a short final session no longer starves the prior-messages block."""
    import kiln.engine as eng
    from kiln import store as kstore

    def turn(role, text):
        return {"role": role, "text": text}

    st = kstore.empty_store()
    seed = [
        ("s1", "2026-06-27T10:00:00", [turn("user", "a1"), turn("bot", "b1")]),
        ("s2", "2026-06-28T10:00:00", [turn("user", "a2"), turn("bot", "b2")]),
        ("s3", "2026-06-29T10:00:00", [turn("user", "a3")]),  # short last session
    ]
    for sid, started, msgs in seed:
        st["sessions"].append(
            {"id": sid, "started_at": started, "ended_at": "x", "mode": "live", "turns": len(msgs)}
        )
        st["messages"][sid] = msgs
    p = tmp_path / "store.json"
    kstore.save_store(st, p)

    texts = [t["text"] for t in eng._previous_session_turns(p)]
    assert texts == ["a1", "b1", "a2", "b2", "a3"]  # every session, chronological — not just s3


def test_session_persisted_in_real_time(monkeypatch, tmp_path):
    """Turns are upserted into the store DURING the session (not only on close), so a crash can't
    lose them and a freshly attached client's snapshot sees the live conversation."""
    import kiln.engine as eng
    from kiln import store as kstore

    _isolate(monkeypatch, eng, tmp_path)
    store_path = tmp_path / "store.json"
    monkeypatch.setattr(eng, "load_store", lambda *a, **k: kstore.load_store(store_path))
    monkeypatch.setattr(eng, "save_store", lambda s, *a, **k: kstore.save_store(s, store_path))
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "S")

    mid_turns = []

    class Probe:
        def user(self, *a, **k): ...
        def agent(self, *a, **k): ...
        def usage(self, *a, **k): ...
        def notice(self, *a, **k): ...
        def status(self, snap):  # observe the store on every tick, mid-session
            st = kstore.load_store(store_path)
            sid = st["sessions"][0]["id"] if st["sessions"] else None
            mid_turns.append(len(st["messages"].get(sid, [])) if sid else 0)

    eng.run(
        ticks=3,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=MockBrain(),
        output=Probe(),
    )
    assert max(mid_turns) >= 2  # the turn hit the store mid-session, before the close finalize


def test_reload_command_rereads_canon(tmp_path, monkeypatch):
    """/reload re-reads canon mid-session — the system prompt swaps, no restart, no session drop."""
    import kiln.engine as eng
    from kiln.config import AgentPaths

    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "")
    monkeypatch.setattr(eng, "extract_facts", lambda *a, **k: [])
    monkeypatch.setattr(eng, "digest_facts", lambda *a, **k: "")

    canon = tmp_path / "canon.md"
    canon.write_text("CANON-V1", encoding="utf-8")
    paths = AgentPaths(
        state_dir=tmp_path / "s",
        needs_file=tmp_path / "n.json",
        store_file=tmp_path / "store.json",
        usage_ledger=tmp_path / "l.jsonl",
        usage_report=tmp_path / "r.md",
        canon_file=canon,
        prompts_file=tmp_path / "p.md",
    )
    systems = []

    class RecBrain(MockBrain):
        def chat(self, history, system):
            systems.append(system)
            return super().chat(history, system)

    class Chan:  # tick 2 edits the canon on disk; tick 3 sends /reload
        def __init__(self):
            self.t = 0

        def poll(self):
            self.t += 1
            if self.t == 2:
                canon.write_text("CANON-V2", encoding="utf-8")
                return None
            return {1: "first", 3: "/reload", 4: "second"}.get(self.t)

    eng.run(
        ticks=5, live=False, paths=paths, brain=RecBrain(), output=StatusRecorder(), channel=Chan()
    )
    assert systems[0].startswith("CANON-V1")  # turn before /reload
    assert systems[-1].startswith("CANON-V2")  # turn after /reload picked up the new canon


def test_rotate_finalizes_session_and_starts_fresh(monkeypatch, tmp_path):
    """/rotate finalizes the current session (summary folds in via a worker) and starts a fresh
    session_id — non-blocking. Two sessions get stored; both rotation notices fire."""
    import kiln.engine as eng
    from kiln import store as kstore

    _isolate(monkeypatch, eng, tmp_path)
    store_path = tmp_path / "store.json"
    monkeypatch.setattr(eng, "load_store", lambda *a, **k: kstore.load_store(store_path))
    monkeypatch.setattr(eng, "save_store", lambda s, *a, **k: kstore.save_store(s, store_path))
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "SUM")

    class NoticeRec(StatusRecorder):
        def __init__(self):
            super().__init__()
            self.notices = []

        def notice(self, text):
            self.notices.append(text)

    out = NoticeRec()
    eng.run(
        ticks=8,
        live=False,
        brain=MockBrain(),
        output=out,
        channel=eng.ScriptedChannel({1: "A", 2: "/rotate", 3: "B"}),
    )
    st = kstore.load_store(store_path)
    assert len(st["sessions"]) == 2  # the rotated session + the fresh one
    assert any("rotated" in n for n in out.notices)  # synchronous cutover notice
    assert any("summarized" in n for n in out.notices)  # async completion notice
    assert any("SUM" in (x.get("text") or "") for x in st["summaries"])  # old session summarized


def _mood_scenario(monkeypatch, eng, tmp_path):
    """Common setup: isolate, mute world, fixed needs — so only the ## Настрій block varies."""
    _isolate(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(eng, "load_canon", lambda *a, **k: "CANON")
    monkeypatch.setattr(eng, "load_memory", lambda *a, **k: "")
    monkeypatch.setattr(eng, "digest_facts", lambda *a, **k: "")
    monkeypatch.setattr(eng, "WORLD_AWARENESS", False)  # isolate the mood section
    monkeypatch.setattr(
        eng,
        "load_state",
        lambda *a, **k: eng.State(
            needs={"connection": 0.72, "novelty": 0.30, "rest": 0.55, "intensity": 0.41}
        ),
    )


def _capture_systems(eng, n_ticks, script):
    seen = []

    class RB(MockBrain):
        def chat(self, history, system):
            seen.append(system)
            return super().chat(history, system)

    eng.run(
        ticks=n_ticks,
        live=False,
        channel=eng.ScriptedChannel(script),
        brain=RB(),
        output=StatusRecorder(),
    )
    return seen


def test_run_injects_mood_block_per_turn_biorhythm_static(monkeypatch, tmp_path):
    """KILN-038: run() composes ## Настрій per turn (live needs); the biorhythm is computed once."""
    import kiln.engine as eng

    _mood_scenario(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(eng, "MOOD_AWARENESS", True)
    monkeypatch.setattr(eng, "BIORHYTHM", True)
    seen = _capture_systems(eng, 3, {1: "привіт", 2: "ще"})
    assert len(seen) >= 2
    assert "## Настрій" in seen[0] and "самотність" in seen[0]  # the live needs

    def bio(s):
        return next(line for line in s.splitlines() if line.startswith("- фізичний"))

    assert bio(seen[0]) == bio(seen[1])  # biorhythm identical across turns (computed once at start)


def test_run_mood_awareness_off_no_section(monkeypatch, tmp_path):
    import kiln.engine as eng

    _mood_scenario(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(eng, "MOOD_AWARENESS", False)
    seen = _capture_systems(eng, 2, {1: "привіт"})
    assert "## Настрій" not in seen[0] and seen[0] == "CANON"  # mood off + world off -> bare canon


def test_run_biorhythm_off_omits_sub_block(monkeypatch, tmp_path):
    import kiln.engine as eng

    _mood_scenario(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(eng, "MOOD_AWARENESS", True)
    monkeypatch.setattr(eng, "BIORHYTHM", False)
    seen = _capture_systems(eng, 2, {1: "привіт"})
    assert "## Настрій" in seen[0] and "Біоритм дня:" not in seen[0]  # needs yes, biorhythm no


def _thought_scenario(monkeypatch, eng, tmp_path, reflection=0.9):
    """Isolate with a real tmp store + high reflection so a thought fires on an idle tick."""
    from kiln import store as kstore

    _isolate(monkeypatch, eng, tmp_path)
    store_path = tmp_path / "store.json"
    monkeypatch.setattr(eng, "load_store", lambda *a, **k: kstore.load_store(store_path))
    monkeypatch.setattr(eng, "save_store", lambda s, *a, **k: kstore.save_store(s, store_path))
    monkeypatch.setattr(eng, "load_prompts", lambda *a, **k: {"thought": ["поміркуй наодинці"]})
    monkeypatch.setattr(
        eng,
        "load_state",
        lambda *a, **k: eng.State(
            needs={
                "connection": 0.0,
                "rest": 0.0,
                "novelty": 0.0,
                "intensity": 0.0,
                "reflection": reflection,
            }
        ),
    )
    return store_path


def test_run_thought_generated_stored_hidden(monkeypatch, tmp_path):
    """KILN-042: a reflection crossing on an idle tick generates a thought — stored, hidden."""
    import kiln.engine as eng
    from kiln import store as kstore

    monkeypatch.setattr(eng, "THOUGHTS_ENABLED", True)
    monkeypatch.setattr(eng, "_thought_visible", lambda every: False)  # KILN-043: force hidden
    store_path = _thought_scenario(monkeypatch, eng, tmp_path)
    rec = StatusRecorder()
    eng.run(ticks=2, live=False, channel=eng.ScriptedChannel({}), brain=MockBrain(), output=rec)
    thoughts = kstore.load_store(store_path)["thoughts"]
    assert len(thoughts) == 1 and thoughts[0]["shown"] is False  # one thought, hidden
    assert thoughts[0]["text"]  # the (mock) thought text
    assert rec.replies == []  # nothing displayed — not a conversation turn


def test_run_thought_surfaced_becomes_a_turn(monkeypatch, tmp_path):
    """KILN-043: with the RNG forced to hit, a thought surfaces (is_thought) AND becomes a turn."""
    import kiln.engine as eng
    from kiln import store as kstore

    monkeypatch.setattr(eng, "THOUGHTS_ENABLED", True)
    monkeypatch.setattr(eng, "_thought_visible", lambda every: True)  # force surfaced
    store_path = _thought_scenario(monkeypatch, eng, tmp_path)
    rec = StatusRecorder()
    eng.run(ticks=2, live=False, channel=eng.ScriptedChannel({}), brain=MockBrain(), output=rec)
    saved = kstore.load_store(store_path)
    thought = saved["thoughts"][0]
    assert thought["shown"] is True  # stored as surfaced
    assert rec.thoughts_shown == [thought["text"]]  # displayed via agent(is_thought=True)
    # it entered the conversation as a real assistant turn (in the session's stored messages)
    msgs = saved["messages"].get(saved["sessions"][-1]["id"], [])
    assert any(m["role"] == "assistant" and m["text"] == thought["text"] for m in msgs)


def test_run_thoughts_disabled_no_thought(monkeypatch, tmp_path):
    import kiln.engine as eng
    from kiln import store as kstore

    monkeypatch.setattr(eng, "THOUGHTS_ENABLED", False)
    store_path = _thought_scenario(monkeypatch, eng, tmp_path)
    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({}),
        brain=MockBrain(),
        output=StatusRecorder(),
    )
    assert kstore.load_store(store_path)["thoughts"] == []  # off -> no thoughts at all


def test_run_user_input_preempts_thought(monkeypatch, tmp_path):
    import kiln.engine as eng
    from kiln import store as kstore

    monkeypatch.setattr(eng, "THOUGHTS_ENABLED", True)
    store_path = _thought_scenario(monkeypatch, eng, tmp_path)
    # a user message on the FIRST poll (ScriptedChannel: tick 0) — input wins, no thought fires
    eng.run(
        ticks=1,
        live=False,
        channel=eng.ScriptedChannel({0: "привіт"}),
        brain=MockBrain(),
        output=StatusRecorder(),
    )
    assert kstore.load_store(store_path)["thoughts"] == []  # user input pre-empted the thought


def test_self_prompt_appends_silence_note_only_when_reached_out():
    import kiln.engine as eng

    prompts = {"connection": ["озвися"]}
    assert eng._self_prompt(prompts, "connection", reached_out=False) == "озвися"  # first: plain
    again = eng._self_prompt(prompts, "connection", reached_out=True)
    assert again.startswith("озвися ") and eng.SELF_SILENCE_NOTE in again  # repeat: + silence note


def test_run_self_trigger_first_reach_out_has_no_silence_note(monkeypatch, tmp_path):
    """KILN: the first reach-out uses a plain self-prompt (no 'you already wrote' note)."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(
        eng,
        "load_state",
        lambda *a, **k: eng.State(
            needs={"connection": 0.95, "rest": 0.0, "novelty": 0.0, "intensity": 0.0}
        ),
    )
    captured = []

    class RB(MockBrain):
        def chat(self, history, system):
            captured.append(history[-1]["text"])  # the injected self-prompt (last user turn)
            return super().chat(history, system)

    eng.run(
        ticks=2, live=False, channel=eng.ScriptedChannel({}), brain=RB(), output=StatusRecorder()
    )
    assert captured  # a connection reach-out fired
    assert eng.SELF_SILENCE_NOTE not in captured[0]  # first reach-out -> no silence note


def test_run_world_awareness_off_no_section(monkeypatch, tmp_path):
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(eng, "WORLD_AWARENESS", False)

    seen = []

    class RB(MockBrain):
        def chat(self, history, system):
            seen.append(system)
            return super().chat(history, system)

    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=RB(),
        output=StatusRecorder(),
    )
    assert seen and "## Зараз" not in seen[0]  # disabled -> no world block


def test_usage_report_off_skips_ledger_and_report(monkeypatch, tmp_path):
    """KILN-030: USAGE_REPORT=0 -> a session close writes no ledger line and no report."""
    import kiln.engine as eng

    _isolate(monkeypatch, eng, tmp_path)
    monkeypatch.setattr(eng, "USAGE_REPORT", False)
    ledger_calls, report_calls = [], []
    monkeypatch.setattr(eng, "append_session", lambda *a, **k: ledger_calls.append(1))
    monkeypatch.setattr(eng, "write_report", lambda *a, **k: report_calls.append(1))

    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=MockBrain(),
        output=StatusRecorder(),
    )
    assert ledger_calls == [] and report_calls == []  # both skipped when disabled


def test_run_noise_only_session_not_stored(monkeypatch, tmp_path):
    """KILN-019: when the session prunes to nothing, the store stays empty (no session/summary)."""
    import kiln.engine as eng
    from kiln import store as kstore

    _isolate(monkeypatch, eng, tmp_path)
    store_path = tmp_path / "store.json"
    monkeypatch.setattr(eng, "load_store", lambda *a, **k: kstore.load_store(store_path))
    monkeypatch.setattr(eng, "save_store", lambda s, *a, **k: kstore.save_store(s, store_path))
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


# --- v0.11 curiosity: discharge on asking + the reply marker (KILN-047) ---


def test_is_curiosity_reply_detects_questions():
    from kiln.engine import is_curiosity_reply

    assert is_curiosity_reply("а чому саме так?") is True  # question mark
    assert is_curiosity_reply("Що ти зараз читаєш") is True  # leading interrogative, no '?'
    assert is_curiosity_reply("зрозуміло, дякую.") is False  # a statement
    assert is_curiosity_reply("якось дивно це звучить") is False  # 'якось' is not the word 'як'


class _AskBrain(MockBrain):
    """A brain whose chat reply asks a question (so is_curiosity_reply is true)."""

    def chat(self, history, system):
        return "а що саме ти маєш на увазі?", usage_record(
            CHAT_MODEL, {"input_tokens": 8, "output_tokens": 12}
        )


def _curiosity_run(monkeypatch, eng, tmp_path, *, curiosity, brain):
    """Isolate with a fixed curiosity level, run one user turn, and return the (mutated) State."""
    _isolate(monkeypatch, eng, tmp_path)
    st = eng.State(
        needs={
            "connection": 0.0,
            "rest": 0.0,
            "novelty": 0.0,
            "intensity": 0.0,
            "reflection": 0.0,
            "curiosity": curiosity,
        }
    )
    monkeypatch.setattr(eng, "load_state", lambda *a, **k: st)
    rec = StatusRecorder()
    eng.run(
        ticks=1, live=False, channel=eng.ScriptedChannel({0: "привіт"}), brain=brain, output=rec
    )
    return st, rec


def test_run_curiosity_discharges_on_a_question(monkeypatch, tmp_path):
    """v0.11 monitor: a question reply discharges curiosity via SATIATION['asked'] (and marks the
    reply); curious -> asks -> sated."""
    import kiln.engine as eng
    from kiln.config import DRIFT, NEED_TRIGGERS, SATIATION

    start = NEED_TRIGGERS["curiosity"]["threshold"] + 0.1  # comfortably over the threshold
    st, rec = _curiosity_run(monkeypatch, eng, tmp_path, curiosity=start, brain=_AskBrain())
    drop = SATIATION["asked"]["curiosity"]  # negative
    assert st.needs["curiosity"] == pytest.approx(start + DRIFT["curiosity"] + drop)
    assert rec.curiosity_replies  # the reply carried is_curiosity=True


def test_run_curiosity_unchanged_without_a_question(monkeypatch, tmp_path):
    """A no-question reply leaves curiosity high (only drift) — it stays high till she asks."""
    import kiln.engine as eng
    from kiln.config import DRIFT, NEED_TRIGGERS

    start = NEED_TRIGGERS["curiosity"]["threshold"] + 0.1  # over the threshold (monitor on)...
    st, rec = _curiosity_run(monkeypatch, eng, tmp_path, curiosity=start, brain=MockBrain())
    assert st.needs["curiosity"] == pytest.approx(start + DRIFT["curiosity"])  # ...but no question
    assert not rec.curiosity_replies


def test_run_curiosity_no_discharge_below_threshold(monkeypatch, tmp_path):
    """Below the curiosity threshold the monitor doesn't discharge — even a question reply (she
    wasn't curious enough). The band cue keeps her calm there anyway."""
    import kiln.engine as eng
    from kiln.config import DRIFT

    st, rec = _curiosity_run(monkeypatch, eng, tmp_path, curiosity=0.30, brain=_AskBrain())
    assert st.needs["curiosity"] == pytest.approx(0.30 + DRIFT["curiosity"])  # only drift
    assert not rec.curiosity_replies


def test_status_snapshot_curiosity_has_threshold_but_no_self_trigger():
    """v0.11: curiosity is in the snapshot thresholds/actions (panel parity — threshold + colour +
    `→ ask`, like the other needs), yet it's discharged by the question monitor, not a crossing."""
    from kiln.config import NEED_TRIGGERS
    from tui.render import needs_panel_lines

    thr = NEED_TRIGGERS["curiosity"]["threshold"]
    lvl = thr + 0.05  # over the threshold
    state = State(needs={"connection": 0.1, "curiosity": lvl})
    snap = _status_snapshot("idle", state, TriggerBook(), SessionStats(), None, 1)
    assert snap["thresholds"]["curiosity"] == thr
    assert snap["actions"]["curiosity"] == "ask"
    row = next(r for r in needs_panel_lines(snap) if "цікавість" in r)
    assert f"{lvl:.2f}/{thr:.2f}" in row and "→ ask" in row  # coloured row, over-threshold
