"""
KILN-061 — the static FSM model (kiln/fsm.py). Pure, no engine wired up, no paid calls: `advance` is
exercised directly against hand-built `Ctx`. These tests pin that the default table reproduces
v1.2's transition structure (input > self-trigger > tick is enforced upstream by the queue; here
each event is mapped in isolation) and the rest-gate hysteresis.
"""

from kiln import fsm
from kiln.fsm import ACTIVE_STATES, Ctx, Event, EventKind, State, advance, table_actions

# --- fixtures -----------------------------------------------------------------

RESTING_THRESHOLDS = dict(rest_threshold=0.9, rest_wake=0.85)  # v1.2 defaults


def ctx(rest=0.0, **kw):
    return Ctx(needs={"rest": rest}, **{**RESTING_THRESHOLDS, **kw})


def ev(kind, payload=None):
    return Event(kind, payload)


# --- the state / event vocabulary --------------------------------------------


def test_states_are_the_v12_set_plus_cooling():
    assert {s.value for s in State} == {"idle", "thinking", "responding", "cooling", "resting"}


def test_active_states_exclude_resting():
    assert State.RESTING not in ACTIVE_STATES
    assert set(ACTIVE_STATES) == {State.IDLE, State.RESPONDING, State.THINKING, State.COOLING}


def test_event_kinds_include_the_live_five_and_reserved_three():
    kinds = {k.value for k in EventKind}
    assert {"user.message", "command", "self_trigger", "tick", "rotate.request"} <= kinds
    # reserved — defined but unused by the default table (land in 1.6 / 1.11 / 1.5)
    assert {"peer.message", "room.message", "tool.result"} <= kinds


# --- transitions from an ACTIVE state ----------------------------------------


def test_user_message_from_any_active_state_responds():
    for s in ACTIVE_STATES:
        assert advance(s, ev(EventKind.USER_MESSAGE, "hi"), ctx()) == ("respond", State.RESPONDING)


def test_connection_self_trigger_reaches_out():
    assert advance(State.IDLE, ev(EventKind.SELF_TRIGGER, "connection"), ctx()) == (
        "reach_out",
        State.RESPONDING,
    )


def test_reflection_self_trigger_thinks():
    assert advance(State.IDLE, ev(EventKind.SELF_TRIGGER, "reflection"), ctx()) == (
        "think",
        State.THINKING,
    )


def test_self_trigger_routes_by_configured_need_not_a_hardcoded_string():
    # retune reach_out_need → 'novelty'; the payload need follows and still routes to reach_out
    c = ctx(reach_out_need="novelty", reflect_need="rest")
    assert advance(State.IDLE, ev(EventKind.SELF_TRIGGER, "novelty"), c)[0] == "reach_out"
    assert advance(State.IDLE, ev(EventKind.SELF_TRIGGER, "rest"), c)[0] == "think"


def test_command_from_active_is_handled_and_stays_idle():
    assert advance(State.RESPONDING, ev(EventKind.COMMAND, "/status"), ctx()) == (
        "command",
        State.IDLE,
    )


def test_rotate_request_from_active():
    assert advance(State.IDLE, ev(EventKind.ROTATE), ctx()) == ("rotate", State.IDLE)


def test_idle_tick_stays_idle_when_rest_is_low():
    assert advance(State.IDLE, ev(EventKind.TICK), ctx(rest=0.2)) == ("idle", State.IDLE)


def test_tick_enters_rest_when_rest_at_or_above_threshold():
    assert advance(State.IDLE, ev(EventKind.TICK), ctx(rest=0.90)) == ("enter_rest", State.RESTING)
    assert advance(State.RESPONDING, ev(EventKind.TICK), ctx(rest=0.95)) == (
        "enter_rest",
        State.RESTING,
    )


# --- the rest gate (hysteresis) ----------------------------------------------


def test_resting_tick_wakes_when_rest_at_or_below_wake():
    assert advance(State.RESTING, ev(EventKind.TICK), ctx(rest=0.85)) == ("wake", State.IDLE)
    assert advance(State.RESTING, ev(EventKind.TICK), ctx(rest=0.5)) == ("wake", State.IDLE)


def test_resting_tick_stays_resting_inside_the_hysteresis_band():
    # between wake (0.85) and threshold (0.90): stays resting (won't re-wake, won't re-enter)
    assert advance(State.RESTING, ev(EventKind.TICK), ctx(rest=0.88)) == ("idle", State.RESTING)


def test_resting_user_message_is_heard_not_engaged():
    assert advance(State.RESTING, ev(EventKind.USER_MESSAGE, "hey"), ctx(rest=0.88)) == (
        "rest_ack",
        State.RESTING,
    )


def test_resting_command_still_works():
    assert advance(State.RESTING, ev(EventKind.COMMAND, "/status"), ctx(rest=0.88)) == (
        "command",
        State.RESTING,
    )


def test_resting_rotate_stays_resting():
    assert advance(State.RESTING, ev(EventKind.ROTATE), ctx(rest=0.88)) == ("rotate", State.RESTING)


# --- totality / registry contract --------------------------------------------


def test_advance_falls_back_to_idle_for_an_unmatched_event():
    # a reserved kind the default table doesn't handle → defensive (idle, same state)
    assert advance(State.IDLE, ev(EventKind.TOOL_RESULT), ctx()) == ("idle", State.IDLE)


def test_table_names_only_actions_the_registry_will_provide():
    # KILN-063 must resolve exactly these; keep this in lockstep with the registry's built-ins.
    expected = {
        "respond",
        "reach_out",
        "think",
        "idle",
        "enter_rest",
        "wake",
        "rotate",
        "command",
        "rest_ack",
    }
    assert table_actions() == expected


def test_table_targets_only_defined_states():
    for rule in fsm.DEFAULT_TABLE:
        assert rule.to in State
        assert all(s in State for s in rule.states)
