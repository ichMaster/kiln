# How it works (in detail)

Step-by-step runtime mechanics, tables, and algorithms. For a design overview, see
[architecture.md](architecture.md).

## Tick loop

The engine spins in `run(ticks, live, channel)`. On each tick, in order:

1. **`drift(state, steps)`** — every need grows by `DRIFT[k] × steps`
   (clamped at `1.0`). In live mode, `steps` is how many ticks **actually**
   elapsed since the previous iteration (`round(elapsed / TICK_SECONDS)`, minimum 1),
   measured via `time.monotonic()`. This way a blocking model call "ages"
   the needs in proportion to the time spent — **drift catch-up**; when
   `steps > 1`, a `[time] …` line is printed. In dry-run there is always exactly 1 tick
   (so the demo stays deterministic).
2. **`channel.poll()`** — non-blocking; takes the next user message or
   `None`.
3. **Action selection by priority:**
   - there is **input** → if it's a slash command, `handle_command` handles it (the brain
     isn't touched); otherwise `respond()` classifies and replies;
   - there is no input, but a **self-trigger** fired → `respond(force=...)` on the
     branch specified by the trigger;
   - nothing → **silence**: `apply_satiation(state, "idle")`.
4. **`time.sleep(TICK_SECONDS)`** in live mode (in dry-run — `0`).

On exit (normal, `/quit`, or Ctrl-C — via `finally`): `save_state`,
then `save_session` (transcript) and `summarize` + `save_summary` (summary).

`ticks=None` spins the loop forever (for the live `StdinChannel`); an integer means
exactly that many ticks (for the demo).

## Needs model

State is a set of needs, each `0..1` (`state/needs.json`). Two forces: slow
**upward drift** every tick and **closure by events**.

### Drift and what each need means

| Need | What it means | Drift/tick (`DRIFT`) | Role |
|---|---|---|---|
| `connection` | the urge for contact | +0.0020 | the **cheap chat** driver — fires most often |
| `novelty` | the need for something new | +0.0012 | the **expensive deep** driver — leads the deep turn |
| `intensity` | emotional tension | +0.0008 | discharged by every deep turn; hovers, rarely leads |
| `rest` | accumulated fatigue | +0.0004 | mostly activity-driven; recovers in silence, rarely fires |

### Closure by events (`SATIATION`)

The key idea: **whichever branch answered is what determines which needs got closed.**
A Claude call (`deep`) satiates more deeply than cheap chat.

| Event | connection | rest | novelty | intensity |
|---|---|---|---|---|
| `chat` — Haiku reply (Anthropic SDK) | −0.50 | +0.01 | −0.05 | −0.08 |
| `deep` — Claude CLI reply (reasoning/tools) | −0.40 | +0.04 | −0.45 | −0.40 |
| `idle` — silence (a tick without a reply) | — | −0.005 | — | +0.0003 |

The resets are **large relative to drift**, so one event clearly satisfies a need (a calm,
minute-scale cadence) rather than leaving it hovering just under threshold and re-firing every
few seconds. Contact (of any kind) eases `connection`. `deep` is the "filling meal": it closes
`novelty` and discharges `intensity` the most — and it **tires** (`rest` *rises*, it does not
fall). `idle` (silence) is what recovers `rest`, and being unanswered builds a little
`intensity` (restlessness). Hence the cycle: needs accumulate → push toward the expensive `deep`
→ it eases them deeply (and tires) → a long stretch of cheap chat and silence (which rests).

`apply_satiation` clamps levels at `0.0`; `drift` clamps them at `1.0`.

**Only connection self-triggers; the other needs pick the brain.** When **connection** crosses its
threshold she reaches out, and `reach_out_branch(state)` chooses the model: `intensity` over its
threshold → deep/Opus, else `novelty` over its → the **`session-wiki`** sub-agent (`brain.tool` →
`claude -p --agent session-wiki`), else a light `chat`. The session-wiki sub-agent reads the recent
session, picks a curiosity topic, fetches a **Wikipedia** fact, and returns a single Ukrainian
paragraph in Agnika's voice — her reach-out. So Opus and session-wiki **never self-initiate**; they
only shape a connection-driven message. Satiation is **per-agent** — `SATIATION["session-wiki"]`
drops `novelty` hard (and barely tires, unlike an opus `deep` turn), falling back to the `deep`
event for any agent without its own entry. (`MockBrain.tool` returns canned text, so dry-run and
tests make no network/subprocess calls.)

## Classification and routing

When the brain kicks in on **user input**, `classify(prompt, state)` returns
`(class, agent)`, class ∈ `chat | think | tools | tool`, in priority order:

1. explicit **tool markers** (`TOOL_HINTS`: `файл`, `запусти`, `пошук`…) → `tools`
   (deep + `--allowedTools`);
2. explicit **reasoning markers** (`THINK_HINTS`: `чому`, `поясни`, `проаналізуй`…) → `think`;
3. **ambient high needs pick the deeper brain** — the same map as a self-trigger
   (`REACH_OUT_MODELS`): `intensity ≥ 0.75` → `deep` (opus); else `novelty ≥ 0.85` → `tool`
   (`session-wiki`). So when she's intense or curious, even a plain user turn gets the deeper
   brain, not cheap chat;
4. a high **state weight** → `think`;
5. otherwise → `chat`.

State weight:

```
turn_weight = 0.55 * intensity + 0.45 * connection      # clamped to 0..1
```

`respond()` maps the class to a branch and a satiation event: `chat → "chat"`,
`think`/`tools → "deep"`, `tool → its per-agent event` (e.g. `session-wiki`).

> **Self-triggers bypass classification.** The branch is chosen by `reach_out_branch`
> (not message markers) and passed to `respond(force=...)`.

## Self-triggers (the engine speaks up on its own)

Only **connection** (`REACH_OUT_NEED`) self-triggers a proactive message — when it crosses its
threshold (loneliness), the engine initiates a turn (`select_self_trigger`). **Which brain answers**
is then chosen by her other needs at that moment (`reach_out_branch`):

| Condition at fire time | Branch | Why |
|---|---|---|
| `intensity ≥ 0.75` | deep (Opus) | tense → a deep reply |
| else `novelty ≥ 0.85` | `session-wiki` (Sonnet) | curious → a fresh external fact |
| else | chat (Haiku) | calm → light contact |

So Opus and session-wiki **never self-initiate** — they only *shape* a connection-driven reach-out
(`intensity` also routes *user* turns to Opus via `turn_weight`). `rest` crossing drives the **rest
gate** (sleep), not a message. Priority order is `REACH_OUT_MODELS = (intensity, novelty)`.

### Trigger state (`TriggerBook`)

Separately from the needs, the engine keeps **runtime trigger state** (not persisted across
sessions) — two fields per need:

| Field | What it means | Default |
|---|---|---|
| `armed[need]` | whether the trigger is ready to fire | `True` |
| `cooldown[need]` | how many more ticks to stay silent after firing | `0` |

These two fields provide the two guards against "spam" — without them, a need that sits above
its threshold would launch a turn **every tick**.

### The `select_self_trigger` algorithm (step by step)

Called every tick **when there is no user input**. Returns `(need, action)`
for a self-call, or `(None, None)`.

1. **Cooldowns tick down.** For each need, if `cooldown > 0`, decrement by 1.
2. **Collect candidates.** For each need, compare its level against its threshold:
   - level **below** the threshold → `armed = True` (**re-arm**: the trigger is
     again ready for the next upward crossing);
   - level **≥** the threshold **and** `armed` **and** `cooldown == 0` → it's a candidate,
     with an "overshoot" of `overshoot = level − threshold`.
3. **No candidates** → `(None, None)`: the engine stays silent, the tick becomes `idle`.
4. **Selection.** Among the candidates, the one with the **largest overshoot** is taken — the need
   that has gone furthest past its threshold.
5. **Firing.** For the chosen need: `armed = False` (**discharge**) and
   `cooldown = SELF_COOLDOWN`. We return `(need, action)` → `run()` does
   `respond(prompt, force=action)`.

So the firing condition is — **above the threshold AND `armed` AND `cooldown == 0`**.

### Why both guards

- **Hysteresis (`armed`).** Gives exactly **one** turn per upward threshold crossing.
  Immediately after firing the trigger is discharged (`armed = False`) and won't fire
  again until the need **falls below** the threshold and re-arms (step 2). In
  practice it's the reply itself that eases the need: a self-turn on `novelty` (deep) lowers
  novelty by 0.40 → it drops below the threshold → re-arm.
- **Cooldown (`SELF_COOLDOWN` = 5).** A hard floor: even if the need quickly
  re-armed, there are still `N` ticks of silence for that specific need. Usually hysteresis
  dominates (the reply already knocks the need below the threshold), and the cooldown is an extra
  guard against a fast repeat.

### Example: `novelty` (threshold 0.85, action deep, drift +0.015/tick)

| Tick | novelty | armed | cooldown | What happened |
|---|---|---|---|---|
| k−1 | 0.84 | `True` | 0 | below the threshold → re-armed, waiting |
| **k** | **0.86** | `True`→`False` | 0→**5** | crossed the threshold → **fired** (deep); novelty −0.40 |
| k+1 | 0.46 | `False`→`True` | 4 | below the threshold → re-armed; but far from the threshold |
| k+2…k+5 | ↑ slow drift | `True` | 3 → 0 | silent; cooldown burns down |
| ~k+27 | **0.85** | `True` | 0 | above the threshold again and armed → may fire |

You can see that here **hysteresis** (the need dropped by 0.40 and has ~26 ticks of drift to climb
back) binds more tightly than the cooldown (5 ticks). The cooldown becomes decisive only when
the reply barely eases the need.

**One self-turn per tick:** if several needs are above their thresholds at once, this tick
the one with the largest overshoot speaks; the rest wait for later ticks, each according to
its own `armed`/`cooldown`. (And the reply for the chosen need, via `apply_satiation`,
often knocks neighboring needs below their thresholds too.)

The self-trigger's prompt is picked at random from a list in `state/prompts.md` (the
`[потреба]` section), with a fallback generated in code.

## Input channels

Input is abstracted into a channel with a `poll() -> str | None` method:

- **`ScriptedChannel({tick: text})`** — deterministic: input is bound to
  tick numbers. For the demo and tests.
- **`StdinChannel`** — live: a background daemon thread reads `stdin` into a queue;
  `poll()` non-blockingly takes the next line (or `None`). So the tick loop doesn't
  stall waiting for input — a message is picked up on the next
  tick.

## Conversation history

A list of all session messages shared by both branches (`history`), with no trimming
or summarization — we accumulate everything and append it to the prompt. Each item is
`{"role": "user"|"assistant", "text": ...}`.

- **Chat** (SDK): the whole history goes in as a `messages` array (`to_messages`).
- **Reasoning/tools** (CLI): history is embedded in the prompt as a text transcript
  (`to_transcript`, with `Користувач:` / `Ти:` labels), because the subprocess holds no session
  between calls.

Each `respond()` turn: first the user's message is appended to `history`,
then the engine's reply. The stream is shared, so switching branches doesn't lose
context.

## The two branches in detail

**CHAT — `chat_reply` (Haiku, Anthropic SDK).** The `anthropic` import is local
(dry-run stays dependency-free); `Anthropic()` takes the key from
`ANTHROPIC_API_KEY`. The call:

```python
Anthropic().messages.create(
    model=CHAT_MODEL, max_tokens=512,
    system=system,                  # canon + long-term memory
    messages=to_messages(history),  # the whole stream, including the current turn
)
```

The reply is a list of blocks; we take the first text one. A network/limit/API error
doesn't crash the loop — a `(chat error: …)` string is returned.

**REASONING/TOOLS — `deep_reply` (Opus, `claude -p`).** The subprocess:

```
claude -p --model DEEP_MODEL --append-system-prompt <system> [--allowedTools …] <prompt>
```

Prior history goes into the prompt as a text transcript; the current message comes at
the end. `--allowedTools` is added only for the `tools` class (`DEEP_TOOLS`).

## Long-term memory and session transcripts

**On start:** `load_memory()` reads all past summaries as a single text, and
`build_system(canon, memory)` stitches them together with the canon into the system prompt of both
branches. This is how the engine "remembers" earlier conversations. `load_canon()` takes the persona from
`state/canon.md` (fallback — `DEFAULT_CANON`).

**On exit** (`finally`):

1. `save_session(history, live, started)` — writes the **raw** transcript to
   `history/session-<stamp>.json` (`{session, started_at, ended_at, mode, turns,
   history}`, `ensure_ascii=False`). It's saved **first**, so that a possible
   `summarize` failure can't swallow the transcript; a same-second collision is sidestepped with a `-2`,
   `-3`, … suffix.
2. `summarize(history, live)` — a concise summary via `claude -p`
   (in dry-run — a stub).
3. `save_summary(text)` — appends the summary to `state/memory.md` with a date.

`history` and `memory.md` are append-only, with no trimming yet.

## Configuration and calibration

All the constants are in `config.py`. `load_dotenv()` (its own minimal
`KEY=VALUE` parser, dependency-free) reads `.env` from the root **before** the constants are
defined. It uses `os.environ.setdefault` — **a real environment variable always
wins** over `.env`.

Set from `.env`: `CHAT_MODEL`, `DEEP_MODEL`, `TICK_SECONDS`,
`THINK_THRESHOLD`, `SELF_COOLDOWN` (plus `KILN_LIVE`, `ANTHROPIC_API_KEY` for
live mode).

The rest of the knobs are constants in `config.py`:

| Knob | What it tunes |
|---|---|
| `DRIFT` | drift speed, separately for each need |
| `SATIATION` | how much each event (chat/deep/idle) closes the needs |
| `NEED_TRIGGERS` | the threshold and branch of the self-trigger for each need |
| `DEEP_TOOLS` / `DEEP_SKILLS` | the allowed tools and skills for the reasoning branch |
| `THINK_HINTS` / `TOOL_HINTS` | marker words for classification |
| the weights in `turn_weight()` | the contribution of intensity/connection to the turn weight |

The structured knobs (the `DRIFT`/`SATIATION`/`NEED_TRIGGERS` dicts) stay in
code — they don't fit a flat `KEY=VALUE`.

## Slash commands

A line starting with `/` is intercepted **before** classification
(`handle_command`), so it doesn't go to the brain as a message:

| Command | Action |
|---|---|
| `/status` | turns, the hottest need, mode, all needs |
| `/needs` | current need levels |
| `/memory` | contents of long-term memory (`memory.md`) |
| `/history` | the latest turns of the session stream |
| `/ask <text>` | a forced Claude call (deep), past the classifier |
| `/clear` | clear the session history |
| `/help` | the list of commands |
| `/quit` | exit (the summary and transcript are saved regardless) |

## Extension points ("Next")

- Finer classification: currently by marker words; eventually — by intent.
- Trimming the session history (a window of the last N turns) — currently no trimming.
- Trimming/merging long-term memory: `memory.md` only grows over time.
- Per-turn timestamps in transcripts (currently — only the session time) for finer RAG.
- Typed SDK exceptions in `chat_reply` instead of one broad `except`.
