"""
KILN-066 — the FSM transition trace + the pinned FSM contract. The trace is a per-tick sink (off by
default) emitting `fsm.trace_record` (state → event → guard → action → next_state + needs) — the log
1.10 simulation consumes. All against MockBrain (zero paid calls). The unchanged WS event protocol
is pinned by the existing v1.1 server tests (still green in the suite).
"""

from kiln import engine as eng
from kiln import fsm
from kiln.brain import MockBrain
from kiln.config import AgentPaths
from kiln.engine import save_state
from kiln.fsm import State  # the FSM state enum (distinct from engine.State, the needs dataclass)


def _tmp_paths(root) -> AgentPaths:
    s, k = root / "state", root / "kiln"
    return AgentPaths(
        state_dir=s,
        needs_file=k / "needs.json",
        store_file=k / "store.json",
        usage_ledger=k / "usage-ledger.jsonl",
        usage_report=k / "usage-report.md",
        canon_file=s / "canon.md",
        prompts_file=s / "prompts.md",
    )


class _Null:
    """A silent Output sink — we only care about the trace here."""

    def user(self, *a, **k): ...
    def agent(self, *a, **k): ...
    def usage(self, *a, **k): ...
    def notice(self, *a, **k): ...
    def status(self, *a, **k): ...


def _run(paths, ticks=3, inputs=None, trace=None):
    eng.run(
        ticks=ticks,
        live=False,
        channel=eng.ScriptedChannel(inputs or {}),
        brain=MockBrain(),
        output=_Null(),
        paths=paths,
        trace=trace,
    )


# --- the record shape (the pinned contract) -----------------------------------


def test_trace_record_shape_and_keys():
    ev = fsm.Event(fsm.EventKind.USER_MESSAGE, "hi")
    rec = fsm.trace_record(7, State.IDLE, ev, "respond", State.COOLING, {"rest": 0.5}, guard=None)
    assert set(rec) == fsm.TRACE_KEYS
    assert rec["tick"] == 7
    assert rec["state"] == "idle"
    assert rec["event"] == "user.message"
    assert rec["action"] == "respond"
    assert rec["next_state"] == "cooling"
    assert rec["needs"] == {"rest": 0.5}


def test_guard_name_maps_lambda_and_none_to_none():
    # the always-true default guard is a lambda → None; a named guard → its name
    rest_rule = next(r for r in fsm.DEFAULT_TABLE if r.action == "enter_rest")
    assert fsm.guard_name(rest_rule) == "_should_rest"
    plain = next(r for r in fsm.DEFAULT_TABLE if r.action == "respond")
    assert fsm.guard_name(plain) is None
    assert fsm.guard_name(None) is None


# --- tracing on / off ---------------------------------------------------------


def test_trace_off_emits_no_records(tmp_path):
    # no sink passed → the run must not require one and produces nothing to capture (default off)
    captured: list[dict] = []
    _run(_tmp_paths(tmp_path / "off"), ticks=3, trace=None)
    assert captured == []


def test_trace_on_emits_one_record_per_tick(tmp_path):
    recs: list[dict] = []
    _run(_tmp_paths(tmp_path / "on"), ticks=3, inputs={}, trace=recs.append)
    assert len(recs) == 3  # one per tick
    for r in recs:
        assert set(r) == fsm.TRACE_KEYS
        assert r["state"] in {s.value for s in State}
        assert r["next_state"] in {s.value for s in State}
    # quiet ticks: idle → idle
    assert all(r["event"] == "tick" and r["action"] == "idle" for r in recs)
    assert [r["tick"] for r in recs] == [1, 2, 3]  # total_ticks is 1-based in dry-run


def test_trace_captures_a_user_turn_transition(tmp_path):
    recs: list[dict] = []
    _run(_tmp_paths(tmp_path / "turn"), ticks=2, inputs={0: "привіт"}, trace=recs.append)
    first = recs[0]
    assert first["event"] == "user.message"
    assert first["action"] == "respond"
    assert first["next_state"] == "cooling"  # a turn settles into cooling (KILN-065)


def test_trace_names_the_guard_that_fired(tmp_path):
    recs: list[dict] = []
    p = _tmp_paths(tmp_path / "guard")
    p.needs_file.parent.mkdir(parents=True, exist_ok=True)
    save_state(eng.State(needs={"connection": 0.9}), p.needs_file)  # a reach-out this tick
    _run(p, ticks=1, inputs={}, trace=recs.append)
    assert recs[0]["event"] == "self_trigger"
    assert recs[0]["action"] == "reach_out"
    assert recs[0]["guard"] == "_is_reach_out"


# --- the table contract still reproduces v1.2 --------------------------------


def test_advance_and_match_agree():
    ctx = fsm.Ctx(needs={"rest": 0.2})
    for st in State:
        for kind in fsm.EventKind:
            ev = fsm.Event(kind, "connection")
            rule = fsm.match(st, ev, ctx)
            action, nxt = fsm.advance(st, ev, ctx)
            if rule is None:
                assert (action, nxt) == ("idle", st)  # defensive fallback
            else:
                assert (action, nxt) == (rule.action, rule.to)
