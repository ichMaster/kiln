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
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .brain import Brain, LiveBrain, MockBrain
from .commands import handle_command
from .config import (
    CHAT_MODEL,
    DEEP_MODEL,
    DRIFT,
    NEED_TRIGGERS,
    REACH_OUT_MODELS,
    REACH_OUT_NEED,
    REST_MESSAGE,
    REST_WAKE,
    SATIATION,
    SELF_COOLDOWN,
    STATE_DIR,
    STORE_FILE,
    THINK_HINTS,
    THINK_THRESHOLD,
    TICK_SECONDS,
    TOOL_HINTS,
)
from .history import ROLE_BOT, ROLE_USER
from .memory import (
    build_system,
    load_canon,
    load_memory,
    load_prompts,
    pick_prompt,
    prune_history,
    summarize,
)
from .output import ConsoleOutput, Output
from .stats import SessionStats
from .store import load_store, save_store

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


def load_state(state_dir: Path = STATE_DIR) -> State:
    """Reads needs from state/needs.json ({need: level}); empty state if the file is missing."""
    path = state_dir / "needs.json"
    if not path.exists():
        return State()
    data = json.loads(path.read_text(encoding="utf-8"))
    needs = {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}
    return State(needs=needs)


def save_state(state: State, state_dir: Path = STATE_DIR) -> None:
    """Writes needs to state/needs.json ({need: level}, rounded to 3 decimal places)."""
    data = {k: round(v, 3) for k, v in state.needs.items()}
    (state_dir / "needs.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# === Tick ===================================================================


def drift(state: State, ticks: int = 1) -> None:
    """Each need grows by DRIFT[k] × ticks (clamped to 1.0).
    ticks > 1 — "catch-up" drift for real time that elapsed during a
    blocking model call (see the loop in run)."""
    for k in state.needs:
        state.needs[k] = min(1.0, state.needs[k] + DRIFT.get(k, 0.0) * ticks)


def apply_satiation(state: State, event: str) -> None:
    """Closes needs per the event ('chat' | 'deep' | 'idle'), clamping at 0."""
    for k, delta in SATIATION.get(event, {}).items():
        if k in state.needs:
            state.needs[k] = max(0.0, state.needs[k] + delta)


@dataclass
class TriggerBook:
    """Runtime trigger state (not persisted): hysteresis + per-need cooldown."""

    armed: dict[str, bool] = field(default_factory=dict)  # ready to fire?
    cooldown: dict[str, int] = field(default_factory=dict)  # silent ticks remaining


def select_self_trigger(state: State, tg: TriggerBook) -> str | None:
    """
    The proactive reach-out fires ONLY on REACH_OUT_NEED (connection = loneliness): returns
    that need name when it crosses its threshold this tick, else None. WHICH brain answers is
    a separate choice (reach_out_branch). Hysteresis: fires only on an UPWARD crossing (re-arms
    once it falls back below). Cooldown: after firing — SELF_COOLDOWN silent ticks.
    """
    name = REACH_OUT_NEED
    cfg = NEED_TRIGGERS.get(name)
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
    tg.cooldown[name] = SELF_COOLDOWN  # start cooldown
    return name


def reach_out_branch(state: State) -> tuple[str, str | None]:
    """
    WHICH brain answers a connection reach-out, shaped by her OTHER needs at fire time:
    the first REACH_OUT_MODELS need over its threshold wins (intensity -> deep/opus, novelty
    -> session-wiki), else the reach-out need's baseline (chat). So opus/session-wiki never
    self-INITIATE — they only shape a connection-driven message. Returns (action, agent).
    """
    for name in REACH_OUT_MODELS:
        cfg = NEED_TRIGGERS.get(name, {})
        if state.needs.get(name, 0.0) >= cfg.get("threshold", 2.0):
            return cfg.get("action", "chat"), cfg.get("agent")
    base = NEED_TRIGGERS.get(REACH_OUT_NEED, {})
    return base.get("action", "chat"), base.get("agent")


# === Turn classification ====================================================


def turn_weight(state: State) -> float:
    """Turn weight ~0..1 from state. Higher -> closer to deep reasoning."""
    return max(0.0, min(1.0, 0.55 * state.intensity + 0.45 * state.connection))


def classify(prompt: str, state: State) -> tuple[str, str | None]:
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
    """
    low = prompt.lower()
    if any(h in low for h in TOOL_HINTS):
        return "tools", None
    if any(h in low for h in THINK_HINTS):
        return "think", None
    for name in REACH_OUT_MODELS:  # high need -> deeper brain (same map as reach_out_branch)
        cfg = NEED_TRIGGERS.get(name, {})
        if state.needs.get(name, 0.0) >= cfg.get("threshold", 2.0):
            return cfg.get("action", "chat"), cfg.get("agent")
    if turn_weight(state) >= THINK_THRESHOLD:
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


def respond(
    prompt: str,
    state: State,
    history: list[dict],
    system: str,
    brain: Brain,
    force: str | None = None,
    agent: str | None = None,
) -> dict:
    # force ("chat"|"deep"|"tool") picks the branch directly (for self-triggers and /ask),
    # otherwise classify() routes the user turn (and may name the "tool" sub-agent). We call
    # the model ONLY through brain (seam): the core knows nothing about the SDK or the CLI.
    if force:
        cls = force
    else:
        cls, agent = classify(prompt, state)

    # The user's current turn goes into the shared history before the call.
    history.append({"role": ROLE_USER, "text": prompt})

    if cls == "chat":
        reply, usage = brain.chat(history, system)
        route = f"CHAT/{CHAT_MODEL.split('-')[1]}"  # e.g. CHAT/haiku
        event = "chat"
    elif cls == "tool":
        # A "tool" self-trigger runs a named Claude Code sub-agent (e.g. novelty ->
        # session-wiki, an external Wikipedia fact). Satiation is PER-AGENT: if SATIATION has
        # an entry keyed by the agent name it's used (so session-wiki drops novelty on its
        # own terms), else it falls back to a deep "filling meal". NB: distinct from the
        # "tools" class below (deep + --allowedTools); here the whole turn is a sub-agent.
        reply, usage = brain.tool(agent or "", history, system)
        route = f"TOOL/{agent}"  # e.g. TOOL/session-wiki (the agent IS the trace label)
        event = agent if agent in SATIATION else "deep"
    elif cls in ("think", "deep"):
        reply, usage = brain.deep(prompt, history, system, with_tools=False)
        route = f"THINK/{DEEP_MODEL.split('-')[1]}"  # e.g. THINK/opus
        event = "deep"
    else:  # tools
        reply, usage = brain.deep(prompt, history, system, with_tools=True)
        route = f"TOOLS/{DEEP_MODEL.split('-')[1]}"
        event = "deep"

    # The reply goes into the history too.
    history.append({"role": ROLE_BOT, "text": reply})

    # The branch determines which needs were closed.
    apply_satiation(state, event)
    return {"class": cls, "route": route, "reply": reply, "usage": usage}


def _status_snapshot(
    status: str, state: State, tg: TriggerBook, stats: SessionStats, branch: str | None, tick: int
) -> dict:
    """
    Build the per-tick status snapshot the TUI status bar / needs panel render from.
    status ∈ idle/thinking/responding; branch is the last turn class (chat/think/tools);
    tick is the real elapsed-tick count since session start (catch-up included, so time
    spent in a blocking model call is counted, not just loop iterations).
    """
    thresholds = {name: cfg["threshold"] for name, cfg in NEED_TRIGGERS.items()}
    # what addresses each need (the self-trigger branch)
    actions = {name: cfg["action"] for name, cfg in NEED_TRIGGERS.items()}
    cooldowns = {name: c for name, c in tg.cooldown.items() if c > 0}
    # headline model for the status bar follows the last branch (deep/Opus is the default;
    # a "tool" branch runs a sub-agent whose own model is shown on the reply label instead)
    model = CHAT_MODEL if branch == "chat" else DEEP_MODEL
    return {
        "status": status,
        "model": model,
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


def run(
    ticks: int | None = 12,
    live: bool = False,
    channel=None,
    brain: Brain | None = None,
    output: Output | None = None,
) -> None:
    """
    The tick loop. `channel.poll()` yields the next user message or None.
    ticks=None -> run forever (for the live StdinChannel).
    `brain` default: LiveBrain when live, MockBrain in dry-run (zero paid
    calls). `output` default: ConsoleOutput (prints to the terminal) — the core
    writes replies only through this port, so the interface (TUI/bus) is swappable.
    """
    if channel is None:
        channel = ScriptedChannel()
    if brain is None:
        brain = LiveBrain() if live else MockBrain()
    if output is None:
        output = ConsoleOutput()
    STATE_DIR.mkdir(parents=True, exist_ok=True)  # state dir must exist for writing
    state = load_state()
    history: list[dict] = []  # shared conversation transcript for the session
    started = _dt.datetime.now().isoformat(timespec="seconds")  # session start (for transcript)
    canon = load_canon()  # persona/voice from state/canon.md
    memory = load_memory()  # long-term memory from past sessions
    system = build_system(canon, memory)  # canon + memory
    prompts = load_prompts()  # self-trigger prompts from state/prompts.md
    tg = TriggerBook()  # trigger hysteresis + cooldown
    stats = SessionStats()  # session token/turn/latency totals (for the status bar)

    def _turn(prompt: str, force: str | None = None, agent: str | None = None) -> dict:
        # One model turn, timed; folds tokens + latency into the session stats.
        t0 = time.monotonic()
        out = respond(prompt, state, history, system, brain, force=force, agent=agent)
        stats.record(out["class"], out.get("usage"), time.monotonic() - t0)
        return out

    t = 0
    total_ticks = 0  # real ticks since session start (catch-up included — counts blocked time)
    branch: str | None = None  # last turn's class (chat/think/tools) for the status snapshot
    resting = False  # rest gate: too tired to answer (recovers on idle; hysteresis vs REST_WAKE)
    rest_threshold = NEED_TRIGGERS.get("rest", {}).get("threshold", 1.1)  # >1 -> never sleeps
    last_tick = time.monotonic()  # for catch-up drift over real time
    try:
        while ticks is None or t < ticks:
            # A model call blocks the loop, so one iteration can last many
            # seconds. We count how many ticks ACTUALLY elapsed and apply that
            # much drift (catch-up). In dry-run time "doesn't flow" — exactly 1 tick.
            now = time.monotonic()
            elapsed = now - last_tick
            last_tick = now
            steps = max(1, round(elapsed / TICK_SECONDS)) if (live and TICK_SECONDS > 0) else 1
            drift(state, steps)  # catch-up drift over real time (silent)
            total_ticks += steps  # the tick counter tracks real elapsed ticks, not loop iterations
            # Rest gate (hysteresis): when fatigue (rest) reaches its threshold Agnika stops
            # answering and only recovers (idle) until rest falls back to REST_WAKE. The band
            # (sleep >= threshold, wake <= REST_WAKE) makes her actually rest instead of
            # flip-flopping every tick. She says REST_MESSAGE on entering, and to anyone who
            # writes while she rests; slash commands still work.
            rest = state.needs.get("rest", 0.0)
            entered_rest = False
            if resting:
                if rest <= REST_WAKE:
                    resting = False
            elif rest >= rest_threshold:
                resting, entered_rest = True, True

            user_msg = channel.poll()
            # Priority: INPUT beats a self-trigger. No reach-out while resting, or when proactive
            # self-messages are switched off (/self).
            fired = None
            if user_msg is None and not resting and state.self_messages:
                fired = select_self_trigger(state, tg)  # connection reach-out (need name or None)

            status_label = "idle"
            if user_msg is not None:
                action = handle_command(user_msg, state, history, system, live, output)
                if action == "quit":
                    output.notice("[exit] exit by command")
                    break
                elif action == "handled":
                    pass  # command handled (commands work even while resting)
                elif resting:
                    # too tired to engage: heard, but don't call the brain. Say the rest
                    # line only ONCE (when she enters rest), not to every message.
                    output.user(user_msg)
                    if entered_rest:
                        output.agent(REST_MESSAGE, is_self=True)
                    apply_satiation(state, "idle")
                    status_label = "resting"
                elif isinstance(action, tuple):  # ("ask", text) -> forced deep
                    out = _turn(action[1], force="deep")
                    output.agent(out["reply"], lead=True, model=out["route"].split("/")[-1])
                    output.usage(out.get("usage"), stats.last_latency)
                    status_label, branch = "responding", out["class"]
                else:  # None -> normal turn
                    out = _turn(user_msg)
                    output.user(user_msg)
                    output.agent(out["reply"], model=out["route"].split("/")[-1])
                    output.usage(out.get("usage"), stats.last_latency)
                    status_label, branch = "responding", out["class"]
            elif resting:
                if entered_rest:
                    output.agent(REST_MESSAGE, is_self=True)  # announce once on entering rest
                apply_satiation(state, "idle")
                status_label = "resting"
            elif fired is not None:
                prompt = pick_prompt(prompts, fired)
                # connection fired the reach-out; her other needs choose which brain answers
                # (intensity -> deep/opus, novelty -> session-wiki, else chat).
                faction, agent = reach_out_branch(state)
                out = _turn(prompt, force=faction, agent=agent)
                output.agent(out["reply"], is_self=True, model=out["route"].split("/")[-1])
                output.usage(out.get("usage"), stats.last_latency)
                status_label, branch = "responding", out["class"]
            else:
                apply_satiation(state, "idle")  # silence: rest + cooling down
                # a silent tick isn't printed — check state via /status

            # Per-tick status snapshot (needs + thresholds + stats) for live clients.
            output.status(_status_snapshot(status_label, state, tg, stats, branch, total_ticks))

            time.sleep(TICK_SECONDS if live else 0)
            t += 1
    finally:
        save_state(state)
        # Review & prune to real conversation; an all-noise session is skipped entirely.
        cleaned = prune_history(history)
        if cleaned:
            # Everything closes into the single .kiln/store.json. We persist the session +
            # its raw turns (the RAG corpus) FIRST — before the (possibly failing) summary —
            # so a summary failure can't lose the transcript. The session id is the start time.
            ended = _dt.datetime.now().isoformat(timespec="seconds")
            store = load_store()
            store["sessions"].append(
                {
                    "id": started,
                    "started_at": started,
                    "ended_at": ended,
                    "mode": "live" if live else "dry",
                    "turns": len(cleaned),
                }
            )
            store["messages"][started] = list(cleaned)
            save_store(store)  # transcript safe before summarizing
            summary = summarize(cleaned, live)
            if summary:
                stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
                store["summaries"].append({"session_id": started, "stamp": stamp, "text": summary})
                save_store(store)
            output.notice(
                f"[exit] stored session {started} ({len(cleaned)} turns)"
                f"{' + summary' if summary else ''} -> {STORE_FILE.name}"
            )
