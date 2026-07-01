"""
kiln — the FSM core (v1.3, KILN-061): the agent's behaviour as an explicit, table-driven state
machine. This module is the **static model** — the states, the event vocabulary, the default
transition table, and a pure `advance` lookup. It is a leaf (imports nothing from kiln), wired into
the tick loop in KILN-064; the runtime event queue is KILN-062 and the action registry KILN-063.

The **default table reproduces v1.2 behaviour** (Agnika byte-for-byte). Guards are Python predicates
here; the per-agent YAML grammar is v1.7. The queue (KILN-062) delivers ONE event per tick in
priority order (input > self-trigger > tick), so the table maps a single event — it does NOT
re-encode priority. See spec/features/fsm.md for the concept.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    """The agent's FSM states (also the snapshot `status`). `cooling` is defined here but **unused
    by the default table** until KILN-065 — v1.2 has no cooling state, so 1.3's gate stays
    behaviour-preserving."""

    IDLE = "idle"
    THINKING = "thinking"
    RESPONDING = "responding"
    COOLING = "cooling"
    RESTING = "resting"


# The machine reacts identically from any of these (a turn / reach-out / thought / rest / idle can
# follow any of them). RESTING is the one state that behaves differently — the rest gate.
ACTIVE_STATES: tuple[State, ...] = (State.IDLE, State.RESPONDING, State.THINKING, State.COOLING)


class EventKind(str, Enum):
    """The event vocabulary drained from the queue (KILN-062). The **reserved** kinds are defined
    but unused by the default table — they land with their phases: `peer.message` (1.6),
    `room.message` (1.11), `tool.result` (1.5)."""

    USER_MESSAGE = "user.message"
    COMMAND = "command"
    SELF_TRIGGER = "self_trigger"  # payload = the need name (connection / reflection / …)
    TICK = "tick"
    ROTATE = "rotate.request"
    # reserved (defined, unused this phase)
    PEER_MESSAGE = "peer.message"
    ROOM_MESSAGE = "room.message"
    TOOL_RESULT = "tool.result"


@dataclass(frozen=True)
class Event:
    """One event: a `kind` + an optional `payload` (the text for user.message/command, the need name
    for a self_trigger)."""

    kind: EventKind
    payload: object = None


@dataclass(frozen=True)
class Ctx:
    """What a guard reads: the live need **levels** + the agent's need-model wiring (rest-gate
    thresholds and which need is the reach-out vs the reflect trigger). Tiny + pure, so `advance` is
    unit-testable in isolation. The `*_need` names are configurable (REACH_OUT_NEED / REFLECT_NEED),
    so a `self_trigger` routes by comparing its payload need against these — not a fixed string."""

    needs: dict[str, float] = field(default_factory=dict)
    rest_threshold: float = 1.1  # NEED_TRIGGERS["rest"]["threshold"] (>1 → never rests)
    rest_wake: float = 0.85  # REST_WAKE
    reach_out_need: str = "connection"  # REACH_OUT_NEED — its self_trigger → reach_out
    reflect_need: str = "reflection"  # REFLECT_NEED — its self_trigger → think

    @property
    def rest(self) -> float:
        return self.needs.get("rest", 0.0)


# Actions the FSM fires (resolved by the registry, KILN-063). `respond` routes chat/deep internally
# (v1.2 `classify`); `reach_out` shapes a connection reach-out; `chat`/`deep` are the brain-level
# sub-tools those use. Kept as strings here — names the table references and the registry provides.
Action = str


@dataclass(frozen=True)
class Rule:
    """One transition: from any of `states`, on `kind` (when `guard` holds), fire `action` and go
    `to`. Rules are tried in order; the first whose state + kind + guard match wins."""

    states: tuple[State, ...]
    kind: EventKind
    action: Action
    to: State
    guard: Callable[[Event, Ctx], bool] = lambda e, c: True


def _should_rest(e: Event, c: Ctx) -> bool:
    return c.rest >= c.rest_threshold  # enter the rest gate


def _can_wake(e: Event, c: Ctx) -> bool:
    return c.rest <= c.rest_wake  # leave it (hysteresis band between wake and threshold)


def _is_reach_out(e: Event, c: Ctx) -> bool:
    return e.payload == c.reach_out_need  # the reach-out need crossed


def _is_reflect(e: Event, c: Ctx) -> bool:
    return e.payload == c.reflect_need  # the inner-thought need crossed


# The DEFAULT transition table — encodes v1.2's behaviour. Order matters only within a (state, kind)
# group: guarded rules precede the fall-through.
DEFAULT_TABLE: tuple[Rule, ...] = (
    # --- from an ACTIVE state (idle / responding / thinking / cooling) ---
    Rule(ACTIVE_STATES, EventKind.COMMAND, "command", State.IDLE),
    Rule(ACTIVE_STATES, EventKind.USER_MESSAGE, "respond", State.RESPONDING),
    Rule(ACTIVE_STATES, EventKind.SELF_TRIGGER, "reach_out", State.RESPONDING, guard=_is_reach_out),
    Rule(ACTIVE_STATES, EventKind.SELF_TRIGGER, "think", State.THINKING, guard=_is_reflect),
    Rule(ACTIVE_STATES, EventKind.ROTATE, "rotate", State.IDLE),
    Rule(ACTIVE_STATES, EventKind.TICK, "enter_rest", State.RESTING, guard=_should_rest),
    Rule(ACTIVE_STATES, EventKind.TICK, "idle", State.IDLE),
    # --- from RESTING (the rest gate: heard-but-not-engaged; recover on idle) ---
    Rule((State.RESTING,), EventKind.COMMAND, "command", State.RESTING),
    Rule((State.RESTING,), EventKind.USER_MESSAGE, "rest_ack", State.RESTING),
    Rule((State.RESTING,), EventKind.ROTATE, "rotate", State.RESTING),
    Rule((State.RESTING,), EventKind.TICK, "wake", State.IDLE, guard=_can_wake),
    Rule((State.RESTING,), EventKind.TICK, "idle", State.RESTING),
)


def advance(
    state: State, event: Event, ctx: Ctx, table: tuple[Rule, ...] = DEFAULT_TABLE
) -> tuple[Action, State]:
    """The pure FSM step: given the current `state`, an `event`, and a guard `ctx`, return
    `(action, next_state)` from the first matching rule in `table`. **No side effects** — the driver
    (KILN-064) fires the action through the registry (KILN-063). Falls back to `(idle, state)` if no
    rule matches (defensive; the default table is total for the real event set)."""
    for rule in table:
        if state in rule.states and event.kind == rule.kind and rule.guard(event, ctx):
            return rule.action, rule.to
    return "idle", state


def table_actions(table: tuple[Rule, ...] = DEFAULT_TABLE) -> set[Action]:
    """The set of action names a table references — what the registry (KILN-063) must provide."""
    return {rule.action for rule in table}
