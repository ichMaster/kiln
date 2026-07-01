"""
kiln — a simple chat engine with two "brains".

Two reply branches:
  - CHAT  -> Haiku via a plain (HTTP) API. Cheap, fast, no tools.
            This is ordinary conversation: greetings, small talk, light replies.
  - THINK / TOOLS -> Claude as an external process (`claude -p`). Here you can
            specify the model (Opus), allowed tools, and skills. More expensive,
            but with reasoning and access to tools.

The engine runs a loop of short ticks. Each tick is cheap: needs drift on their
own, with no calls to the model. The brain only switches on under a condition
(user input or a need crossing its threshold), and then the turn is CLASSIFIED
into one of the branches.

Both brains sit behind the brain.Brain seam (LiveBrain — real SDK/CLI calls;
MockBrain — for dry-run and tests), so the core (respond/run) depends neither on the
SDK nor on the CLI directly. The rest of the code lives in modules: config (constants),
history (transcript), usage (logging/colors), memory (long-term memory + transcripts),
commands (slash commands).
"""

from __future__ import annotations

import datetime as _dt
import json
import queue
import random
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import fsm
from .brain import Brain, LiveBrain, MockBrain
from .commands import handle_command
from .config import (
    AGENT_NAME,
    BIORHYTHM,
    CHAT_MODEL,
    DEEP_MODEL,
    DRIFT,
    MOOD_AWARENESS,
    NEED_TRIGGERS,
    NEEDS_LEVELS_FILE,
    REACH_OUT_MODELS,
    REACH_OUT_NEED,
    RECENT_MESSAGES,
    REFLECT_NEED,
    REST_MESSAGE,
    REST_WAKE,
    ROTATE_EVERY_HOURS,
    SATIATION,
    SELF_COOLDOWN,
    SELF_SILENCE_NOTE,
    STORE_FILE,
    THINK_HINTS,
    THINK_THRESHOLD,
    THOUGHT_COOLDOWN,
    THOUGHT_VISIBLE_EVERY,
    THOUGHTS_ENABLED,
    THOUGHTS_IN_PROMPT,
    TICK_SECONDS,
    TIMEZONE,
    TOOL_HINTS,
    USAGE_REPORT,
    USER_LOCATION,
    WORLD_AWARENESS,
    AgentConfig,
    AgentPaths,
)
from .history import ROLE_BOT, ROLE_USER, strip_leading_name, turn
from .ledger import append_session, read_ledger
from .memory import (
    build_system,
    digest_facts,
    extract_facts,
    load_birth,
    load_canon,
    load_memory,
    load_prompts,
    pick_prompt,
    prune_history,
    summarize,
    thoughts_block,
)
from .mood import biorhythm, mood_block
from .output import ConsoleOutput, Output
from .report import write_report
from .stats import SessionStats
from .store import add_facts, add_thought, load_store, remove_session, save_store, upsert_session
from .world import world_block

# === State ==================================================================


@dataclass
class State:
    needs: dict[str, float] = field(default_factory=dict)
    self_messages: bool = True  # proactive reach-outs on? (/self toggle; per-session, not saved)

    @property
    def intensity(self) -> float:
        return self.needs.get("intensity", 0.0)

    @property
    def connection(self) -> float:
        return self.needs.get("connection", 0.0)

    def hottest_need(self) -> tuple[str, float]:
        if not self.needs:
            return ("", 0.0)
        k = max(self.needs, key=self.needs.get)
        return (k, self.needs[k])


def load_state(path: Path = NEEDS_LEVELS_FILE) -> State:
    """Reads the live need LEVELS from .kiln/needs.json ({need: level}); empty state if the file is
    missing. Any configured need (a `DRIFT` key) absent from the file is healed in at 0.0 — so a new
    need (e.g. v0.10 `reflection`, v0.11 `curiosity`) appears in the TUI/commands and drifts, no
    re-seed. (The need MODEL — drift/satiation/triggers — is separate: state/needs_model.yaml.)"""
    if not path.exists():
        return State()
    data = json.loads(path.read_text(encoding="utf-8"))
    needs = {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}
    for need in DRIFT:  # heal: a configured need missing from the file starts at 0.0
        needs.setdefault(need, 0.0)
    return State(needs=needs)


def save_state(state: State, path: Path = NEEDS_LEVELS_FILE) -> None:
    """Writes the live need LEVELS to .kiln/needs.json ({need: level}, rounded to 3 decimals)."""
    data = {k: round(v, 3) for k, v in state.needs.items()}
    path.parent.mkdir(parents=True, exist_ok=True)  # .kiln[/{id}] may not exist on a fresh run
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# === Tick ===================================================================


def drift(state: State, ticks: int = 1, config: AgentConfig | None = None) -> None:
    """Each need grows by DRIFT[k] × ticks (clamped to 1.0).
    ticks > 1 — "catch-up" drift for real time that elapsed during a
    blocking model call (see the loop in run).
    `config` (v1.2): the per-agent need model; None → the module global DRIFT (the agnika default,
    still monkeypatchable in tests). run() threads its config; direct callers omit it."""
    dmap = config.drift if config is not None else DRIFT
    for k in state.needs:
        state.needs[k] = min(1.0, state.needs[k] + dmap.get(k, 0.0) * ticks)


def apply_satiation(state: State, event: str, config: AgentConfig | None = None) -> None:
    """Closes needs per the event ('chat' | 'deep' | 'idle'), clamping at 0.
    `config` (v1.2): the per-agent satiation map; None → the module global SATIATION."""
    sat = config.satiation if config is not None else SATIATION
    for k, delta in sat.get(event, {}).items():
        if k in state.needs:
            state.needs[k] = max(0.0, state.needs[k] + delta)


@dataclass
class TriggerBook:
    """Runtime trigger state (not persisted): hysteresis + per-need cooldown."""

    armed: dict[str, bool] = field(default_factory=dict)  # ready to fire?
    cooldown: dict[str, int] = field(default_factory=dict)  # silent ticks remaining
    curiosity_monitor: bool = False  # v0.11: ON between a curiosity crossing and falling below it


def _crossing_trigger(
    state: State, tg: TriggerBook, name: str, cooldown: int, config: AgentConfig | None = None
) -> str | None:
    """Fire `name` on an UPWARD threshold crossing (`NEED_TRIGGERS`) with hysteresis — fires only on
    the up-crossing, re-arms once it falls back below — and a `cooldown` of silent ticks after.
    Returns the need name when it fires this tick, else None.
    `config` (v1.2): the per-agent triggers; None → the module global NEED_TRIGGERS."""
    triggers = config.need_triggers if config is not None else NEED_TRIGGERS
    cfg = triggers.get(name)
    if cfg is None:
        return None
    if tg.cooldown.get(name, 0) > 0:
        tg.cooldown[name] -= 1
    if state.needs.get(name, 0.0) < cfg["threshold"]:
        tg.armed[name] = True  # re-arm below threshold
        return None
    if not tg.armed.get(name, True) or tg.cooldown.get(name, 0) != 0:
        return None  # already discharged this crossing, or still cooling down
    tg.armed[name] = False  # discharge hysteresis
    tg.cooldown[name] = cooldown  # start cooldown
    return name


def select_self_trigger(
    state: State, tg: TriggerBook, config: AgentConfig | None = None
) -> str | None:
    """The proactive reach-out fires ONLY on REACH_OUT_NEED (connection = loneliness): returns that
    need name when it crosses its threshold this tick, else None. WHICH brain answers is a separate
    choice (reach_out_branch). Hysteresis + SELF_COOLDOWN silent ticks after firing.
    `config` (v1.2): the per-agent reach-out need + cooldown; None → the module globals."""
    reach = config.reach_out_need if config is not None else REACH_OUT_NEED
    cooldown = config.self_cooldown if config is not None else SELF_COOLDOWN
    return _crossing_trigger(state, tg, reach, cooldown, config)


def _thought_visible(every: int) -> bool:
    """Whether a freshly formed thought surfaces in the chat — ~1/`every` via the stdlib RNG
    (seedable / monkeypatchable in tests). `every <= 0` → never shown."""
    return every > 0 and random.random() < (1.0 / every)


def select_thought_trigger(
    state: State, tg: TriggerBook, config: AgentConfig | None = None
) -> str | None:
    """The inner monologue fires on REFLECT_NEED (reflection = незібраність): returns it on an
    upward crossing this tick (else None), with the same hysteresis + THOUGHT_COOLDOWN as the
    reach-out. The thought itself (KILN-042) is generated separately — this only decides WHEN.
    `config` (v1.2): the per-agent reflect need + cooldown; None → the module globals."""
    reflect = config.reflect_need if config is not None else REFLECT_NEED
    cooldown = config.thought_cooldown if config is not None else THOUGHT_COOLDOWN
    return _crossing_trigger(state, tg, reflect, cooldown, config)


def update_curiosity_monitor(
    state: State, tg: TriggerBook, config: AgentConfig | None = None
) -> bool:
    """v0.11: curiosity's "trigger" — an upward crossing of its threshold **enables the monitor**
    (`tg.curiosity_monitor`); falling back below disables it. Unlike a reach-out/thought it sends
    nothing — it just gates whether a `?` reply discharges curiosity (the monitor, in `_turn`).
    Run every tick (after drift). Returns the monitor state. No `NEED_TRIGGERS` entry → off.
    `config` (v1.2): the per-agent triggers; None → the module global NEED_TRIGGERS."""
    triggers = config.need_triggers if config is not None else NEED_TRIGGERS
    cfg = triggers.get("curiosity")
    if cfg is None:
        tg.curiosity_monitor = False
        return False
    if _crossing_trigger(
        state, tg, "curiosity", 0, config
    ):  # an upward crossing -> arm the monitor
        tg.curiosity_monitor = True
    elif state.needs.get("curiosity", 0.0) < cfg["threshold"]:  # fell below -> disarm
        tg.curiosity_monitor = False
    return tg.curiosity_monitor


def reach_out_branch(state: State, config: AgentConfig | None = None) -> tuple[str, str | None]:
    """
    WHICH brain answers a connection reach-out, shaped by her OTHER needs at fire time:
    the first REACH_OUT_MODELS need over its threshold wins (intensity -> deep/opus, novelty
    -> session-wiki), else the reach-out need's baseline (chat). So opus/session-wiki never
    self-INITIATE — they only shape a connection-driven message. Returns (action, agent).
    `config` (v1.2): the per-agent reach-out models/triggers/need; None → the module globals.
    """
    models = config.reach_out_models if config is not None else REACH_OUT_MODELS
    triggers = config.need_triggers if config is not None else NEED_TRIGGERS
    reach = config.reach_out_need if config is not None else REACH_OUT_NEED
    for name in models:
        cfg = triggers.get(name, {})
        if state.needs.get(name, 0.0) >= cfg.get("threshold", 2.0):
            return cfg.get("action", "chat"), cfg.get("agent")
    base = triggers.get(reach, {})
    return base.get("action", "chat"), base.get("agent")


# === Turn classification ====================================================


def turn_weight(state: State) -> float:
    """Turn weight ~0..1 from state. Higher -> closer to deep reasoning."""
    return max(0.0, min(1.0, 0.55 * state.intensity + 0.45 * state.connection))


def classify(
    prompt: str, state: State, config: AgentConfig | None = None
) -> tuple[str, str | None]:
    """
    Route a USER turn to (class, agent), class ∈ 'chat'|'think'|'tools'|'tool'.

    Priority:
      1. explicit tool markers -> 'tools' (deep + --allowedTools);
      2. explicit reasoning markers -> 'think';
      3. ambient HIGH NEEDS pick the model the same way a self-trigger does
         (reach_out_branch / REACH_OUT_MODELS): intensity over its threshold -> 'deep'
         (opus); else novelty over its -> 'tool' (session-wiki). So when she's intense or
         curious, even a plain user turn gets the deeper brain, not cheap chat;
      4. a high state weight -> 'think';
      5. otherwise -> 'chat'.

    `config` (v1.2): the per-agent reach-out models / triggers / think-threshold; None → the module
    globals. The TOOL_HINTS / THINK_HINTS markers stay global (persona-layer Ukrainian words).
    """
    models = config.reach_out_models if config is not None else REACH_OUT_MODELS
    triggers = config.need_triggers if config is not None else NEED_TRIGGERS
    threshold = config.think_threshold if config is not None else THINK_THRESHOLD
    low = prompt.lower()
    if any(h in low for h in TOOL_HINTS):
        return "tools", None
    if any(h in low for h in THINK_HINTS):
        return "think", None
    for name in models:  # high need -> deeper brain (same map as reach_out_branch)
        cfg = triggers.get(name, {})
        if state.needs.get(name, 0.0) >= cfg.get("threshold", 2.0):
            return cfg.get("action", "chat"), cfg.get("agent")
    if turn_weight(state) >= threshold:
        return "think", None
    return "chat", None


# === Input channel ==========================================================
# Simple input channel: ticks run continuously, while user messages arrive
# asynchronously and are picked up on the next tick.


class ScriptedChannel:
    """Deterministic channel for tests: input is keyed to tick numbers."""

    def __init__(self, inputs: dict[int, str] | None = None):
        self._inputs = inputs or {}
        self._tick = -1

    def poll(self) -> str | None:
        self._tick += 1
        return self._inputs.get(self._tick)


class StdinChannel:
    """
    Live channel: a background daemon thread reads stdin and queues lines.
    `poll()` non-blockingly pulls the next line (or None if empty), so the
    tick loop never stalls waiting for input.
    """

    def __init__(self):
        self._q: queue.Queue[str] = queue.Queue()
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def _reader(self) -> None:
        while True:
            line = sys.stdin.readline()
            if line == "":  # EOF
                break
            line = line.strip()
            if line:
                self._q.put(line)

    def poll(self) -> str | None:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None


# === Engine (loop) ==========================================================


# v0.11 curiosity: she "asks" when the reply carries a question — a `?` or a leading Ukrainian
# interrogative as its first word. Pure heuristic (a model judge may refine it later); it gates the
# curiosity discharge (she acted on the nudge -> sated).
_QUESTION_WORDS = frozenset(
    {
        "чому",
        "що",
        "як",
        "коли",
        "де",
        "хто",
        "навіщо",
        "чи",
        "чим",
        "кого",
        "кому",
        "який",
        "яка",
        "яке",
        "які",
        "скільки",
        "куди",
        "звідки",
    }
)


def is_curiosity_reply(text: str) -> bool:
    """True when a reply actually ASKS — it contains a question mark, or its first word is a
    Ukrainian interrogative. Pure; used to discharge curiosity (KILN-047)."""
    if "?" in text:
        return True
    words = text.lstrip().lower().split(maxsplit=1)
    first = words[0].strip(".,!?;:—-«»\"'") if words else ""
    return first in _QUESTION_WORDS


def respond(
    prompt: str,
    state: State,
    history: list[dict],
    system: str,
    brain: Brain,
    force: str | None = None,
    agent: str | None = None,
    config: AgentConfig | None = None,
) -> dict:
    # force ("chat"|"deep"|"tool") picks the branch directly (for self-triggers and /ask),
    # otherwise classify() routes the user turn (and may name the "tool" sub-agent). We call
    # the model ONLY through brain (seam): the core knows nothing about the SDK or the CLI.
    # `config` (v1.2): the per-agent models + satiation; None → the module globals.
    chat_model = config.chat_model if config is not None else CHAT_MODEL
    deep_model = config.deep_model if config is not None else DEEP_MODEL
    sat = config.satiation if config is not None else SATIATION
    name = (
        config.agent_name if config is not None else AGENT_NAME
    )  # strip THIS agent's echoed label
    if force:
        cls = force
    else:
        cls, agent = classify(prompt, state, config)

    # The user's current turn goes into the shared history before the call (timestamped, v0.8).
    history.append(turn(ROLE_USER, prompt))

    if cls == "chat":
        reply, usage = brain.chat(history, system)
        route = f"CHAT/{chat_model.split('-')[1]}"  # e.g. CHAT/haiku
        event = "chat"
    elif cls == "tool":
        # A "tool" self-trigger runs a named Claude Code sub-agent (e.g. novelty ->
        # session-wiki, an external Wikipedia fact). Satiation is PER-AGENT: if SATIATION has
        # an entry keyed by the agent name it's used (so session-wiki drops novelty on its
        # own terms), else it falls back to a deep "filling meal". NB: distinct from the
        # "tools" class below (deep + --allowedTools); here the whole turn is a sub-agent.
        reply, usage = brain.tool(agent or "", history, system)
        route = f"TOOL/{agent}"  # e.g. TOOL/session-wiki (the agent IS the trace label)
        event = agent if agent in sat else "deep"
    elif cls in ("think", "deep"):
        reply, usage = brain.deep(prompt, history, system, with_tools=False)
        route = f"THINK/{deep_model.split('-')[1]}"  # e.g. THINK/opus
        event = "deep"
    else:  # tools
        reply, usage = brain.deep(prompt, history, system, with_tools=True)
        route = f"TOOLS/{deep_model.split('-')[1]}"
        event = "deep"

    # Strip a leading name the model echoed (it mirrors the timeline's "Name:" labels) — clean
    # for both display and storage, so it never shows and never compounds in the next timeline.
    reply = strip_leading_name(reply, name)
    # The reply goes into the history too (timestamped, v0.8).
    history.append(turn(ROLE_BOT, reply))

    # The branch determines which needs were closed.
    apply_satiation(state, event, config)
    return {"class": cls, "route": route, "reply": reply, "usage": usage}


def _status_snapshot(
    status: str,
    state: State,
    tg: TriggerBook,
    stats: SessionStats,
    branch: str | None,
    tick: int,
    config: AgentConfig | None = None,
) -> dict:
    """
    Build the per-tick status snapshot the TUI status bar / needs panel render from.
    status ∈ idle/thinking/responding; branch is the last turn class (chat/think/tools);
    tick is the real elapsed-tick count since session start (catch-up included, so time
    spent in a blocking model call is counted, not just loop iterations).
    `config` (v1.2): the per-agent triggers + models; None → the module globals.
    """
    triggers = config.need_triggers if config is not None else NEED_TRIGGERS
    chat_model = config.chat_model if config is not None else CHAT_MODEL
    deep_model = config.deep_model if config is not None else DEEP_MODEL
    thresholds = {name: cfg["threshold"] for name, cfg in triggers.items()}
    # what addresses each need (the self-trigger branch)
    actions = {name: cfg["action"] for name, cfg in triggers.items()}
    cooldowns = {name: c for name, c in tg.cooldown.items() if c > 0}
    # headline model for the status bar follows the last branch (deep/Opus is the default;
    # a "tool" branch runs a sub-agent whose own model is shown on the reply label instead)
    model = chat_model if branch == "chat" else deep_model
    return {
        "status": status,
        "model": model,
        "agent_name": config.agent_name if config is not None else AGENT_NAME,  # v1.2: TUI label
        "branch": branch,
        "tick": tick,
        "needs": dict(state.needs),
        "thresholds": thresholds,
        "actions": actions,
        "hottest": list(state.hottest_need()),  # [need, level]
        "cooldowns": cooldowns,
        "self_messages": state.self_messages,  # proactive reach-outs on/off (/self)
        "stats": stats.snapshot(),
    }


def _self_prompt(prompts: dict, need: str, reached_out: bool) -> str:
    """The self-trigger (reach-out) prompt for a need. When `reached_out` (she already initiated
    and the user hasn't replied since), append SELF_SILENCE_NOTE so consecutive reach-outs don't
    robotically repeat — she acknowledges the silence instead."""
    prompt = pick_prompt(prompts, need)
    return f"{prompt} {SELF_SILENCE_NOTE}" if reached_out else prompt


def _previous_session_turns(
    store_path: Path = STORE_FILE, exclude_session: str | None = None
) -> list[dict]:
    """Turns across ALL prior sessions, oldest→newest, for the v0.8 world timeline. The current
    session's own turns already ride in the messages array / transcript; `world_block` keeps only
    the last `RECENT_MESSAGES`, so the tail spans whatever earlier sessions it needs — a short last
    session no longer starves the block. `exclude_session` drops the live session (which, with
    real-time persistence, it's already in the store) so it isn't double-counted. No store -> []."""
    store = load_store(store_path)
    ordered = sorted(store.get("sessions", []), key=lambda s: s.get("started_at") or "")
    turns: list[dict] = []
    for session in ordered:
        if session.get("id") == exclude_session:
            continue
        turns.extend(store.get("messages", {}).get(session.get("id"), []))
    return turns


# === Action registry (KILN-063) =============================================
# The seam the FSM fires actions through: `name -> callable(ctx)`, with `fire(action, ctx)`. The
# built-ins wrap today's inline `run()` branches; each reads/writes an `ActionContext` — the run
# loop's handles it needs. The turn / think / self-prompt / command primitives are INJECTED as calls
# (run-loop closures over history/prompts/brain), so the built-ins stay thin and testable; the
# module-level engine functions (`apply_satiation`, `reach_out_branch`) are called directly. This is
# the exact seam **1.5 Tools** extends with user tools (an agent's scope gates which it may fire).
# Added here; `run()` drives it in KILN-064.


@dataclass
class ActionContext:
    """What an FSM action reads and writes for one tick. Injected primitives keep the built-ins
    thin; the outcome fields (`status` / `branch` / `reached_out` / `resting` / `do_rotate` /
    `control`) are set by the action and read back by the driver (KILN-064)."""

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
    resting: bool | None = None  # wake/enter_rest flip it; None = leave the driver's flag as-is
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


def run(
    ticks: int | None = 12,
    live: bool = False,
    channel=None,
    brain: Brain | None = None,
    output: Output | None = None,
    paths: AgentPaths | None = None,
    stop_event: threading.Event | None = None,
    config: AgentConfig | None = None,
) -> None:
    """
    The tick loop. `channel.poll()` yields the next user message or None.
    ticks=None -> run forever (for the live StdinChannel).
    `brain` default: LiveBrain when live, MockBrain in dry-run (zero paid
    calls). `output` default: ConsoleOutput (prints to the terminal) — the core
    writes replies only through this port, so the interface (TUI/bus) is swappable.
    `paths` (v1.1): the per-agent persistence root (default = the flat globals).
    `stop_event` (v1.1): cooperative cancel — when set, the loop exits after the
    current tick and the `finally` still persists/summarizes (clean host shutdown).
    `config` (v1.2): the per-agent calibration (need model / tunables / mood). None = the agnika
    default, which reads the module globals at each point — still monkeypatchable in tests; the host
    (KILN-059) passes a per-agent `AgentConfig` so two agents drift/route/sound on their own model.
    """
    if channel is None:
        channel = ScriptedChannel()
    if brain is None:
        brain = LiveBrain() if live else MockBrain()
    if output is None:
        output = ConsoleOutput()
    if paths is None:
        paths = AgentPaths.for_agent()  # v1.1: default agent -> today's flat global paths
    # v1.2: resolve each calibration scalar from `config`, falling back to the module global (read
    # here, so a test monkeypatching e.g. eng.TICK_SECONDS before run() still wins on the None path.
    tick_seconds = config.tick_seconds if config is not None else TICK_SECONDS
    rotate_hours = config.rotate_every_hours if config is not None else ROTATE_EVERY_HOURS
    rest_wake = config.rest_wake if config is not None else REST_WAKE
    recent_messages = config.recent_messages if config is not None else RECENT_MESSAGES
    world_on = config.world_awareness if config is not None else WORLD_AWARENESS
    mood_on = config.mood_awareness if config is not None else MOOD_AWARENESS
    bio_on = config.biorhythm if config is not None else BIORHYTHM
    thoughts_on = config.thoughts_enabled if config is not None else THOUGHTS_ENABLED
    thoughts_in_prompt = config.thoughts_in_prompt if config is not None else THOUGHTS_IN_PROMPT
    thought_every = config.thought_visible_every if config is not None else THOUGHT_VISIBLE_EVERY
    usage_report_on = config.usage_report if config is not None else USAGE_REPORT
    triggers = config.need_triggers if config is not None else NEED_TRIGGERS
    mood_cfg = (
        config.mood if config is not None else None
    )  # None -> mood.py module globals (agnika)
    agent_name = (
        config.agent_name if config is not None else AGENT_NAME
    )  # bot label (timeline/reply)
    paths.state_dir.mkdir(parents=True, exist_ok=True)  # state dir must exist for writing
    paths.store_file.parent.mkdir(parents=True, exist_ok=True)  # .kiln[/{agent}] for store + needs
    state = load_state(paths.needs_file)
    history: list[dict] = []  # shared conversation transcript for the session
    started = _dt.datetime.now().isoformat(timespec="microseconds")  # session id (unique/rotation)
    session_mode = "live" if live else "dry"  # stored with the session (real-time persist + close)
    canon = load_canon(paths.canon_file)  # persona/voice from state/canon.md (re-read on /reload)
    memory = load_memory(paths.store_file)  # long-term memory: summaries of past sessions
    facts = digest_facts(live, paths.store_file)  # v0.6: N-line digest of durable user facts
    base_system = build_system(canon, memory, facts)  # static: canon + memory summaries + facts
    prev_turns = _previous_session_turns(paths.store_file, started)  # v0.8: recent turns across ALL
    # prior sessions for the timeline (the live session is excluded — real-time persistence puts it
    # already in the store; its turns ride in the messages array; world_block keeps RECENT_MESSAGES)
    prompts = load_prompts(paths.prompts_file)  # self-trigger prompts from state/prompts.md
    store = load_store(paths.store_file)  # v0.10: held for the session so thoughts persist
    tg = TriggerBook()  # trigger hysteresis + cooldown
    stats = SessionStats()  # session token/turn/latency totals (for the status bar)

    def _now() -> _dt.datetime:
        # The world clock — TIMEZONE if set, else the machine's local time.
        if TIMEZONE:
            try:
                from zoneinfo import ZoneInfo

                return _dt.datetime.now(ZoneInfo(TIMEZONE))
            except Exception:
                pass
        return _dt.datetime.now()

    birth = load_birth(canon)  # v0.9: from the canon natal line / AGENT_BIRTH (canon is per-agent)
    session_bio = biorhythm(
        _now(), birth, mood_cfg
    )  # the day's biorhythm — computed ONCE, static all session (per-agent periods via mood_cfg)

    def _system() -> str:
        # v0.8 world + v0.9 mood + v0.10 thoughts, composed PER TURN — the clock, needs (incl. the
        # v0.11 curiosity cue in ## Настрій), and latest thoughts stay live; the prior-session
        # timeline and the day's biorhythm are static. All off -> the static base.
        world = (
            world_block(_now(), USER_LOCATION, prev_turns, recent_messages, agent_name)
            if world_on
            else ""
        )
        mood = mood_block(state.needs, session_bio if bio_on else None, mood_cfg) if mood_on else ""
        thoughts = (
            thoughts_block(store["thoughts"], thoughts_in_prompt, {h["text"] for h in history})
            if thoughts_on
            else ""
        )
        if not world and not mood and not thoughts:
            return base_system
        return build_system(canon, memory, facts, world, mood, thoughts)

    def _turn(prompt: str, force: str | None = None, agent: str | None = None) -> dict:
        # One model turn, timed; folds tokens + latency into the session stats.
        t0 = time.monotonic()
        out = respond(
            prompt, state, history, _system(), brain, force=force, agent=agent, config=config
        )
        # v0.11 curiosity monitor: post-process the reply — only while the monitor is ON (armed by a
        # threshold crossing, update_curiosity_monitor), a "?" reply fires the SATIATION "asked"
        # event that discharges curiosity (curious -> asks -> sated -> curious). A statement, or a
        # question while the monitor is off, leaves it. `curiosity` flags the reply for the marker.
        asked = tg.curiosity_monitor and is_curiosity_reply(out["reply"])
        out["curiosity"] = asked
        if asked:
            apply_satiation(state, "asked", config)
        stats.record(out["class"], out.get("usage"), time.monotonic() - t0)
        return out

    def _think() -> dict | None:
        # v0.10 inner monologue: a PRIVATE thought via the chat brain (Haiku). It is NOT a
        # conversation turn — generated against an ephemeral history (so the real transcript is
        # untouched), stored hidden, and it discharges `reflection`. Returns the stored thought
        # record (KILN-043 decides whether to surface it), or None on an empty reply.
        prompt = pick_prompt(prompts, "thought")  # a [thought] reflection prompt (state/prompts.md)
        ephemeral = history + [turn(ROLE_USER, prompt)]  # don't pollute the real conversation
        t0 = time.monotonic()
        text, usage = brain.chat(ephemeral, _system())
        stats.record("thought", usage, time.monotonic() - t0)
        text = strip_leading_name(text, agent_name).strip()
        apply_satiation(state, "thought", config)  # the thought discharges «незібраність»
        if not text:
            return None
        shown = _thought_visible(thought_every)  # ~1/M -> surface it in the chat
        thought = add_thought(
            store, text, started, _now().isoformat(timespec="seconds"), shown=shown
        )
        if shown:
            output.agent(text, is_thought=True)  # dim / «думка:»
            history.append(turn(ROLE_BOT, text))  # a REAL turn — she remembers voicing it
        save_store(store, paths.store_file)
        return thought

    def _save_session_live() -> None:
        # Real-time persistence: upsert the OPEN session + its raw turns into the store every turn,
        # so a crash can't lose them and a freshly attached client's snapshot sees the live
        # conversation. The close replaces these with the pruned turns (+ summary); an all-noise
        # session is dropped there. No-op until there's at least one turn.
        if not history:
            return
        ended_at = _now().isoformat(timespec="seconds")
        upsert_session(store, started, started, session_mode, history, ended_at=ended_at)
        save_store(store, paths.store_file)

    rotate_results: queue.Queue = queue.Queue()  # finished rotations -> applied on the agent thread

    def _ledger_entry(session_id: str, started_at: str, ended: str, turns_count: int, sstats):
        return {
            "session_id": session_id,
            "model": "+".join(sstats.models),
            "started_at": started_at,
            "ended_at": ended,
            "turns": turns_count,
            "input": sstats.input_total,
            "output": sstats.output_total,
            "cache_read": sstats.cache_read_total,
            "cache_write": sstats.cache_write_total,
            "cache_ttl": "5m",
            "cost_usd": round(sstats.cost_usd, 6),
            "cli_calls": sstats.cli_calls,
            "by_model": {
                m: {**v, "cost_usd": round(v["cost_usd"], 6)} for m, v in sstats.by_model.items()
            },
        }

    def _finalize_async(oid, cleaned, existing_facts, ostats, ostart, oended, ostamp):
        # Worker thread: the SLOW close work (summary + facts) computed OFF the agent loop, handed
        # back via rotate_results for the agent thread to write — so all store writes stay on one
        # thread (no race with real-time persistence) and rotation never pauses the agent.
        rotate_results.put(
            {
                "id": oid,
                "summary": summarize(cleaned, live),
                "facts": extract_facts(cleaned, existing_facts, live),
                "stats": ostats,
                "started_at": ostart,
                "ended": oended,
                "stamp": ostamp,
                "turns": len(cleaned),
            }
        )

    t = 0
    total_ticks = 0  # real ticks since session start (catch-up included — counts blocked time)
    branch: str | None = None  # last turn's class (chat/think/tools) for the status snapshot
    resting = False  # rest gate: too tired to answer (recovers on idle; hysteresis vs REST_WAKE)
    reached_out = False  # she self-initiated and the user hasn't replied since (anti-repeat)
    registry = default_registry()  # KILN-064: the FSM's action vocabulary (built-ins as tools)
    rest_threshold = triggers.get("rest", {}).get("threshold", 1.1)  # >1 -> never sleeps
    last_tick = time.monotonic()  # for catch-up drift over real time
    session_start_wall = last_tick  # for auto-rotation (ROTATE_EVERY_HOURS); reset on rotate
    try:
        while (ticks is None or t < ticks) and not (stop_event and stop_event.is_set()):
            # A model call blocks the loop, so one iteration can last many
            # seconds. We count how many ticks ACTUALLY elapsed and apply that
            # much drift (catch-up). In dry-run time "doesn't flow" — exactly 1 tick.
            now = time.monotonic()
            elapsed = now - last_tick
            last_tick = now
            steps = max(1, round(elapsed / tick_seconds)) if (live and tick_seconds > 0) else 1
            drift(state, steps, config)  # catch-up drift over real time (silent)
            total_ticks += steps  # the tick counter tracks real elapsed ticks, not loop iterations
            update_curiosity_monitor(state, tg, config)  # v0.11: arm/disarm curiosity monitor
            # Apply finished rotations (summary/facts computed off-thread) HERE on the agent thread,
            # so the store is only written here, never racing the worker. Refresh memory so the new
            # session's prompt includes the just-summarized one, and notify completion.
            while not rotate_results.empty():
                r = rotate_results.get()
                if r["summary"]:
                    store["summaries"].append(
                        {"session_id": r["id"], "stamp": r["stamp"], "text": r["summary"]}
                    )
                added = add_facts(store, r["facts"], r["id"], r["stamp"])
                if usage_report_on:
                    append_session(
                        _ledger_entry(r["id"], r["started_at"], r["ended"], r["turns"], r["stats"]),
                        paths.usage_ledger,
                    )
                    write_report(read_ledger(paths.usage_ledger), paths.usage_report)
                save_store(store, paths.store_file)
                memory = load_memory(paths.store_file)
                base_system = build_system(canon, memory, facts)
                output.notice(
                    f"[rotate] previous session summarized{f' + {added} facts' if added else ''}"
                )
            # Auto-rotation: every ROTATE_EVERY_HOURS of real time, rotate the session (only when it
            # has content worth summarizing). 0 = off. The /rotate command sets this flag too.
            do_rotate = (
                rotate_hours > 0
                and bool(history)
                and (time.monotonic() - session_start_wall) >= rotate_hours * 3600
            )
            # Rest gate (hysteresis): when fatigue (rest) reaches its threshold Agnika stops
            # answering and only recovers (idle) until rest falls back to REST_WAKE. The band
            # (sleep >= threshold, wake <= REST_WAKE) makes her actually rest instead of
            # flip-flopping every tick. She says REST_MESSAGE on entering, and to anyone who
            # writes while she rests; slash commands still work.
            rest = state.needs.get("rest", 0.0)
            entered_rest = False
            if resting:
                if rest <= rest_wake:
                    resting = False
            elif rest >= rest_threshold:
                resting, entered_rest = True, True

            hlen = len(history)  # real-time persistence: did this tick add a turn?
            user_msg = channel.poll()
            # Priority: INPUT > reach-out > thought > idle. No reach-out/thought while resting; the
            # reach-out needs /self on; the thought (inner monologue) only when no reach-out fires.
            fired = thought_fired = None
            if user_msg is None and not resting:
                if state.self_messages:
                    fired = select_self_trigger(
                        state, tg, config
                    )  # connection reach-out (need name or None)
                if fired is None and thoughts_on:
                    thought_fired = select_thought_trigger(state, tg, config)  # inner monologue

            # --- FSM driver (KILN-064): this tick's ONE action, from the transition table ---
            # The rest-gate flag + self-triggers above are unchanged (v1.2). We turn the sources
            # into events, drain the top-priority one (input > self-trigger > tick), look it up via
            # `advance`, and fire the action through the registry — the old if/elif, byte-for-byte
            # (suite + dry-run are the pin). Rotation stays orthogonal (do_rotate below); the rest
            # gate stays a flag, so RESTING is derived from it (waking already happened, above).
            evq = fsm.gather_events(fsm.EventQueue(), user_msg, fired, thought_fired, False)
            event = evq.drain_one()
            fsm_state = fsm.State.RESTING if resting else fsm.State.IDLE
            fctx = fsm.Ctx(
                needs=state.needs,
                rest_threshold=rest_threshold,
                rest_wake=rest_wake,
                reach_out_need=config.reach_out_need if config is not None else REACH_OUT_NEED,
                reflect_need=config.reflect_need if config is not None else REFLECT_NEED,
            )
            action, _next = fsm.advance(fsm_state, event, fctx)
            actx = ActionContext(
                state=state,
                event=event,
                output=output,
                stats=stats,
                turn=_turn,
                think=_think,
                # bind the loop-reassigned locals (prompts/user_msg/stats) at creation — the actions
                # fire immediately this tick, but the explicit bind keeps it correct and lint-clean.
                self_prompt=lambda need, ro, _p=prompts: _self_prompt(_p, need, ro),
                handle_command=lambda _m=user_msg, _st=stats: handle_command(
                    _m, state, history, _system(), live, output, _st
                ),
                config=config,
                reached_out=reached_out,
                entered_rest=entered_rest,
                resting=resting,
                branch=branch,
            )
            registry.fire(action, actx)
            status_label, branch, reached_out = actx.status, actx.branch, actx.reached_out
            if actx.do_rotate:
                do_rotate = True
            if actx.control == "quit":
                break
            if actx.control == "reload":
                # Re-read the file-backed prompt sources mid-session (no session drop). config.yaml
                # knobs are module constants -> still need a restart; the canon birthday/biorhythm
                # is session-static -> a /rotate or restart picks it up.
                canon = load_canon(paths.canon_file)
                prompts = load_prompts(paths.prompts_file)
                memory = load_memory(paths.store_file)
                base_system = build_system(canon, memory, facts)
                prev_turns = _previous_session_turns(paths.store_file, started)
                output.notice("[reload] canon, prompts, and memory reloaded")

            if do_rotate:
                # Non-blocking rotation (from /rotate or the timer): cut to a fresh session now,
                # the old one's slow summary/facts on a worker (folded in later). The agent never
                # pauses (real-time persistence already stored the old turns).
                old_id, old_turns, old_stats = started, list(history), stats
                cleaned_old = prune_history(old_turns)
                if not cleaned_old:
                    remove_session(store, old_id)  # all-noise: nothing worth summarizing
                    save_store(store, paths.store_file)
                else:
                    oended = _dt.datetime.now().isoformat(timespec="seconds")
                    ostamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
                    upsert_session(store, old_id, old_id, session_mode, cleaned_old, oended)
                    save_store(store, paths.store_file)
                    existing = [f.get("text", "") for f in store.get("facts", [])]
                    threading.Thread(
                        target=_finalize_async,
                        args=(old_id, cleaned_old, existing, old_stats, old_id, oended, ostamp),
                        daemon=True,
                    ).start()
                started = _dt.datetime.now().isoformat(timespec="microseconds")
                history.clear()
                stats = SessionStats()
                branch = None
                prev_turns = _previous_session_turns(paths.store_file, started)
                session_start_wall = time.monotonic()  # reset the auto-rotate clock
                output.notice("[rotate] session rotated; summarizing the previous one…")

            if len(history) != hlen:
                _save_session_live()  # real-time: a turn was added this tick -> persist it now
            # Per-tick status snapshot (needs + thresholds + stats) for live clients.
            output.status(
                _status_snapshot(status_label, state, tg, stats, branch, total_ticks, config)
            )

            time.sleep(tick_seconds if live else 0)
            t += 1
    finally:
        save_state(state, paths.needs_file)
        # Review & prune to real conversation; an all-noise session is dropped (incl. one persisted
        # live during the session). Otherwise upsert the pruned turns over the real-time copy.
        cleaned = prune_history(history)
        store = load_store(paths.store_file)
        if not cleaned:
            remove_session(store, started)
            save_store(store, paths.store_file)
        else:
            # The session id is the start time. Persist the pruned turns FIRST — before the
            # (possibly failing) summary — so a summary failure can't lose the transcript.
            ended = _dt.datetime.now().isoformat(timespec="seconds")
            stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
            upsert_session(store, started, started, session_mode, cleaned, ended)
            save_store(store, paths.store_file)  # transcript safe before summarizing
            summary = summarize(cleaned, live)
            if summary:
                store["summaries"].append({"session_id": started, "stamp": stamp, "text": summary})
                save_store(store, paths.store_file)
            # KILN-023: extract durable user facts (Opus + thinking) and fold them in (deduped).
            existing_facts = [f.get("text", "") for f in store.get("facts", [])]
            added_facts = add_facts(
                store, extract_facts(cleaned, existing_facts, live), started, stamp
            )
            if added_facts:
                save_store(store, paths.store_file)
            # KILN-028/029/030: append one usage-ledger line + regenerate the report, unless
            # usage reporting is disabled (USAGE_REPORT=0).
            if usage_report_on:
                append_session(
                    _ledger_entry(started, started, ended, len(cleaned), stats), paths.usage_ledger
                )
                # regenerate the agent's usage-report.md from ITS ledger
                write_report(read_ledger(paths.usage_ledger), paths.usage_report)
            output.notice(
                f"[exit] stored session {started} ({len(cleaned)} turns)"
                f"{' + summary' if summary else ''}"
                f"{f' + {added_facts} facts' if added_facts else ''} -> {paths.store_file.name}"
            )
