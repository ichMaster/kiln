# How it works (in detail)

Step-by-step runtime mechanics, tables, and algorithms — current as of v1.3. For the design
overview see [architecture.md](architecture.md); for the state-machine concept and where it is
headed (declarative per-agent FSMs, simulation) see
[../spec/features/fsm.md](../spec/features/fsm.md). Ukrainian translation:
[how-it-works.uk.md](how-it-works.uk.md).

## Tick loop

The engine spins in `engine.run(...)`. The signature carries the seams the core is written against —
`run(ticks, live, channel, brain, output, paths, stop_event, config)` — so the same loop drives the
terminal, the dry-run demo, and a server-hosted agent without changing any logic. `brain` is the
model seam (`LiveBrain` in live mode, `MockBrain` in dry-run and tests, so nothing paid runs off the
happy path); `output` is where replies go (`ConsoleOutput` by default, the server bus when hosted);
`paths` is the per-agent persistence root; `config` is the per-agent calibration (an `AgentConfig`,
or `None` for the default agent, which reads the module globals).

Each tick does the same handful of things, in order:

1. **Drift the needs.** Every need moves by `DRIFT[k] × steps`. In live mode `steps` is how many
   ticks actually elapsed since the previous iteration (`round(elapsed / TICK_SECONDS)`, at least 1),
   measured with `time.monotonic()`. A blocking model call can span many seconds, so this "catch-up"
   ages the needs by the real time that passed rather than by one nominal tick. In dry-run time does
   not flow, so it is always exactly one tick and the demo stays deterministic.
2. **Fold in finished rotations.** If a background rotation completed (see below), its summary and
   facts are applied to the store here, on the agent thread, so only one thread ever writes the store.
3. **Decide whether to rest.** The rest gate is a hysteresis band: once `rest` reaches its threshold
   she stops engaging and only recovers, until `rest` falls back to `REST_WAKE`. Entering rest is
   announced once; while resting, slash commands still work and anyone who writes gets a short "I'm
   resting" acknowledgement rather than a real reply.
4. **Pick exactly one action, by priority.** The loop looks at everything that could happen this
   tick — a user message or slash command, a self-initiated reach-out or inner thought, a due session
   rotation, or nothing — and acts on the single highest-priority one. The order is **input first,
   then a self-trigger, then idle**; a self-trigger that loses to input is simply reconsidered next
   tick. This selection is the state machine described in the next section.
5. **Persist if something changed.** If the tick added a turn, the open session and its raw turns are
   written to the store immediately (real-time persistence), so a crash can't lose them and a client
   that attaches mid-conversation sees the live transcript.
6. **Emit a status snapshot.** Every tick — whether or not anything was said — the loop emits a
   `status` snapshot (the current state, the needs and their thresholds, session stats, the agent's
   name). This is what a TUI or web client renders its status bar and needs panel from.
7. **Sleep** for `TICK_SECONDS` in live mode (zero in dry-run).

`ticks=None` runs the loop forever (the live channel); an integer runs exactly that many ticks (the
demo). On exit — a normal stop, `/quit`, or Ctrl-C, all via `finally` — the loop saves the need
levels, prunes the session to real conversation (an all-noise session is dropped), writes the pruned
turns to the store, then summarizes the session and extracts durable user facts, and appends a line
to the usage ledger.

## The state machine

The engine's behaviour is a finite state machine. From the beginning the loop has "sat" in one mode
and moved to another when something happened; v1.3 lifts that machine out of the loop's `if/elif`
branches and writes it down explicitly as data in `kiln/fsm.py`, with the actions it can take kept in
a small registry in `engine.py`. Writing it as a table (rather than nested conditionals) is what lets
it be inspected, traced, and — later — declared per agent in YAML (v1.7) and simulated (v1.8). The
table reproduces the established behaviour exactly; nothing about what Agnika does changes.

For an engineering-level treatment — the needs→trigger→event→transition pipeline, the event queue,
the two coupling paths, the full transition matrix, and a worked example traced against it — see the
dedicated [state-machine.md](state-machine.md).

There are three parts: the **states** she can be in, the **events** that arrive, and the **transition
table** that says, for a given state and event, which **action** to run and which state to move to.

### States

| State | What it means | How you see it |
|---|---|---|
| `idle` | waiting; needs drifting, nothing to say | the resting-but-awake default |
| `responding` | answering a user, or speaking first (a reach-out) | her reply appears |
| `thinking` | forming a private inner thought | usually nothing shown (a thought is stored; roughly one in M is surfaced) |
| `cooling` | just finished a turn; cooldowns ticking down before idle | shown as `cooling` right after a reply |
| `resting` | too tired to engage (the rest gate); only recovering | the "I'm resting" acknowledgement; recovers in silence |

`idle`, `responding`, `thinking`, and `cooling` are the **active** states — from any of them the same
things can happen next (answer, reach out, think, rest, or fall idle); they differ only in what the
last tick did. `resting` is the one genuinely different state, because the rest gate changes how she
reacts. (`cooling` is defined in the state set now and becomes a live post-turn state within v1.3;
until then a turn returns straight to `idle`.)

### Events

One event is acted on per tick. The live ones:

| Event | Where it comes from |
|---|---|
| `user.message` | a normal line from the channel (`channel.poll()`) |
| `command` | a line starting with `/` — handled without touching the brain |
| `self_trigger` | a need crossed its threshold this tick (carries the need name) |
| `tick` | the heartbeat — emitted every tick, the lowest priority |
| `rotate.request` | the auto-rotate timer, or `/rotate` |

Three more names are **reserved** — defined but unused until their features land: `peer.message`
(agent-to-agent, v1.6), `room.message` (group rooms, v1.11), and `tool.result` (tools, v1.5).

### The event queue and priority

Each tick the producers turn today's sources into events and put them on a small queue; the loop then
drains the **single highest-priority** event and acts on it. The priority is what encodes "input
beats a self-trigger beats idle":

```
user.message = command   >   self_trigger   >   rotate.request   >   tick
```

So if you type at the same tick a reach-out was about to fire, your message wins and the reach-out is
reconsidered next tick — exactly the old behaviour. The producers are deliberately thin: they emit
events, they do not decide what to do (the table does that). Rotation is the one orthogonal case —
the `rotate` action only requests a rotation; the loop performs it alongside whatever action ran, so
a rotation coinciding with a turn behaves as it always has.

### The transition table

This is the whole machine. Read a row as: *in this state, on this event (when the guard holds), run
this action and move to this state.*

| From | Event | Guard | Action | To |
|---|---|---|---|---|
| active | `command` | — | `command` | `idle` |
| active | `user.message` | — | `respond` | `responding` |
| active | `self_trigger` | need is the reach-out need | `reach_out` | `responding` |
| active | `self_trigger` | need is the reflect need | `think` | `thinking` |
| active | `rotate.request` | — | `rotate` | `idle` |
| active | `tick` | `rest ≥ threshold` | `enter_rest` | `resting` |
| active | `tick` | otherwise | `idle` | `idle` |
| `resting` | `command` | — | `command` | `resting` |
| `resting` | `user.message` | — | `rest_ack` | `resting` |
| `resting` | `rotate.request` | — | `rotate` | `resting` |
| `resting` | `tick` | `rest ≤ REST_WAKE` | `wake` | `idle` |
| `resting` | `tick` | otherwise | `idle` | `resting` |

"active" means any of `idle` / `responding` / `thinking` / `cooling`. `advance(state, event, ctx)` is
the pure lookup: it walks the table in order and returns the first row whose state, event, and guard
all match. It has no side effects — the loop is what runs the action.

Two guards carry the subtlety:

- **The rest gate is hysteresis.** She enters `resting` when `rest` reaches its threshold (0.9) and
  leaves it only when `rest` falls back to `REST_WAKE` (0.85). The band between the two stops her from
  flip-flopping in and out of rest every tick.
- **Self-trigger routing is by configured need, not a hardcoded name.** A `self_trigger` becomes a
  `reach_out` or a `think` by comparing the need it carries against the agent's configured reach-out
  need and reflect need. Retuning which need drives the reach-out can't misroute it.

### Actions are tools

Each action name in the table resolves to a callable through an **action registry**. The built-ins
wrap what the loop has always done inline: `respond` (a user turn), `reach_out` (speak first — her
other needs pick the brain), `think` (a private inner thought), `idle` (a silent, recovering tick),
`enter_rest` / `rest_ack` / `wake` (the rest gate), `rotate` (request a session rotation), and
`command` (dispatch a slash line: quit, reload, rotate, or a forced-deep `/ask`). An action reads and
writes a small per-tick context — the turn/think primitives it needs, plus the outcome fields the
loop reads back (the new state, whether she reached out, whether a rotation was requested, and so on).

Keeping actions in a registry is deliberate: "actions are tools." The permission-scoped tool registry
in v1.5 extends this same seam with user-defined tools, and an agent's scope will gate which actions
it may fire. `respond` internally still routes between the cheap and deep brains (see
[Classification and routing](#classification-and-routing)); the table names the action, the brain
routing lives inside it.

### A tick-by-tick example

Starting from `idle`, with `rest` low:

1. You type "привіт". The input event outranks the tick, so `advance(idle, user.message)` runs
   `respond` and moves to `responding`. Your line and her reply are shown; the turn is persisted.
2. Next tick you're quiet. `advance(responding, tick)` runs `idle` and returns to `idle`, recovering
   a little `rest`.
3. Over many quiet ticks `connection` drifts up past 0.8. That tick a `self_trigger` for `connection`
   is produced; it outranks the tick, so `advance(idle, self_trigger)` runs `reach_out` and she
   speaks first. If she was tense or curious at that moment, the reach-out uses the deep brain or the
   session-wiki sub-agent instead of cheap chat.
4. After a long stretch of deep turns, `rest` climbs to 0.9. On the next quiet tick `advance` matches
   the rest guard, runs `enter_rest`, and she moves to `resting` — answering nothing but recovering
   until `rest` eases back to 0.85, when `wake` returns her to `idle`.

## Needs model

State is a set of needs, each in `0..1`, stored in `.kiln/needs.json`. Two forces act on every need:
a slow **drift** each tick, and **closure by an event** (a reply, or silence). The stored file is the
live levels; the model that tunes them — drift, satiation, thresholds — lives separately in
`state/needs_model.yaml` (calibration you edit), falling back to built-in defaults.

### The needs and their drift

| Need | What it means | Drift/tick | Role |
|---|---|---|---|
| `connection` | the urge for contact | +0.0010 | the frequent driver — the only need that reaches out |
| `reflection` | the pull to think something through | +0.0010 | drives the private inner thought |
| `curiosity` | the itch to ask | +0.0008 | arms the question monitor (she asks within a reply) |
| `intensity` | emotional tension | +0.0005 | discharged by deep turns; also routes turns to the deep brain |
| `novelty` | the need for something new | +0.0001 | routes a reach-out to the session-wiki sub-agent |
| `rest` | accumulated fatigue | −0.0050 | **rises with work, not drift**; recovers when idle |

Note `rest` drifts *downward*: fatigue is not something that accumulates on its own but something
**work** adds (a chat turn raises it a little, a deep turn a lot) and quiet removes. That is what
makes the rest gate meaningful — a run of expensive turns pushes `rest` up to its threshold, and only
silence brings it back down.

### Closure by events (`SATIATION`)

The central idea: **whichever branch answered is what decides which needs got closed.** A cheap chat
turn eases contact; a deep turn discharges tension; the session-wiki sub-agent is what actually feeds
novelty; a private thought discharges reflection; a reply that asks a question discharges curiosity;
and every kind of engagement adds fatigue while silence removes it.

| Event | connection | rest | novelty | intensity | reflection | curiosity |
|---|---|---|---|---|---|---|
| `chat` — Haiku reply (SDK) | −0.30 | +0.20 | 0 | −0.001 | — | — |
| `deep` — Opus reply (`claude -p`) | −0.60 | +0.40 | −0.0015 | −0.80 | — | — |
| `session-wiki` — the sub-agent reach-out | −0.60 | +0.40 | −0.90 | −0.15 | — | — |
| `idle` — a silent tick | 0 | −0.01 | +0.0001 | +0.0005 | — | — |
| `thought` — a private inner thought | — | — | — | — | −0.70 | — |
| `asked` — a reply that actually asked | — | — | — | — | — | −0.40 |

The resets are large relative to drift, so one event clearly satisfies a need on a calm, minute-scale
cadence rather than leaving it hovering just under threshold and re-firing every few seconds.
`apply_satiation` clamps at `0.0`; `drift` clamps at `1.0`. The satiation map is **per-agent**:
`session-wiki` has its own entry (novelty hard, barely tiring), and any agent without a bespoke entry
for a sub-agent falls back to the `deep` event.

The cycle this produces: needs accumulate → `connection` reaches out (cheaply by default, or via the
deep/session-wiki brain when she's tense or curious) → deep discharges `intensity`, session-wiki
discharges `novelty`, and both add `rest` → after enough work `rest` reaches the gate → she rests →
silence recovers `rest` and the cheap-first cadence resumes. Conserving Opus falls out of this: the
expensive brains only run when a real need pushes for them.

## Classification and routing

When the brain kicks in on **user input**, `classify(prompt, state)` returns `(class, agent)`, with
class one of `chat | think | tools | tool`, decided in this priority order:

1. explicit **tool markers** (`TOOL_HINTS`: `файл`, `запусти`, `пошук`…) → `tools` (deep with
   `--allowedTools`);
2. explicit **reasoning markers** (`THINK_HINTS`: `чому`, `поясни`, `проаналізуй`…) → `think`;
3. **ambient high needs pick the deeper brain** (the same map as a reach-out, `REACH_OUT_MODELS`):
   `intensity ≥ 0.75` → `deep` (Opus); else `novelty ≥ 0.85` → `tool` (the session-wiki sub-agent).
   So when she's tense or curious, even a plain turn gets the deeper brain;
4. a high **state weight** (`turn_weight = 0.55·intensity + 0.45·connection`, clamped, vs
   `THINK_THRESHOLD = 0.85`) → `think`;
5. otherwise → `chat`.

`respond()` maps the class to a branch and a satiation event: `chat → "chat"`, `think`/`tools →
"deep"`, and `tool →` its per-agent event (e.g. `session-wiki`). A reach-out bypasses this
classification entirely — the branch is chosen by `reach_out_branch` and passed to
`respond(force=...)`.

## Self-triggers (speaking without being spoken to)

Two needs make the engine act on its own, and a third arms a subtler behaviour:

- **`connection` → a reach-out.** When loneliness crosses its threshold (0.8), `select_self_trigger`
  fires a proactive turn. Which brain answers is chosen at that moment by her other needs
  (`reach_out_branch`): `intensity ≥ 0.75` → deep (Opus); else `novelty ≥ 0.85` → the session-wiki
  sub-agent (a fresh external fact); else cheap chat. So Opus and session-wiki **never self-initiate**
  — they only shape a connection-driven message. This is the `reach_out` action in the table.
- **`reflection` → a private inner thought.** When it crosses its threshold (0.6),
  `select_thought_trigger` fires `_think`: a private thought via the cheap brain, generated against an
  ephemeral history so the real transcript is untouched, stored hidden (roughly one in M is later
  surfaced in chat). This is the `think` action, and it discharges `reflection`.
- **`curiosity` → the question monitor.** Crossing its threshold (0.65) arms a monitor rather than
  sending anything; while armed, a reply that actually asks a question fires the `asked` satiation and
  discharges curiosity (curious → asks → sated). Falling back below the threshold disarms it.

`rest` also has a threshold, but it drives the rest gate (sleep), not a message.

### Anti-spam guards (`TriggerBook`)

The self-triggers keep runtime state, not persisted across sessions — two fields per need:

| Field | What it means | Default |
|---|---|---|
| `armed[need]` | ready to fire on the next upward crossing | `True` |
| `cooldown[need]` | ticks of enforced silence remaining after firing | `0` |

**Hysteresis (`armed`)** gives exactly one turn per upward threshold crossing: firing discharges it
(`armed = False`), and it re-arms only when the need falls back below the threshold — which the reply
itself usually causes (a deep reach-out drops `connection` by 0.6). **Cooldown** (`SELF_COOLDOWN` =
`THOUGHT_COOLDOWN` = 5) is a hard floor of silent ticks after firing, even if the need re-armed
quickly. In practice hysteresis binds tighter; the cooldown matters only when a reply barely eases
the need. If several needs are over threshold at once, the one furthest past it speaks this tick and
the others wait their turn. The prompt text is picked at random from `state/prompts.md` (the
per-need sections), with a code fallback.

## Input channels

Input is abstracted behind a `poll() -> str | None` method, so the loop never blocks on it:

- **`ScriptedChannel({tick: text})`** — deterministic, input bound to tick numbers; the demo and
  tests.
- **`StdinChannel`** — live: a daemon thread reads `stdin` into a queue; `poll()` takes the next line
  or `None`, so a message is picked up on the following tick.
- **`ServerChannel`** (v1.1, `server/bus.py`) — drains a per-agent inbox queue fed by WebSocket
  clients; the same seam, so the loop is identical whether driven by a terminal or the network.

## Conversation history

Both branches share one list of session turns (`history`), each `{"role", "text", "at"}`, with no
trimming — everything accumulates and rides in the prompt. The cheap branch sends the whole history
as a `messages` array (`to_messages`); the deep branch, a subprocess with no session between calls,
gets it flattened into the prompt as a text transcript (`to_transcript`, with `Користувач:` / `Ти:`
labels). Each turn appends the user message first, then the reply, so switching branches never loses
context.

## The two brains

Both sit behind the **`Brain` seam** (`brain.py`); `respond()` calls the model only through it, so the
core knows nothing of the SDK or the CLI. `LiveBrain` makes the real calls; `MockBrain` returns canned
replies with synthetic usage (dry-run and every test go through it — zero paid calls).

- **Chat — `brain.chat` (Haiku, Anthropic SDK).** A plain Messages API call with the system prompt
  and the whole history as the `messages` array. Cheap, fast, no tools. A network or API error is
  caught and returned as a `(chat error: …)` string rather than crashing the loop.
- **Deep — `brain.deep` (Opus, `claude -p`).** A subprocess:
  `claude -p --model <deep> --append-system-prompt <system> [--allowedTools …]`, with
  `--output-format json` so the reply text and token usage (and the CLI's actual cost) come back
  together. Prior history is embedded as a transcript; the prompt goes in via stdin (so a trailing
  positional isn't swallowed by the variadic `--allowedTools`). `--allowedTools` is added only for the
  `tools` class. A nonzero exit degrades to a `(deep error: …)` string.
- **Sub-agent — `brain.tool` (`claude -p --agent <name>`).** A named Claude Code sub-agent (e.g.
  `session-wiki`, which reads the recent session, picks a topic, fetches a Wikipedia fact, and returns
  one Ukrainian paragraph in Agnika's voice). It is a whole turn handed to the sub-agent, distinct
  from the `tools` class (deep + allowed tools).

## Memory, facts, thoughts, and transcripts

Everything an agent persists lives under `.kiln/` (the flat root for the default agent, or
`.kiln/{id}/` for a companion), gitignored so a run never dirties git.

**On start**, `load_memory()` reads the stored past-session summaries as one block, and
`build_system(canon, memory, facts, world, mood, thoughts)` stitches the system prompt for *both*
branches from several layers, each optional:

- the **canon** — the persona/voice (`state/canon.md`, or `DEFAULT_CANON`);
- the **memory** summaries of past sessions and a **facts** digest about the user;
- a **world** block composed per turn (the clock, the user's location, and the previous session's
  tail);
- a **mood** block per turn (each need's level with a Ukrainian band and a behavioural cue, then the
  day's biorhythm computed once at session start);
- a **thoughts** block (the recent inner thoughts, deduped against the live turns).

**During the session**, turns are persisted in real time and a private thought may be stored on a
`reflection` self-trigger. **On exit** (via `finally`), the session is pruned to real conversation (an
all-noise session is dropped), the pruned turns are written to the store first — before the
possibly-failing summary — then the session is summarized (Haiku, cheap, not Opus), durable user facts
are extracted (Opus, deduped) and folded in, and a usage-ledger line is appended and the report
regenerated.

The store (`.kiln/store.json`, atomic write with a `.bak`) holds sessions and their raw turns,
summaries, facts, and thoughts. The full raw transcripts double as the corpus for retrieval (RAG,
v1.4). (`state/memory.md` and a top-level `history/` are legacy; a one-shot `migrate_legacy` folds
any old summaries and transcripts into the store on first run.)

**Rotation** starts a fresh session without stopping the agent. The auto-rotate timer
(`ROTATE_EVERY_HOURS`, `0` = off) or `/rotate` cuts to a new session immediately; the slow work
(summary + facts) of the old one runs on a worker thread and is folded back in on the agent thread a
few ticks later, so the agent never pauses and only one thread writes the store.

## Configuration and calibration

Configuration resolves in three layers — **environment variable, then a committed YAML file, then a
built-in default** — via the `_opt_*` helpers.

- **`.env`** (gitignored) holds *only* secrets and personal fields — `ANTHROPIC_API_KEY`,
  `USER_NAME` / `USER_LOCATION` / `TIMEZONE` — plus the launch flags `KILN_LIVE` / `KILN_SERVE` /
  `KILN_HOME` (never committed defaults; a committed `live: true` would be dangerous). A tiny
  dependency-free `KEY=VALUE` parser reads it, and a real environment variable always wins.
- **`state/config.yaml`** (committed) holds the agent tunables: the model ids, `tick_seconds`,
  `think_threshold`, the thoughts/facts/memory knobs, the awareness toggles (world/mood/biorhythm),
  `agent_name`, and so on.
- **`state/needs_model.yaml`** (committed) is the need **model** — `drift` / `satiation` /
  `need_triggers` plus the trigger-wiring scalars (`reach_out_need`, `reflect_need`, `rest_wake`,
  the cooldowns). Editing this is how you tune an agent's temperament.
- **`state/mood.json`** holds the need/biorhythm bands (thresholds + Ukrainian names) and the
  behavioural cues; **`server.yaml`** holds the tick-server host/port and the list of agents to boot.

A real environment variable named for the key (UPPER_SNAKE, e.g. `TICK_SECONDS`) overrides the file.
Per-agent, all of this is bundled into an `AgentConfig` (`AgentConfig.for_agent(id)`); the default
agent runs with `config=None`, which reads the module globals and reproduces the pre-v1.2 behaviour
byte-for-byte.

## Slash commands

A line starting with `/` is intercepted before classification (`handle_command`) and never reaches
the brain:

| Command | Action |
|---|---|
| `/status` | turns, the hottest need, mode, and all need levels |
| `/needs` | current need levels |
| `/mood` | the `## Настрій` block exactly as it goes into the prompt this turn |
| `/thoughts` | recent inner thoughts, each marked internal or surfaced |
| `/self` | toggle proactive reach-outs on/off (per session) |
| `/prompt` | the system prompt + the messages array sent to the model |
| `/usage` | session tokens, `claude -p` call count, estimated cost, report path |
| `/report` | regenerate `.kiln/usage-report.md` from the ledger |
| `/ask <text>` | force a deep (Opus) turn, past the classifier |
| `/reload` | re-read canon/prompts/memory and rebuild the system prompt, no session drop |
| `/rotate` | close+summarize this session and start a fresh one, without pausing |
| `/clear` | clear the session history |
| `/help` | the list of commands |
| `/quit` (`/exit`, `/q`) | exit (the session closes into the store) |

## Running more than one agent

A single server process can host several agents at once, each on its own thread with fully isolated
state (needs, memory, persona, calibration, and scope). You attach a client to one at a time. That is
its own topic — see [multi-agent-setup.md](multi-agent-setup.md) and [server.md](server.md).

## Where this is going

The near-term roadmap (see [../spec/ROADMAP.md](../spec/ROADMAP.md)) builds on the state machine and
the seams above: finishing the explicit FSM (a real `cooling` state and per-transition tracing),
semantic recall over the stored transcripts (RAG, v1.4), the permission-scoped tool registry that
extends the action registry (v1.5), agent-to-agent messages (v1.6), per-agent FSMs declared in YAML
(v1.7), and FSM simulation and calibration (v1.8).
