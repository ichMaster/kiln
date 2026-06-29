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
from dataclasses import dataclass, field
from pathlib import Path

from .brain import Brain, LiveBrain, MockBrain
from .commands import handle_command
from .config import (
    BIORHYTHM,
    CHAT_MODEL,
    CURIOSITY,
    CURIOSITY_SATIATION,
    CURIOSITY_THRESHOLD,
    DEEP_MODEL,
    DRIFT,
    MOOD_AWARENESS,
    NEED_TRIGGERS,
    REACH_OUT_MODELS,
    REACH_OUT_NEED,
    RECENT_MESSAGES,
    REFLECT_NEED,
    REST_MESSAGE,
    REST_WAKE,
    SATIATION,
    SELF_COOLDOWN,
    SELF_SILENCE_NOTE,
    STATE_DIR,
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
)
from .history import ROLE_BOT, ROLE_USER, strip_leading_name, turn
from .ledger import append_session
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
from .mood import biorhythm, curiosity_nudge, mood_block
from .output import ConsoleOutput, Output
from .report import write_report
from .stats import SessionStats
from .store import add_facts, add_thought, load_store, save_store
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


def load_state(state_dir: Path = STATE_DIR) -> State:
    """Reads needs from state/needs.json ({need: level}); empty state if the file is missing. Any
    configured need (a `DRIFT` key) absent from the file is healed in at 0.0 — so a new need (e.g.
    v0.10 `reflection`, v0.11 `curiosity`) appears in the TUI/commands and drifts, no re-seed."""
    path = state_dir / "needs.json"
    if not path.exists():
        return State()
    data = json.loads(path.read_text(encoding="utf-8"))
    needs = {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}
    for need in DRIFT:  # heal: a configured need missing from the file starts at 0.0
        needs.setdefault(need, 0.0)
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


def _crossing_trigger(state: State, tg: TriggerBook, name: str, cooldown: int) -> str | None:
    """Fire `name` on an UPWARD threshold crossing (`NEED_TRIGGERS`) with hysteresis — fires only on
    the up-crossing, re-arms once it falls back below — and a `cooldown` of silent ticks after.
    Returns the need name when it fires this tick, else None."""
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
    tg.cooldown[name] = cooldown  # start cooldown
    return name


def select_self_trigger(state: State, tg: TriggerBook) -> str | None:
    """The proactive reach-out fires ONLY on REACH_OUT_NEED (connection = loneliness): returns that
    need name when it crosses its threshold this tick, else None. WHICH brain answers is a separate
    choice (reach_out_branch). Hysteresis + SELF_COOLDOWN silent ticks after firing."""
    return _crossing_trigger(state, tg, REACH_OUT_NEED, SELF_COOLDOWN)


def _thought_visible(every: int) -> bool:
    """Whether a freshly formed thought surfaces in the chat — ~1/`every` via the stdlib RNG
    (seedable / monkeypatchable in tests). `every <= 0` → never shown."""
    return every > 0 and random.random() < (1.0 / every)


def select_thought_trigger(state: State, tg: TriggerBook) -> str | None:
    """The inner monologue fires on REFLECT_NEED (reflection = незібраність): returns it on an
    upward crossing this tick (else None), with the same hysteresis + THOUGHT_COOLDOWN as the
    reach-out. The thought itself (KILN-042) is generated separately — this only decides WHEN."""
    return _crossing_trigger(state, tg, REFLECT_NEED, THOUGHT_COOLDOWN)


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
) -> dict:
    # force ("chat"|"deep"|"tool") picks the branch directly (for self-triggers and /ask),
    # otherwise classify() routes the user turn (and may name the "tool" sub-agent). We call
    # the model ONLY through brain (seam): the core knows nothing about the SDK or the CLI.
    if force:
        cls = force
    else:
        cls, agent = classify(prompt, state)

    # The user's current turn goes into the shared history before the call (timestamped, v0.8).
    history.append(turn(ROLE_USER, prompt))

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

    # Strip a leading name the model echoed (it mirrors the timeline's "Агніка:" labels) — clean
    # for both display and storage, so it never shows and never compounds in the next timeline.
    reply = strip_leading_name(reply)
    # The reply goes into the history too (timestamped, v0.8).
    history.append(turn(ROLE_BOT, reply))

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
    # v0.11: curiosity is NOT a trigger (no self-message), but surface its nudge threshold so the
    # panel shows it as a full need (`0.62/0.50 → nudge` + the colour gradient), not a bare bar.
    if CURIOSITY and "curiosity" in state.needs:
        thresholds["curiosity"] = CURIOSITY_THRESHOLD
        actions["curiosity"] = "nudge"
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


def _self_prompt(prompts: dict, need: str, reached_out: bool) -> str:
    """The self-trigger (reach-out) prompt for a need. When `reached_out` (she already initiated
    and the user hasn't replied since), append SELF_SILENCE_NOTE so consecutive reach-outs don't
    robotically repeat — she acknowledges the silence instead."""
    prompt = pick_prompt(prompts, need)
    return f"{prompt} {SELF_SILENCE_NOTE}" if reached_out else prompt


def _previous_session_turns() -> list[dict]:
    """The most recent CLOSED session's turn list, for the v0.8 world timeline. The current
    session's own turns already ride in the messages array / transcript, so the timeline carries
    the prior conversation's tail instead. Empty store / no prior session -> []."""
    store = load_store()
    sessions = store.get("sessions", [])
    if not sessions:
        return []
    last = max(sessions, key=lambda s: s.get("started_at") or "")
    return store.get("messages", {}).get(last.get("id"), [])


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
    memory = load_memory()  # long-term memory: summaries of past sessions
    facts = digest_facts(live)  # v0.6 long memory: N-line digest of durable user facts
    base_system = build_system(canon, memory, facts)  # static: canon + memory summaries + facts
    prev_turns = _previous_session_turns()  # v0.8: the PRIOR session's tail for the timeline (the
    # current session's own turns already ride in the messages array / transcript, so they aren't
    # repeated here — this carries continuity from the last conversation instead)
    prompts = load_prompts()  # self-trigger prompts from state/prompts.md
    store = load_store()  # v0.10: held for the session so thoughts persist as they form
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

    birth = load_birth(canon)  # v0.9: from the canon natal line / AGENT_BIRTH
    session_bio = biorhythm(
        _now(), birth
    )  # the day's biorhythm — computed ONCE, static all session

    def _system() -> str:
        # v0.8 world + v0.9 mood + v0.10 thoughts + v0.11 curiosity, composed PER TURN — the clock,
        # needs, latest thoughts, and the curiosity nudge stay live; the prior-session timeline and
        # the day's biorhythm are static. All off -> the static base.
        world = (
            world_block(_now(), USER_LOCATION, prev_turns, RECENT_MESSAGES)
            if WORLD_AWARENESS
            else ""
        )
        mood = mood_block(state.needs, session_bio if BIORHYTHM else None) if MOOD_AWARENESS else ""
        thoughts = (
            thoughts_block(store["thoughts"], THOUGHTS_IN_PROMPT, {h["text"] for h in history})
            if THOUGHTS_ENABLED
            else ""
        )
        # Always present when CURIOSITY is on — the message is graded by level (curiosity_nudge);
        # CURIOSITY_THRESHOLD still gates the discharge in _turn, not the block.
        curiosity = curiosity_nudge(state.needs.get("curiosity", 0.0)) if CURIOSITY else ""
        if not world and not mood and not thoughts and not curiosity:
            return base_system
        return build_system(canon, memory, facts, world, mood, thoughts, curiosity)

    def _turn(prompt: str, force: str | None = None, agent: str | None = None) -> dict:
        # One model turn, timed; folds tokens + latency into the session stats.
        t0 = time.monotonic()
        # v0.11: was the curiosity nudge active this turn? (read before the reply may sate it)
        curiosity_active = CURIOSITY and state.needs.get("curiosity", 0.0) >= CURIOSITY_THRESHOLD
        out = respond(prompt, state, history, _system(), brain, force=force, agent=agent)
        # Acting on the nudge — a real question while it was active — discharges curiosity (which
        # then drifts back up): curious -> asks -> sated -> curious. A statement leaves it high.
        out["curiosity"] = curiosity_active and is_curiosity_reply(out["reply"])
        if out["curiosity"]:
            cur = state.needs.get("curiosity", 0.0)
            state.needs["curiosity"] = max(0.0, cur - CURIOSITY_SATIATION)
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
        text = strip_leading_name(text).strip()
        apply_satiation(state, "thought")  # the thought discharges «незібраність»
        if not text:
            return None
        shown = _thought_visible(THOUGHT_VISIBLE_EVERY)  # ~1/M -> surface it in the chat
        thought = add_thought(
            store, text, started, _now().isoformat(timespec="seconds"), shown=shown
        )
        if shown:
            output.agent(text, is_thought=True)  # dim / «думка:»
            history.append(turn(ROLE_BOT, text))  # a REAL turn — she remembers voicing it
        save_store(store)
        return thought

    t = 0
    total_ticks = 0  # real ticks since session start (catch-up included — counts blocked time)
    branch: str | None = None  # last turn's class (chat/think/tools) for the status snapshot
    resting = False  # rest gate: too tired to answer (recovers on idle; hysteresis vs REST_WAKE)
    reached_out = False  # she self-initiated and the user hasn't replied since (anti-repeat)
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
            # Priority: INPUT > reach-out > thought > idle. No reach-out/thought while resting; the
            # reach-out needs /self on; the thought (inner monologue) only when no reach-out fires.
            fired = thought_fired = None
            if user_msg is None and not resting:
                if state.self_messages:
                    fired = select_self_trigger(
                        state, tg
                    )  # connection reach-out (need name or None)
                if fired is None and THOUGHTS_ENABLED:
                    thought_fired = select_thought_trigger(state, tg)  # inner monologue (v0.10)

            status_label = "idle"
            if user_msg is not None:
                action = handle_command(user_msg, state, history, _system(), live, output, stats)
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
                    reached_out = False  # the user replied (even while she rests)
                elif isinstance(action, tuple):  # ("ask", text) -> forced deep
                    out = _turn(action[1], force="deep")
                    output.agent(
                        out["reply"],
                        lead=True,
                        model=out["route"].split("/")[-1],
                        is_curiosity=out["curiosity"],
                    )
                    output.usage(out.get("usage"), stats.last_latency)
                    status_label, branch = "responding", out["class"]
                    reached_out = False  # the user engaged
                else:  # None -> normal turn
                    out = _turn(user_msg)
                    output.user(user_msg)
                    output.agent(
                        out["reply"],
                        model=out["route"].split("/")[-1],
                        is_curiosity=out["curiosity"],
                    )
                    output.usage(out.get("usage"), stats.last_latency)
                    status_label, branch = "responding", out["class"]
                    reached_out = False  # the user replied
            elif resting:
                if entered_rest:
                    output.agent(REST_MESSAGE, is_self=True)  # announce once on entering rest
                apply_satiation(state, "idle")
                status_label = "resting"
            elif fired is not None:
                # connection fired the reach-out; her other needs choose which brain answers
                # (intensity -> deep/opus, novelty -> session-wiki, else chat). If she already
                # reached out and got no reply, the prompt tells her not to repeat (reached_out).
                prompt = _self_prompt(prompts, fired, reached_out)
                faction, agent = reach_out_branch(state)
                out = _turn(prompt, force=faction, agent=agent)
                output.agent(
                    out["reply"],
                    is_self=True,
                    model=out["route"].split("/")[-1],
                    is_curiosity=out["curiosity"],
                )
                output.usage(out.get("usage"), stats.last_latency)
                status_label, branch = "responding", out["class"]
                reached_out = True  # awaiting a reply; next reach-out acknowledges the silence
            elif thought_fired is not None:
                _think()  # private inner thought (Haiku) — stored hidden, discharges reflection
                status_label = "thinking"  # nothing displayed (KILN-043 surfaces ~1/M)
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
            stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
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
                store["summaries"].append({"session_id": started, "stamp": stamp, "text": summary})
                save_store(store)
            # KILN-023: extract durable user facts (Opus + thinking) and fold them in (deduped).
            existing_facts = [f.get("text", "") for f in store.get("facts", [])]
            added_facts = add_facts(
                store, extract_facts(cleaned, existing_facts, live), started, stamp
            )
            if added_facts:
                save_store(store)
            # KILN-028/029/030: append one usage-ledger line + regenerate the report, unless
            # usage reporting is disabled (USAGE_REPORT=0).
            if USAGE_REPORT:
                append_session(
                    {
                        "session_id": started,
                        "model": "+".join(stats.models),
                        "started_at": started,
                        "ended_at": ended,
                        "turns": len(cleaned),
                        "input": stats.input_total,
                        "output": stats.output_total,
                        "cache_read": stats.cache_read_total,
                        "cache_write": stats.cache_write_total,
                        "cache_ttl": "5m",
                        "cost_usd": round(stats.cost_usd, 6),
                        "cli_calls": stats.cli_calls,  # how many `claude -p` executions
                        "by_model": {
                            m: {**v, "cost_usd": round(v["cost_usd"], 6)}
                            for m, v in stats.by_model.items()
                        },
                    }
                )
                write_report()  # regenerate .kiln/usage-report.md from the full ledger
            output.notice(
                f"[exit] stored session {started} ({len(cleaned)} turns)"
                f"{' + summary' if summary else ''}"
                f"{f' + {added_facts} facts' if added_facts else ''} -> {STORE_FILE.name}"
            )
