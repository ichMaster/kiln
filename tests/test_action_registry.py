"""
KILN-063 — the action-registry seam (engine.py): `name -> callable`, `fire(action, ctx)`, the
built-ins wrapping today's inline branches. Contract: the registry resolves every action the FSM
table (KILN-061) names. Unit: each built-in produces today's effect against a MockBrain-backed turn
+ a recording Output — zero paid calls.
"""

from kiln import fsm
from kiln.brain import MockBrain
from kiln.engine import (
    ActionContext,
    ActionRegistry,
    State,
    default_registry,
    respond,
)

# --- a recording Output + a MockBrain-backed turn -----------------------------


class RecordingOutput:
    def __init__(self):
        self.calls = []

    def user(self, text):
        self.calls.append(("user", text))

    def agent(self, text, **kw):
        self.calls.append(("agent", text, kw))

    def usage(self, usage, latency=None):
        self.calls.append(("usage", usage))

    def notice(self, text):
        self.calls.append(("notice", text))

    def status(self, snapshot):
        self.calls.append(("status", snapshot))

    def kinds(self):
        return [c[0] for c in self.calls]


class _Stats:
    last_latency = 0.0


def _mock_turn(state):
    # mirrors run()'s _turn: respond() through MockBrain, plus the curiosity flag _turn adds
    def turn(prompt, force=None, agent=None):
        out = respond(prompt, state, [], "sys", MockBrain(), force=force, agent=agent)
        out["curiosity"] = False
        return out

    return turn


def _ctx(kind, payload=None, *, state=None, **kw):
    state = state or State(needs={})
    return ActionContext(
        state=state,
        event=fsm.Event(kind, payload),
        output=RecordingOutput(),
        stats=_Stats(),
        turn=_mock_turn(state),
        self_prompt=lambda need, reached: f"reach:{need}",
        **kw,
    )


# --- contract: the registry covers exactly the table's actions ----------------


def test_default_registry_resolves_every_action_the_table_names():
    reg = default_registry()
    for action in fsm.table_actions():
        assert reg.resolve(action) is not None, f"{action} not registered"


def test_default_registry_actions_equal_the_table_actions():
    # the seam is pinned to the FSM: no stray built-ins, none missing
    assert default_registry().actions() == fsm.table_actions()


def test_fire_unknown_action_raises():
    try:
        default_registry().fire("nope", _ctx(fsm.EventKind.TICK))
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_register_and_fire_a_custom_action():
    reg = ActionRegistry()
    seen = []
    reg.register("custom", lambda ctx: seen.append(ctx.event.kind))
    reg.fire("custom", _ctx(fsm.EventKind.TICK))
    assert seen == [fsm.EventKind.TICK]


# --- unit: each built-in maps to today's inline behaviour ---------------------


def test_idle_applies_idle_satiation():
    ctx = _ctx(fsm.EventKind.TICK, state=State(needs={"rest": 0.5}))
    default_registry().fire("idle", ctx)
    # the "idle" event recovers rest (SATIATION["idle"]["rest"] = -0.01); silent, stays idle
    assert ctx.status == "idle"
    assert ctx.output.calls == []
    assert ctx.state.needs["rest"] == 0.49


def test_respond_runs_a_turn_and_marks_responding():
    ctx = _ctx(fsm.EventKind.USER_MESSAGE, "привіт")
    out = default_registry().fire("respond", ctx)
    assert ctx.status == "responding"
    assert ctx.branch == out["class"]
    assert ctx.reached_out is False
    # echoes the user, prints the reply, folds usage
    assert ctx.output.kinds() == ["user", "agent", "usage"]


def test_reach_out_runs_the_reach_out_path_and_arms_anti_repeat():
    ctx = _ctx(fsm.EventKind.SELF_TRIGGER, "connection")
    out = default_registry().fire("reach_out", ctx)
    assert ctx.status == "responding"
    assert ctx.branch == out["class"]
    assert ctx.reached_out is True  # she spoke first; awaiting a reply
    # she speaks first: an is_self agent line (no user echo), then usage
    assert ctx.output.kinds() == ["agent", "usage"]
    assert ctx.output.calls[0][2].get("is_self") is True


def test_think_calls_the_thought_closure_and_is_silent():
    thought_ran = []
    ctx = _ctx(fsm.EventKind.SELF_TRIGGER, "reflection", think=lambda: thought_ran.append(True))
    default_registry().fire("think", ctx)
    assert thought_ran == [True]
    assert ctx.status == "thinking"
    assert ctx.output.calls == []  # nothing displayed


def test_enter_rest_announces_once_and_rests():
    ctx = _ctx(fsm.EventKind.TICK, entered_rest=True)
    default_registry().fire("enter_rest", ctx)
    assert ctx.status == "resting"
    assert ctx.output.kinds() == ["agent"]  # the REST_MESSAGE line
    assert ctx.output.calls[0][2].get("is_self") is True


def test_enter_rest_stays_silent_when_not_entering():
    ctx = _ctx(fsm.EventKind.TICK, entered_rest=False)
    default_registry().fire("enter_rest", ctx)
    assert ctx.status == "resting"
    assert ctx.output.calls == []  # already announced on entry; don't repeat


def test_rest_ack_echoes_the_user_but_does_not_call_the_brain():
    ctx = _ctx(fsm.EventKind.USER_MESSAGE, "ти тут?", entered_rest=False)
    default_registry().fire("rest_ack", ctx)
    assert ctx.status == "resting"
    assert ctx.reached_out is False
    assert ctx.output.kinds() == ["user"]  # heard (echoed), no agent turn


def test_wake_leaves_the_rest_gate():
    ctx = _ctx(fsm.EventKind.TICK)
    default_registry().fire("wake", ctx)
    assert ctx.resting is False
    assert ctx.status == "idle"


def test_rotate_requests_a_rotation():
    ctx = _ctx(fsm.EventKind.ROTATE)
    default_registry().fire("rotate", ctx)
    assert ctx.do_rotate is True
    assert ctx.status == "idle"


# --- command dispatch → the outcome the driver reads back --------------------


def _command_ctx(code):
    return _ctx(fsm.EventKind.COMMAND, "/x", handle_command=lambda: code)


def test_command_quit_sets_control():
    ctx = _command_ctx("quit")
    default_registry().fire("command", ctx)
    assert ctx.control == "quit"
    assert ("notice", "[exit] exit by command") in ctx.output.calls


def test_command_reload_sets_control():
    ctx = _command_ctx("reload")
    default_registry().fire("command", ctx)
    assert ctx.control == "reload"


def test_command_rotate_requests_rotation():
    ctx = _command_ctx("rotate")
    default_registry().fire("command", ctx)
    assert ctx.do_rotate is True


def test_command_handled_is_a_noop_turn():
    ctx = _command_ctx("handled")
    default_registry().fire("command", ctx)
    assert ctx.status == "idle"
    assert ctx.control is None
    assert ctx.output.calls == []


def test_command_tuple_return_is_ignored_after_ask_removed():
    """v1.4: /ask is gone, so `_act_command` no longer runs a forced-deep turn from a command
    code — an unexpected tuple is simply not acted on (no brain call, no output)."""
    ctx = _command_ctx(("ask", "поясни рекурсію"))
    default_registry().fire("command", ctx)
    assert ctx.status == "idle"
    assert ctx.output.calls == []
