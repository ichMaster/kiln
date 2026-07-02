"""
kiln — the FSM core (v1.3, KILN-061): the agent's behaviour as an explicit, table-driven state
machine. This module is the **static model** — the states, the event vocabulary, the default
transition table, and a pure `advance` lookup. It is a leaf (imports nothing from kiln), wired into
the tick loop in KILN-064; the runtime event queue is KILN-062 and the action registry KILN-063.

The **default table reproduces v1.2 behaviour** (Agnika byte-for-byte). Guards are Python predicates
here; the per-agent YAML grammar is v1.9. The queue (KILN-062) delivers ONE event per tick in
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
    but unused by the default table — they land with their phases: `peer.message` (1.8),
    `room.message` (1.13), `tool.result` (1.7)."""

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
    cooldowns: dict[str, int] = field(default_factory=dict)  # TriggerBook.cooldown (for COOLING)

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


def _cooldowns_clear(e: Event, c: Ctx) -> bool:
    return all(v <= 0 for v in c.cooldowns.values())  # every per-need cooldown has ticked down


# The DEFAULT transition table — encodes v1.2's behaviour. Order matters only within a (state, kind)
# group: guarded rules precede the fall-through.
DEFAULT_TABLE: tuple[Rule, ...] = (
    # --- from an ACTIVE state (idle / responding / thinking / cooling) ---
    # A turn goes to COOLING (KILN-065): the turn tick still emits status "responding"/"thinking"
    # (the action sets it), but the *next* state is cooling — the post-turn window where per-need
    # cooldowns tick down before idle. The status the driver emits comes from the action, not `to`.
    Rule(ACTIVE_STATES, EventKind.COMMAND, "command", State.IDLE),
    Rule(ACTIVE_STATES, EventKind.USER_MESSAGE, "respond", State.COOLING),
    Rule(ACTIVE_STATES, EventKind.SELF_TRIGGER, "reach_out", State.COOLING, guard=_is_reach_out),
    Rule(ACTIVE_STATES, EventKind.SELF_TRIGGER, "think", State.COOLING, guard=_is_reflect),
    Rule(ACTIVE_STATES, EventKind.ROTATE, "rotate", State.IDLE),
    # tick from COOLING: recover while cooldowns are active (`cool`), then return to idle once they
    # clear (KILN-065). These precede the generic active-tick rows below (first match wins); the
    # other active states go to COOLING after a turn, so only IDLE reaches the generic rows.
    Rule((State.COOLING,), EventKind.TICK, "enter_rest", State.RESTING, guard=_should_rest),
    Rule((State.COOLING,), EventKind.TICK, "idle", State.IDLE, guard=_cooldowns_clear),
    Rule((State.COOLING,), EventKind.TICK, "cool", State.COOLING),
    Rule(ACTIVE_STATES, EventKind.TICK, "enter_rest", State.RESTING, guard=_should_rest),
    Rule(ACTIVE_STATES, EventKind.TICK, "idle", State.IDLE),
    # --- from RESTING (the rest gate: heard-but-not-engaged; recover on idle) ---
    Rule((State.RESTING,), EventKind.COMMAND, "command", State.RESTING),
    Rule((State.RESTING,), EventKind.USER_MESSAGE, "rest_ack", State.RESTING),
    Rule((State.RESTING,), EventKind.ROTATE, "rotate", State.RESTING),
    Rule((State.RESTING,), EventKind.TICK, "wake", State.IDLE, guard=_can_wake),
    Rule((State.RESTING,), EventKind.TICK, "enter_rest", State.RESTING),
)
# NB (KILN-064): the v1.2 rest gate is kept as a flag in run(), which flips out of RESTING before
# `advance` sees a wake-eligible tick — so `enter_rest` is the action that *stays* resting (it
# announces once on entering, then just recovers), and the `wake` row above is shadowed by the flag
# until the rest gate becomes fully FSM-owned (1.9). `enter_rest` handles both the entering tick
# (entered_rest → REST_MESSAGE) and every continuing one (idle satiation, status "resting").


def match(
    state: State, event: Event, ctx: Ctx, table: tuple[Rule, ...] = DEFAULT_TABLE
) -> Rule | None:
    """The rule `advance` selects — the first whose state + kind + guard all match, else None.
    Exposed so the trace (KILN-066) can name the guard that fired without re-doing the lookup."""
    for rule in table:
        if state in rule.states and event.kind == rule.kind and rule.guard(event, ctx):
            return rule
    return None


def advance(
    state: State, event: Event, ctx: Ctx, table: tuple[Rule, ...] = DEFAULT_TABLE
) -> tuple[Action, State]:
    """The pure FSM step: given the current `state`, an `event`, and a guard `ctx`, return
    `(action, next_state)` from the first matching rule in `table`. **No side effects** — the driver
    (KILN-064) fires the action through the registry (KILN-063). Falls back to `(idle, state)` if no
    rule matches (defensive; the default table is total for the real event set)."""
    rule = match(state, event, ctx, table)
    return ("idle", state) if rule is None else (rule.action, rule.to)


def table_actions(table: tuple[Rule, ...] = DEFAULT_TABLE) -> set[Action]:
    """The set of action names a table references — what the registry (KILN-063) must provide."""
    return {rule.action for rule in table}


# === Transition trace (KILN-066) =============================================
# A structured record of every FSM step, emitted through a sink the driver is handed (off by
# default; see engine.run's `trace`). The log **1.10 simulation** consumes (state histograms +
# transition heatmaps), so the shape is a pinned contract. Cheap when on (a dict/tick); no effect.

TRACE_KEYS = frozenset({"tick", "state", "event", "guard", "action", "next_state", "needs"})


def guard_name(rule: Rule | None) -> str | None:
    """The name of the guard that fired (`_should_rest`, `_is_reach_out`, …), or None for the
    always-true default guard / an unmatched event — for the trace record."""
    if rule is None:
        return None
    name = getattr(rule.guard, "__name__", None)
    return None if name in (None, "<lambda>") else name


def trace_record(
    tick: int,
    state: State,
    event: Event,
    action: Action,
    next_state: State,
    needs: dict[str, float],
    guard: str | None = None,
) -> dict:
    """The canonical transition-trace record (keys = `TRACE_KEYS`): the from-state, event, guard,
    action, and next_state, plus `tick` and a `needs` snapshot. One record per FSM step."""
    return {
        "tick": tick,
        "state": state.value if isinstance(state, State) else state,
        "event": event.kind.value if isinstance(event, Event) else event,
        "guard": guard,
        "action": action,
        "next_state": next_state.value if isinstance(next_state, State) else next_state,
        "needs": needs,
    }


# === Event queue + producers (KILN-062) ======================================
# The runtime event side. Producers turn today's sources into `Event`s (pure — they take the
# already-computed poll result / crossed need / rotate flag, not the engine objects); the queue
# drains ONE event per tick in the priority the table expects. The driver (KILN-064) calls these:
# it folds the `ServerChannel` inbox (via `channel.poll`) and the `rotate_results` queue into this
# one queue. Added alongside the loop — it does not yet drive it.

# Drain priority — reproduces v1.2's `if/elif`: input > self-trigger > rotate > tick. Lower wins.
EVENT_PRIORITY: dict[EventKind, int] = {
    EventKind.USER_MESSAGE: 0,
    EventKind.COMMAND: 0,  # input tier (a slash line still preempts a self-trigger, as today)
    EventKind.SELF_TRIGGER: 1,
    EventKind.ROTATE: 2,  # orthogonal in v1.2; only wins an otherwise-idle tick
    EventKind.TICK: 3,
}


class EventQueue:
    """The per-run event queue. Each tick the producers enqueue this tick's candidate events;
    `drain_one` pops the single highest-priority event (input > self-trigger > rotate > tick) —
    reproducing v1.2's one-action-per-tick selection. Non-winning per-tick candidates are dropped
    and recomputed next tick (the inbox / `rotate_results` remain the persistent async backing), so
    a self-trigger that loses to input is re-evaluated next tick, exactly as today."""

    def __init__(self) -> None:
        self._events: list[Event] = []

    def put(self, event: Event | None) -> None:
        """Enqueue a candidate (a `None` producer result is a no-op — a source that didn't fire)."""
        if event is not None:
            self._events.append(event)

    def drain_one(self) -> Event:
        """Pop the highest-priority event and clear the rest. Stable within a tier (insertion
        order). Defensive: an empty queue yields a `tick` — a tick always exists conceptually."""
        if not self._events:
            return Event(EventKind.TICK)
        # stable sort keeps insertion order within a priority tier
        self._events.sort(key=lambda e: EVENT_PRIORITY.get(e.kind, 99))
        winner = self._events[0]
        self._events.clear()
        return winner

    def pending(self) -> int:
        return len(self._events)


def input_event(user_msg: str | None) -> Event | None:
    """`channel.poll()` → a COMMAND event for a slash line, else a USER_MESSAGE (None if no input).
    `handle_command` still does the real dispatch; the kind only lets the table put input first."""
    if user_msg is None:
        return None
    kind = EventKind.COMMAND if user_msg.startswith("/") else EventKind.USER_MESSAGE
    return Event(kind, user_msg)


def self_trigger_event(reach_out_need: str | None, thought_need: str | None) -> Event | None:
    """The reach-out (`select_self_trigger`) or inner-thought (`select_thought_trigger`) crossing →
    a `self_trigger` event carrying the need. Reach-out precedes the thought (as v1.2 computes them:
    the thought only runs when no reach-out fired), so at most one self-trigger per tick."""
    need = reach_out_need if reach_out_need is not None else thought_need
    return Event(EventKind.SELF_TRIGGER, need) if need is not None else None


def rotate_event(do_rotate: bool) -> Event | None:
    """The auto-rotate timer or `/rotate` → a `rotate.request` (None when no rotation is due)."""
    return Event(EventKind.ROTATE) if do_rotate else None


def tick_event() -> Event:
    """The heartbeat — emitted every tick, the lowest-priority fall-through (v1.2's idle branch)."""
    return Event(EventKind.TICK)


def gather_events(
    queue: EventQueue,
    user_msg: str | None,
    reach_out_need: str | None,
    thought_need: str | None,
    do_rotate: bool,
) -> EventQueue:
    """Enqueue this tick's candidate events from today's sources (input, self-trigger, rotate, and
    always a tick). Draining the result yields the single event v1.2's `if/elif` would act on."""
    queue.put(input_event(user_msg))
    queue.put(self_trigger_event(reach_out_need, thought_need))
    queue.put(rotate_event(do_rotate))
    queue.put(tick_event())
    return queue
