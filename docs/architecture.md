# Architecture

A high-level view of kiln. Runtime details, tables, and algorithms — see
[how-it-works.md](how-it-works.md).

## The idea

kiln is a chat engine where **state drives when and which model to call**. The
engine runs in a loop of short **ticks**. Each tick is cheap and local: a set of
**needs** slowly drifts upward on its own, without any call to the model. The
model (the "brain") only kicks in on a condition — either the user wrote
something, or a need crossed its threshold. And when the brain does kick in, the
turn is **classified** and routed into one of two branches.

Hence the main property: the expensive model (Opus) works rarely, while most
ticks are just free local state drift or cheap chat.

## Modules

The logic is split into modules with a simple directed dependency (no cycles):
`config` / `history` / `usage` (leaves) → `memory` → `commands` → `engine`.

| File | What it contains | Key symbols |
|---|---|---|
| [`config.py`](../config.py) | paths, `.env`, calibration knobs | `load_dotenv`, `DRIFT`, `SATIATION`, `NEED_TRIGGERS`, `THINK_THRESHOLD`, `CHAT_MODEL`/`DEEP_MODEL`, `DEFAULT_CANON` |
| [`history.py`](../history.py) | the shared session thread | `ROLE_USER`/`ROLE_BOT`, `to_messages`, `to_transcript` |
| [`usage.py`](../usage.py) | model log (tokens) + chat colors | `log_model`, `take_usage`, `print_tech`, `_c`, `BOT_NAME`, `*_COLOR` |
| [`memory.py`](../memory.py) | long-term memory + prompts/canon + transcripts | `load_memory`, `load_canon`, `load_prompts`, `summarize`, `save_summary`, `save_session`, `build_system` |
| [`commands.py`](../commands.py) | slash commands (before classification) | `handle_command` |
| [`engine.py`](../engine.py) | core + loop + `__main__` | `State`, `drift`, `apply_satiation`, `classify`, `chat_reply`, `deep_reply`, `ScriptedChannel`/`StdinChannel`, `respond`, `run` |

`engine.py` runs as `__main__`, so **no module imports it**: constants are moved
out into `config.py`, and `/ask` does its deep turn back in `run()` (which is why
`commands.py` doesn't depend on the core).

## The two branches (the "two brains")

The heart of the architecture — **which branch answered is what determines what
happened to the state**.

- **CHAT → Haiku via the Anthropic Messages API** (`chat_reply`). Ordinary
  conversation: greetings, replies, light answers. Cheap, fast, no tools. A real
  SDK call (`anthropic`); the key comes from `ANTHROPIC_API_KEY`. The whole
  session history goes in as a `messages` array, the canon + memory go in
  `system`.
- **REASONING / TOOLS → Claude as an external process** (`deep_reply`,
  `claude -p`). Here the model (Opus), the allowed tools (`--allowedTools`), and
  skills are specified. More expensive, but with reasoning and access to tools.
  The subprocess holds no session between calls, so the history is embedded in
  the prompt as a text transcript.

Both branches receive the **same** system prompt (`build_system`) — the canon
(persona) plus long-term memory — so the voice stays consistent regardless of
branch.

## Data flow on a tick

```
        ┌──────────────────── tick (TICK_SECONDS) ────────────────────┐
        │                                                              │
  drift(state)                          # needs drift upward           │
        │                                                              │
  channel.poll() ── input? ─┬── yes ─▶ slash command? ─ yes ─▶ handle_command
        │                   │                  │ no                    │
        │                   │                  ▼                       │
        │                   │            classify() ─▶ chat|think|tools │
        │                   │                  │                       │
        │                   │                  ▼                       │
        │                   │            respond(...) ─┬─ CHAT ─▶ chat_reply  (Haiku/SDK)
        │                   │                          └─ DEEP ─▶ deep_reply  (Opus/CLI)
        │                   │ no                              │          │
        │                   ▼                                 │          │
        │          select_self_trigger(state)  ── fired? ─────┤          │
        │            (threshold + hysteresis + cooldown)      │ yes      │
        │                   │ no (silence)                     ▼          │
        │                   ▼                          respond(force=...) │
        │            apply_satiation(state,"idle")            │          │
        │                                                     ▼          │
        └────────────── apply_satiation(state, event) ◀── which branch ──┘
                         (chat | deep | idle)            answered
```

**Priority on a tick:** user input > self-trigger > silence. If there's both
input and a trigger — the input is handled this tick, and the trigger is checked
on the next tick.

## State and persistence

Persistence splits into committed **config** under `state/` and generated **runtime**
under `.kiln/` (gitignored); `run()` creates both as needed, so a fresh clone doesn't crash:

| File | Role | Who writes / reads |
|---|---|---|
| `.kiln/needs.json` | live need levels `0..1` (generated; rewritten each run) | `load_state` / `save_state` |
| `state/canon.md` | the canon — persona/voice (system prompt of both branches) | `load_canon` (fallback — `DEFAULT_CANON`) |
| `state/prompts.md` | self-trigger prompts per need | `load_prompts` |
| `state/memory.md` | long-term memory: summaries of past conversations (generated, not committed) | `save_summary` / `load_memory` |
| `history/session-*.json` | raw session transcripts for RAG (generated, not committed) | `save_session` |
| `.env` | models + scalar knobs (local, not committed) | `load_dotenv` |

The two lines of cross-session memory are intentionally different:
- **`memory.md`** — concise *summaries* (via `claude -p`) that are loaded into
  the system prompt of subsequent sessions. This is "what the engine remembers."
- **`history/*.json`** — *full* raw turns plus metadata, one file per session.
  This is the corpus for future RAG, not for the prompt.

## Why it's like this (the rationale)

- **State-aware routing conserves Opus.** Needs accumulate → push toward the
  expensive `deep` → it discharges them deeply → a long stretch of cheap chat and
  silence. The cycle itself limits the frequency of expensive calls.
- **"Whichever branch answered is what closes the needs."** `deep` is the
  "filling meal" (it closes novelty, rest, intensity), `chat` mostly provides
  connection. This makes the choice of branch meaningful rather than cosmetic.
- **Ticks are cheap by default.** Drift is pure arithmetic; the model isn't
  touched until there's a reason. This lets the engine "live" in the background.
- **External config.** Models, calibration knobs, persona, and prompts are moved
  out into `.env` / `state/`, so behavior is changed without editing code.
