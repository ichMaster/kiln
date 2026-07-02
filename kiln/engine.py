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
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path

from . import fsm
from .actions import ActionContext, ActionRegistry, default_registry  # noqa: F401
from .brain import Brain, LiveBrain, MockBrain
from .channels import ScriptedChannel, StdinChannel  # noqa: F401
from .commands import handle_command
from .config import (
    AGENT_NAME,
    BIORHYTHM,
    CHAT_MODEL,
    DEEP_MODEL,
    DEFAULT_AGENT,
    DRIFT,  # noqa: F401 (re-exported for tests: eng.DRIFT)
    MOOD_AWARENESS,
    NEED_TRIGGERS,
    REACH_OUT_NEED,
    RECENT_MESSAGES,
    REFLECT_NEED,
    REST_WAKE,
    ROTATE_EVERY_HOURS,
    SATIATION,  # noqa: F401 (re-exported for tests: eng.SATIATION)
    SELF_SILENCE_NOTE,
    STORE_FILE,
    THOUGHT_VISIBLE_EVERY,
    THOUGHTS_ENABLED,
    THOUGHTS_IN_PROMPT,
    TICK_SECONDS,
    TIMEZONE,
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
from .needs import (
    State,
    TriggerBook,
    _thought_visible,
    apply_satiation,
    drift,
    load_state,
    reach_out_branch,  # noqa: F401 (re-exported for tests)
    save_state,
    select_self_trigger,
    select_thought_trigger,
    update_curiosity_monitor,
)
from .output import ConsoleOutput, Output
from .report import write_report
from .routing import (  # noqa: F401 (classify + turn_weight re-exported for tests)
    classify,
    is_curiosity_reply,
    respond,
    turn_weight,
)
from .stats import SessionStats
from .store import add_facts, add_thought, load_store, remove_session, save_store, upsert_session
from .world import world_block

# === Engine (loop) ==========================================================


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


def run(
    ticks: int | None = 12,
    live: bool = False,
    channel=None,
    brain: Brain | None = None,
    output: Output | None = None,
    paths: AgentPaths | None = None,
    stop_event: threading.Event | None = None,
    config: AgentConfig | None = None,
    trace: Callable[[dict], object] | None = None,
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
    `trace` (v1.3, KILN-066): an optional sink called once per tick with a `fsm.trace_record` of the
    FSM step (state/event/guard/action/next_state + needs). None = off (no records, cheap).
    """
    if channel is None:
        channel = ScriptedChannel()
    if output is None:
        output = ConsoleOutput()
    if paths is None:
        paths = AgentPaths.for_agent()  # v1.1: default agent -> today's flat global paths
    if brain is None:
        if live:
            # v1.4: the live brain carries this agent's security profile + workspace, so every
            # `claude -p` sub-agent spawn is built + gated by it. The default agent (config=None)
            # loads its committed state/security.yaml; a companion carries config.security.
            from .security import load_security

            profile = (
                config.security
                if config is not None
                else load_security(paths.state_dir / "security.yaml")
            )
            brain = LiveBrain(
                profile=profile,
                paths=paths,
                deep_model=(config.deep_model if config is not None else DEEP_MODEL),
                agent_id=(config.agent_id if config is not None else DEFAULT_AGENT),
            )
        else:
            brain = MockBrain()
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
    facts_model = config.facts_model if config is not None else None  # None → memory.FACTS_MODEL
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
    facts = digest_facts(live, paths.store_file, facts_model)  # v0.6: N-line digest of user facts
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
                "facts": extract_facts(cleaned, existing_facts, live, facts_model),
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
    fsm_state = fsm.State.IDLE  # KILN-065: the carried FSM state (idle / cooling; RESTING via flag)
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
            state_in = fsm.State.RESTING if resting else fsm_state  # rest gate stays a flag
            fctx = fsm.Ctx(
                needs=state.needs,
                rest_threshold=rest_threshold,
                rest_wake=rest_wake,
                reach_out_need=config.reach_out_need if config is not None else REACH_OUT_NEED,
                reflect_need=config.reflect_need if config is not None else REFLECT_NEED,
                cooldowns=dict(tg.cooldown),  # KILN-065: COOLING lasts while any cooldown is active
            )
            action, next_state = fsm.advance(state_in, event, fctx)
            if trace is not None:  # KILN-066: off by default; one record per tick when a sink is on
                trace(
                    fsm.trace_record(
                        total_ticks,
                        state_in,
                        event,
                        action,
                        next_state,
                        {k: round(v, 4) for k, v in state.needs.items()},
                        guard=fsm.guard_name(fsm.match(state_in, event, fctx)),
                    )
                )
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
            # carry the next FSM state (KILN-065). The rest gate is the flag (above): while resting
            # it owns the state, so hold IDLE — what to be on the wake tick, when the flag clears.
            fsm_state = fsm.State.IDLE if resting else next_state
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
            # KILN-023: extract durable user facts (v1.4: SDK/Sonnet) and fold them in (deduped).
            existing_facts = [f.get("text", "") for f in store.get("facts", [])]
            added_facts = add_facts(
                store, extract_facts(cleaned, existing_facts, live, facts_model), started, stamp
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
