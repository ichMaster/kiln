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
    MEMORY_FILE,
    NEED_TRIGGERS,
    SATIATION,
    SELF_COOLDOWN,
    STATE_DIR,
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
    save_session,
    save_summary,
    summarize,
)
from .output import ConsoleOutput, Output
from .stats import SessionStats

# === State ==================================================================


@dataclass
class State:
    needs: dict[str, float] = field(default_factory=dict)

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


def select_self_trigger(state: State, tg: TriggerBook) -> tuple[str | None, str | None]:
    """
    Returns (need, action) for a self-trigger, or (None, None).
    Hysteresis: fires only on an UPWARD threshold crossing (re-arms once the
    need falls back below). Cooldown: after firing — N silent ticks.
    """
    # Cooldown ticks down for every need once per tick.
    for name in NEED_TRIGGERS:
        if tg.cooldown.get(name, 0) > 0:
            tg.cooldown[name] -= 1

    eligible = []
    for name, cfg in NEED_TRIGGERS.items():
        level = state.needs.get(name, 0.0)
        over = level >= cfg["threshold"]
        if not over:
            tg.armed[name] = True  # re-arm below threshold
        elif tg.armed.get(name, True) and tg.cooldown.get(name, 0) == 0:
            eligible.append((level - cfg["threshold"], name, cfg["action"]))

    if not eligible:
        return None, None

    eligible.sort(reverse=True)  # largest overshoot first
    _, name, action = eligible[0]
    tg.armed[name] = False  # discharge hysteresis
    tg.cooldown[name] = SELF_COOLDOWN  # start cooldown
    return name, action


# === Turn classification ====================================================


def turn_weight(state: State) -> float:
    """Turn weight ~0..1 from state. Higher -> closer to deep reasoning."""
    return max(0.0, min(1.0, 0.55 * state.intensity + 0.45 * state.connection))


def classify(prompt: str, state: State) -> str:
    """
    Returns the turn class: 'chat' | 'think' | 'tools'.

    The decision combines message content and state:
      - explicit tool markers -> 'tools';
      - reasoning markers OR a high state weight -> 'think';
      - otherwise -> 'chat'.
    """
    low = prompt.lower()
    if any(h in low for h in TOOL_HINTS):
        return "tools"
    if any(h in low for h in THINK_HINTS) or turn_weight(state) >= THINK_THRESHOLD:
        return "think"
    return "chat"


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
) -> dict:
    # force ("chat"|"deep") picks the branch directly (for self-triggers and /ask),
    # otherwise normal classification. We call the model ONLY through brain (seam):
    # the core knows nothing about the SDK or the CLI. usage arrives with the text.
    cls = force if force else classify(prompt, state)

    # The user's current turn goes into the shared history before the call.
    history.append({"role": ROLE_USER, "text": prompt})

    if cls == "chat":
        reply, usage = brain.chat(history, system)
        route = f"CHAT/{CHAT_MODEL.split('-')[1]}"  # e.g. CHAT/haiku
        event = "chat"
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
    tick is the loop's tick counter (0-based).
    """
    thresholds = {name: cfg["threshold"] for name, cfg in NEED_TRIGGERS.items()}
    # what addresses each need (the self-trigger branch)
    actions = {name: cfg["action"] for name, cfg in NEED_TRIGGERS.items()}
    cooldowns = {name: c for name, c in tg.cooldown.items() if c > 0}
    model = CHAT_MODEL if branch == "chat" else DEEP_MODEL  # deep model is the headline default
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

    def _turn(prompt: str, force: str | None = None) -> dict:
        # One model turn, timed; folds tokens + latency into the session stats.
        t0 = time.monotonic()
        out = respond(prompt, state, history, system, brain, force=force)
        stats.record(out["class"], out.get("usage"), time.monotonic() - t0)
        return out

    t = 0
    branch: str | None = None  # last turn's class (chat/think/tools) for the status snapshot
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
            user_msg = channel.poll()
            # Priority: INPUT beats a self-trigger. If both are present, we handle
            # input this tick; the trigger is checked on the next tick.
            fired, faction = (None, None)
            if user_msg is None:
                fired, faction = select_self_trigger(state, tg)

            status_label = "idle"
            if user_msg is not None:
                action = handle_command(user_msg, state, history, system, live, output)
                if action == "quit":
                    output.notice("[exit] exit by command")
                    break
                elif action == "handled":
                    pass  # command handled, the brain is left untouched
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
            elif fired is not None:
                prompt = pick_prompt(prompts, fired)
                out = _turn(prompt, force=faction)
                output.agent(out["reply"], is_self=True, model=out["route"].split("/")[-1])
                output.usage(out.get("usage"), stats.last_latency)
                status_label, branch = "responding", out["class"]
            else:
                apply_satiation(state, "idle")  # silence: rest + cooling down
                # a silent tick isn't printed — check state via /status

            # Per-tick status snapshot (needs + thresholds + stats) for live clients.
            output.status(_status_snapshot(status_label, state, tg, stats, branch, t))

            time.sleep(TICK_SECONDS if live else 0)
            t += 1
    finally:
        save_state(state)
        if history:
            # We save the raw transcript FIRST — it's the most important (for RAG)
            # and must not depend on a (possibly failing) summarize call.
            session_path = save_session(history, live, started)
            summary = summarize(history, live)
            save_summary(summary)
            output.notice(
                f"[exit] saved summary ({len(history)} turns) -> {MEMORY_FILE.name}; "
                f"transcript -> history/{session_path.name}"
            )
