"""
kiln — the action registry (KILN-063): the seam the FSM fires actions through — `name -> callable`
with `fire(action, ctx)`. The built-ins wrap the loop's inline branches; each reads/writes an
`ActionContext` — the run-loop handles it needs. The turn / think / self-prompt / command primitives
are INJECTED as callables (run-loop closures over history/prompts/brain), so the built-ins stay thin
and testable; the `needs` helpers (`apply_satiation`, `reach_out_branch`) are called directly. This
is the exact seam **1.5 Tools** extends with user tools (an agent's scope gates which it may fire).
The loop (`engine.run`) builds the `ActionContext` and drives this via `fsm.advance` + `fire` (1.3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import fsm
from .config import REST_MESSAGE, AgentConfig
from .needs import State, apply_satiation, reach_out_branch
from .output import Output
from .stats import SessionStats


@dataclass
class ActionContext:
    """What an FSM action reads and writes for one tick. Injected primitives keep the built-ins
    thin; the outcome fields (`status` / `branch` / `reached_out` / `do_rotate` / `control`) are
    set by the action and read back by the driver (KILN-064)."""

    state: State
    event: fsm.Event
    output: Output
    stats: SessionStats
    turn: Callable[..., dict]  # _turn(prompt, force=None, agent=None) -> out
    think: Callable[[], object] = lambda: None  # _think()
    self_prompt: Callable[[str, bool], str] = lambda need, reached: ""  # _self_prompt(...)
    handle_command: Callable[[], object] = lambda: None  # dispatch the current user_msg
    config: AgentConfig | None = None
    reached_out: bool = False  # she self-initiated and awaits a reply (anti-repeat)
    entered_rest: bool = False  # first tick of a rest spell → say REST_MESSAGE once
    resting: bool = False  # the rest-gate flag this tick (so a command action can defer to rest)
    # --- outcome (set by the action, read back by the driver) ---
    status: str = "idle"
    branch: str | None = None
    do_rotate: bool = False
    control: str | None = None  # "quit" | "reload" | None (loop control from a command)


def _act_idle(ctx: ActionContext) -> None:
    """A silent tick: rest + cool down (v1.2's `else` branch). Not printed — check via `/status`."""
    apply_satiation(ctx.state, "idle", ctx.config)
    ctx.status = "idle"


def _act_respond(ctx: ActionContext) -> dict:
    """A normal user turn: run the brain, echo the user + the reply, fold usage, mark responding."""
    msg = ctx.event.payload
    out = ctx.turn(msg)
    ctx.output.user(msg)
    ctx.output.agent(out["reply"], model=out["route"].split("/")[-1], is_curiosity=out["curiosity"])
    ctx.output.usage(out.get("usage"), ctx.stats.last_latency)
    ctx.status, ctx.branch = "responding", out["class"]
    ctx.reached_out = False  # the user replied
    return out


def _act_reach_out(ctx: ActionContext) -> dict:
    """A connection reach-out: her other needs pick the brain (`reach_out_branch`); she speaks first
    (`is_self`), and `reached_out` arms the anti-repeat for the next one."""
    prompt = ctx.self_prompt(ctx.event.payload, ctx.reached_out)
    faction, agent = reach_out_branch(ctx.state, ctx.config)
    out = ctx.turn(prompt, force=faction, agent=agent)
    ctx.output.agent(
        out["reply"], is_self=True, model=out["route"].split("/")[-1], is_curiosity=out["curiosity"]
    )
    ctx.output.usage(out.get("usage"), ctx.stats.last_latency)
    ctx.status, ctx.branch = "responding", out["class"]
    ctx.reached_out = True  # awaiting a reply; the next reach-out acknowledges the silence
    return out


def _act_think(ctx: ActionContext) -> None:
    """A private inner thought (Haiku) — stored hidden, discharges reflection; nothing displayed."""
    ctx.think()
    ctx.status = "thinking"


def _act_enter_rest(ctx: ActionContext) -> None:
    """Too tired to engage: recover (idle satiation); announce REST_MESSAGE once on entering."""
    if ctx.entered_rest:
        ctx.output.agent(REST_MESSAGE, is_self=True)
    apply_satiation(ctx.state, "idle", ctx.config)
    ctx.status = "resting"


def _act_rest_ack(ctx: ActionContext) -> None:
    """Someone wrote while she rests: heard, but don't call the brain — echo them, rest line once.
    (v1.2's `elif resting` input path.)"""
    ctx.output.user(ctx.event.payload)
    if ctx.entered_rest:
        ctx.output.agent(REST_MESSAGE, is_self=True)
    apply_satiation(ctx.state, "idle", ctx.config)
    ctx.status = "resting"
    ctx.reached_out = False  # the user replied (even while she rests)


def _act_wake(ctx: ActionContext) -> None:
    """Rest fell back to REST_WAKE: leave the rest gate and recover this tick (idle satiation)."""
    ctx.resting = False
    apply_satiation(ctx.state, "idle", ctx.config)
    ctx.status = "idle"


def _act_cool(ctx: ActionContext) -> None:
    """Post-turn cooling (KILN-065): recover (idle satiation, exactly the old post-turn idle tick)
    while the per-need cooldowns tick down; surfaced as status "cooling" until they clear."""
    apply_satiation(ctx.state, "idle", ctx.config)
    ctx.status = "cooling"


def _act_rotate(ctx: ActionContext) -> None:
    """Request a session rotation; the driver's rotation block performs it (orthogonal, as v1.2)."""
    ctx.do_rotate = True
    ctx.status = "idle"


def _act_command(ctx: ActionContext) -> None:
    """A slash command: dispatch it and translate the code into the outcome the driver reads back —
    quit/reload → `control`, rotate → `do_rotate`, `("ask", text)` → a forced-deep turn."""
    ctx.status = "idle"
    code = ctx.handle_command()
    if code == "quit":
        ctx.output.notice("[exit] exit by command")
        ctx.control = "quit"
    elif code == "reload":
        ctx.control = "reload"
    elif code == "rotate":
        ctx.do_rotate = True
    elif code == "handled":
        pass
    elif isinstance(code, tuple):  # ("ask", text) -> forced deep
        if ctx.resting:
            _act_rest_ack(ctx)  # too tired to engage even /ask: heard, no brain call (v1.2)
            return
        out = ctx.turn(code[1], force="deep")
        ctx.output.agent(
            out["reply"],
            lead=True,
            model=out["route"].split("/")[-1],
            is_curiosity=out["curiosity"],
        )
        ctx.output.usage(out.get("usage"), ctx.stats.last_latency)
        ctx.status, ctx.branch = "responding", out["class"]
        ctx.reached_out = False


# Built-in action name -> callable. The keys are exactly `fsm.table_actions()` (pinned by a contract
# test); 1.5 adds user tools to a registry seeded from this map.
_BUILTIN_ACTIONS: dict[str, Callable[[ActionContext], object]] = {
    "idle": _act_idle,
    "respond": _act_respond,
    "reach_out": _act_reach_out,
    "think": _act_think,
    "enter_rest": _act_enter_rest,
    "rest_ack": _act_rest_ack,
    "wake": _act_wake,
    "cool": _act_cool,
    "rotate": _act_rotate,
    "command": _act_command,
}


class ActionRegistry:
    """`name -> callable(ctx)` + `fire(action, ctx)`. The FSM fires actions = tools through this
    seam; 1.5 Tools extends the same registry (an agent's scope gates which actions it may fire)."""

    def __init__(self) -> None:
        self._actions: dict[str, Callable[[ActionContext], object]] = {}

    def register(self, name: str, fn: Callable[[ActionContext], object]) -> None:
        self._actions[name] = fn

    def resolve(self, name: str) -> Callable[[ActionContext], object] | None:
        return self._actions.get(name)

    def fire(self, action: str, ctx: ActionContext) -> object:
        fn = self._actions.get(action)
        if fn is None:
            raise KeyError(f"no action registered: {action!r}")
        return fn(ctx)

    def actions(self) -> set[str]:
        return set(self._actions)


def default_registry() -> ActionRegistry:
    """A registry with every built-in action registered — covers `fsm.table_actions()`."""
    reg = ActionRegistry()
    for name, fn in _BUILTIN_ACTIONS.items():
        reg.register(name, fn)
    return reg
